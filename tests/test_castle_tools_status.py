"""Desktop dependency probe tests; no downloads, servers, or hardware."""

import builtins
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import castle_tools_status as tools_status


def _bash() -> str:
    """A POSIX bash for the launcher. On Windows the `bash` CreateProcess
    finds first is System32's WSL stub, which only says no distribution is
    installed. Git for Windows' bash is under its usr/bin — the real one,
    not bin/bash.exe, a launcher that puts Git's own curl ahead of the
    fakes on PATH."""
    if os.name == "nt":
        git = shutil.which("git")
        for up in Path(git).resolve().parents[:3] if git else ():
            if (up / "usr" / "bin" / "bash.exe").is_file():
                return str(up / "usr" / "bin" / "bash.exe")
    return shutil.which("bash") or "bash"


def _fake_checkout(fake: Path) -> Path:
    """The launcher, a venv python and a bin/ for the fakes, under `fake`.
    The interpreter is a one-line script rather than a symlink: Windows
    makes symlinks a privilege, and the launcher only asks that it run."""
    root = Path(__file__).resolve().parents[1]
    (fake / ".venv" / "bin").mkdir(parents=True)
    (fake / "bin").mkdir()
    shutil.copy(root / "Open Castle Studio.command", fake)
    python = fake / ".venv" / "bin" / "python"
    exe = Path(sys.executable).as_posix()
    python.write_text(f"#!/bin/sh\nexec '{exe}' \"$@\"\n", encoding="utf-8")
    python.chmod(0o755)
    return fake / "Open Castle Studio.command"


