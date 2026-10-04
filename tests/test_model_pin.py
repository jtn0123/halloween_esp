"""tools/model_pin.py: CI's cached htdemucs weights, and the smokes' use of them.

What is held here, without a network: the cache layout is the one
huggingface_hub and castle_tools_status read, a cache hit downloads
nothing, a wrong or missing file is fetched again and a wrong download is
refused, both workflows restore the entry under the pin's key and export it
before their smoke, and each smoke fails a Demucs that fell back to its
legacy download rather than read the pin. That huggingface_hub (1.27 and
the 2.0.0 requirements-desktop.lock pins) and demucs.hf really read such a
cache offline was checked by hand when the pin was written; CI has neither.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any, cast
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_tools_status as status
import desktop_smoke
import model_pin as pin

WORKFLOWS = ROOT / ".github" / "workflows"
YAML_BODY = b"models: ['abcd1234']\n"
WEIGHTS = b"not really weights"
SMALL = {
    "htdemucs.yaml": (pin.sha256(YAML_BODY), len(YAML_BODY)),
    "abcd1234.safetensors": (pin.sha256(WEIGHTS), len(WEIGHTS)),
}


def served(url: str) -> bytes:
    """What huggingface.co answers for the small pin's files."""
    name = url.rsplit("/", 1)[1]
    assert url == pin.URL.format(repo=pin.REPO, revision=pin.REVISION, name=name)
    return YAML_BODY if name == "htdemucs.yaml" else WEIGHTS


class ThePin(unittest.TestCase):
    def test_the_key_names_the_signature_and_the_weights_hash(self) -> None:
        (weights,) = [n for n in pin.FILES if n.endswith(".safetensors")]
        sig = weights.removesuffix(".safetensors")
        self.assertEqual(pin.CACHE_KEY, f"htdemucs-{sig}-{pin.FILES[weights][0][:12]}")
        self.assertEqual(set(pin.FILES), {"htdemucs.yaml", weights})
        # The signature the third-party notices name is the one pinned here.
        notices = (ROOT / "tools" / "notices_external.py").read_text(encoding="utf-8")
        self.assertIn(f'"signature {sig}"', notices)


class FetchAndVerify(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hub = Path(tmp.name) / "hub"
        patch = mock.patch.object(pin, "FILES", SMALL)
        patch.start()
        self.addCleanup(patch.stop)
        self.asked: list[str] = []
        self.slept: list[float] = []

    def get(self, url: str) -> bytes:
        self.asked.append(url)
        return served(url)

    def main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = pin.main([*argv, str(self.hub)], self.get)
        return code, out.getvalue()

    def test_a_miss_lays_out_the_hubs_own_cache_and_prints_only_the_path(self) -> None:
        code, out = self.main("fetch")
        self.assertEqual(code, 0)
        self.assertEqual(out, f"{self.hub.resolve()}\n")
        repo = self.hub / "models--adefossez--HTDemucs"
        self.assertEqual((repo / "refs" / "main").read_text(), pin.REVISION)
        snap = repo / "snapshots" / pin.REVISION
        self.assertEqual((snap / "abcd1234.safetensors").read_bytes(), WEIGHTS)
        self.assertEqual(sorted(p.name for p in snap.iterdir()), sorted(SMALL))
        self.assertEqual(pin.problems(self.hub), [])

    def test_the_status_probe_reads_the_same_cache(self) -> None:
        self.main("fetch")
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.hub)}):
            self.assertTrue(status._model()["ok"])

    def test_a_hit_downloads_nothing(self) -> None:
        self.main("fetch")
        self.asked.clear()
        self.assertEqual(self.main("fetch")[0], 0)
        self.assertEqual(self.asked, [])

    def test_a_wrong_file_is_refused_by_verify_and_replaced_by_fetch(self) -> None:
        self.main("fetch")
        weights = pin.snapshot(self.hub) / "abcd1234.safetensors"
        weights.write_bytes(b"poisoned")
        self.assertEqual(self.main("verify"), (1, ""))
        self.asked.clear()
        self.assertEqual(self.main("fetch")[0], 0)
        self.assertEqual([u.rsplit("/", 1)[1] for u in self.asked], [weights.name])
        self.assertEqual(weights.read_bytes(), WEIGHTS)

    def test_verify_wants_refs_main_too(self) -> None:
        self.main("fetch")
        (pin.repo_dir(self.hub) / "refs" / "main").write_text("0" * 40)
        self.assertEqual(self.main("verify")[0], 1)

    def test_a_refused_download_is_tried_again_then_fatal(self) -> None:
        answers: list[Any] = [OSError("refused"), b"truncated", WEIGHTS]

        def flaky(_url: str) -> bytes:
            got = answers.pop(0)
            if isinstance(got, Exception):
                raise got
            return cast(bytes, got)

        with contextlib.redirect_stderr(io.StringIO()):
            data = pin.download("abcd1234.safetensors", flaky, self.slept.append)
            self.assertEqual((data, self.slept), (WEIGHTS, [20.0, 40.0]))
            self.slept.clear()
            with self.assertRaisesRegex(pin.PinError, "not the pinned file"):
                pin.download("abcd1234.safetensors", lambda _u: b"x", self.slept.append)
        self.assertEqual(len(self.slept), pin.TRIES - 1)
        self.assertFalse(self.hub.exists(), "nothing is written before it verifies")

    def test_the_weights_never_leave_https(self) -> None:
        handler = pin._HttpsOnly()
        req = urllib.request.Request("https://huggingface.co/x")
        headers = cast(Any, Message())
        cdn = handler.redirect_request(
            req, io.BytesIO(), 302, "Found", headers, "https://cdn/x"
        )
        self.assertIsNotNone(cdn)
        with self.assertRaisesRegex(pin.PinError, "away from https"):
            handler.redirect_request(
                req, io.BytesIO(), 302, "Found", headers, "http://cdn/x"
            )


class TheWorkflows(unittest.TestCase):
    """Each job restores the entry under the pin's key, exports it, and only
    then runs the smoke that would otherwise download the weights."""

    def steps(self, workflow: str, job: str) -> list[dict[str, Any]]:
        text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
        return cast(list[dict[str, Any]], yaml.safe_load(text)["jobs"][job]["steps"])

    def check(self, workflow: str, job: str, smoke: str) -> None:
        steps = self.steps(workflow, job)
        names = [s.get("name") for s in steps]
        cache = steps[names.index("htdemucs weights (cached)")]
        self.assertTrue(cache["uses"].startswith("actions/cache@"))
        self.assertEqual(
            cache["with"], {"path": "~/htdemucs-hub", "key": pin.CACHE_KEY}
        )
        run = steps[names.index("htdemucs weights")]["run"]
        self.assertIn("hub=$(python tools/model_pin.py fetch ~/htdemucs-hub)\n", run)
        self.assertIn('echo "HF_HUB_CACHE=$hub" >> "$GITHUB_ENV"', run)
        self.assertIn('echo "HF_HUB_OFFLINE=1" >> "$GITHUB_ENV"', run)
        self.assertLess(
            names.index("htdemucs weights (cached)"), names.index("htdemucs weights")
        )
        self.assertLess(names.index("htdemucs weights"), names.index(smoke))

    def test_install_smoke_caches_before_the_buyer_is_staged(self) -> None:
        self.check("install-smoke.yml", "buyer", "stage the download")
        text = (WORKFLOWS / "install-smoke.yml").read_text(encoding="utf-8")
        paths = yaml.safe_load(text)[True]["pull_request"]["paths"]
        self.assertIn("tools/model_pin.py", paths)

    def test_the_release_caches_before_the_first_run_smoke(self) -> None:
        self.check("release.yml", "desktop", "first-run smoke")


class TheSmokesReadThePin(unittest.TestCase):
    """Demucs answers a hub it cannot read by downloading the weights from
    its legacy home into TORCH_HOME — every other check would pass."""

    def test_the_desktop_smoke_fails_a_legacy_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for system, env in (
                ("Darwin", {"HOME": tmp}),
                ("Windows", {"LOCALAPPDATA": tmp}),
            ):
                with self.subTest(system):
                    runtime = desktop_smoke.runtime_path(system, env)
                    torch = runtime / "models" / "torch" / "hub" / "checkpoints"
                    torch.mkdir(parents=True)
                    (torch / "955717e8-8726e21a.th").write_bytes(b"")
                    # Not under the cache: nothing to hold the app to.
                    desktop_smoke.read_the_pinned_model(system, env)
                    offline = {**env, "HF_HUB_OFFLINE": "1"}
                    with self.assertRaisesRegex(desktop_smoke.SmokeError, "skipped"):
                        desktop_smoke.read_the_pinned_model(system, offline)
                    (torch / "955717e8-8726e21a.th").unlink()
                    desktop_smoke.read_the_pinned_model(system, offline)

    def test_the_install_smoke_checks_the_same(self) -> None:
        text = (ROOT / "tests" / "install_smoke.py").read_text(encoding="utf-8")
        self.assertIn('if b.env.get("HF_HUB_OFFLINE"):', text)
        self.assertIn('legacy = sorted((b.dirs.models / "torch").rglob("*.th"))', text)


if __name__ == "__main__":
    unittest.main()