def _launch(launcher: Path, env: dict[str, str], stdin: str = "") -> Any:
    env["PATH"] = os.pathsep.join([str(launcher.parent / "bin"), env["PATH"]])
    return subprocess.run(
        [_bash(), launcher.as_posix()],
        cwd=launcher.parent,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


class CastleToolsStatusTests(unittest.TestCase):
    def test_status_has_stable_identity_and_capabilities(self) -> None:
        result = tools_status.status()
        self.assertEqual(result["service"], "castle-radio")
        self.assertEqual(result["protocol"], 1)
        self.assertEqual(result["install_command"], "./tools/install_castle_tools.sh")
        capabilities = cast(dict[str, Any], result["capabilities"])
        self.assertIn("separation", capabilities)
        self.assertTrue(result["checks"])

    def test_model_probe_requires_yaml_and_every_weight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            snap = cache / "models--adefossez--HTDemucs" / "snapshots" / "revision"
            snap.mkdir(parents=True)
            (snap / "htdemucs.yaml").write_text(
                "models: [one, two]\n", encoding="utf-8"
            )
            (snap / "one.safetensors").write_bytes(b"weight")
            with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(cache)}):
                self.assertFalse(tools_status._model()["ok"])
                (snap / "two.safetensors").write_bytes(b"weight")
                self.assertTrue(tools_status._model()["ok"])

    def test_optional_failure_does_not_block_core(self) -> None:
        def package(name: str, _module: str, required: bool = True):
            ok = name not in {"demucs", "torch"}
            return {"name": name, "ok": ok, "detail": "test", "required": required}

        good_command = lambda name, required=True, command=None: {  # noqa: E731
            "name": name,
            "ok": True,
            "detail": "test",
            "required": required,
        }
        with (
            mock.patch.object(tools_status, "_package", side_effect=package),
            mock.patch.object(tools_status, "_command", side_effect=good_command),
            mock.patch.object(
                tools_status,
                "_analyzer",
                return_value=good_command("analyze_track"),
            ),
            mock.patch.object(
                tools_status,
                "_model",
                return_value={
                    "name": "htdemucs model",
                    "ok": False,
                    "detail": "missing",
                    "required": False,
                },
            ),
        ):
            tools_status.status.cache_clear()
            result = tools_status.status()
            tools_status.status.cache_clear()
        self.assertFalse(result["ready"])
        self.assertTrue(result["core_ready"])
        capabilities = cast(dict[str, Any], result["capabilities"])
        self.assertTrue(capabilities["importing"])
        self.assertFalse(capabilities["separation"])

    def test_cargo_is_not_needed_once_castle_core_is_built(self) -> None:
        """A release install has prebuilt binaries and no cargo: its card
        must not go amber over the compiler (tests/install_smoke.py)."""
        missing = {"name": "cargo", "ok": False, "detail": "missing", "required": False}
        built = {"name": "analyze_track", "ok": True, "detail": "x", "required": True}
        unbuilt = {**built, "ok": False, "detail": "not built"}
        with mock.patch.object(tools_status, "_command", return_value=missing):
            fine = tools_status._cargo(cast(tools_status.Check, built))
            needed = tools_status._cargo(cast(tools_status.Check, unbuilt))
        self.assertTrue(fine["ok"])
        self.assertIn("not needed", fine["detail"])
        self.assertFalse(fine["required"])
        self.assertFalse(needed["ok"])

    def test_model_probe_handles_missing_yaml_and_malformed_cache(self) -> None:
        real_import = builtins.__import__

        def missing_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("missing")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=missing_yaml):
            self.assertFalse(tools_status._model()["ok"])
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "models--adefossez--HTDemucs" / "snapshots" / "revision"
            snap.mkdir(parents=True)
            (snap / "htdemucs.yaml").write_text(
                "models: [unterminated", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"HF_HUB_CACHE": tmp}):
                self.assertFalse(tools_status._model()["ok"])

    def test_launcher_reuses_only_castle_radio_identity(self) -> None:
        """It opens the castle's page: CASTLE_RADIO_HOST, else the first
        castle tools/hosts.py knows, else the local page — no address of
        its own (docs/PRODUCTION-TODO.md §8, tools/ship_guard.py)."""
        root = Path(__file__).resolve().parents[1]
        base = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "CASTLE_RADIO_HOST",
                "CASTLE_HOST",
                "CASTLE_DEVICES",
                "CASTLE_STUDIO_PORT",
                "CASTLE_STUDIO_NO_BROWSER",
            }
        }
        for name, extra, devices, want in (
            ("named", {"CASTLE_RADIO_HOST": "192.168.1.20"}, None, "192.168.1.20/"),
            ("inventory", {}, "192.168.1.30", "192.168.1.30/"),
            ("no castle", {"CASTLE_HOST": ""}, "192.168.1.30", "127.0.0.1:8871/"),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp)
                launcher = _fake_checkout(fake)
                (fake / "tools").mkdir()
                shutil.copy(root / "tools" / "hosts.py", fake / "tools")
                if devices:
                    (fake / "devices.toml").write_text(
                        f'[porch]\nhost = "{devices}"\n', encoding="utf-8"
                    )
                curl = fake / "bin" / "curl"
                curl.write_text(
                    "#!/bin/sh\nprintf '%s\\n' "
                    '\'{"service":"castle-radio","protocol":1}\'\n',
                    encoding="utf-8",
                )
                opened = fake / "opened"
                opener = fake / "bin" / "open"
                opener.write_text(
                    f"#!/bin/sh\nprintf '%s' \"$1\" > '{opened.as_posix()}'\n",
                    encoding="utf-8",
                )
                curl.chmod(0o755)
                opener.chmod(0o755)
                result = _launch(launcher, {**base, **extra})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("already running", result.stdout)
                self.assertEqual(opened.read_text(encoding="utf-8"), f"http://{want}")

    def test_launcher_refuses_a_different_service_on_its_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            launcher = _fake_checkout(fake)
            curl = fake / "bin" / "curl"
            curl.write_text(
                "#!/bin/sh\nprintf '%s\\n' '{\"service\":\"someone-else\"}'\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            result = _launch(launcher, os.environ.copy(), stdin="\n")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("being used by another app", result.stdout)


if __name__ == "__main__":
    unittest.main()
