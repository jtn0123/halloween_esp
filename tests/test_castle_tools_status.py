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
            (snap / "htdemucs.yaml").write_text("models: [one, two]\n")
            (snap / "one.safetensors").write_bytes(b"weight")
            with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(cache)}):
                self.assertFalse(tools_status._model()["ok"])
                (snap / "two.safetensors").write_bytes(b"weight")
                self.assertTrue(tools_status._model()["ok"])

    def test_optional_failure_does_not_block_core(self) -> None:
        def package(name: str, _module: str, required: bool = True):
            ok = name not in {"demucs", "torch"}
            return {"name": name, "ok": ok, "detail": "test", "required": required}

        good_command = lambda name, required=True: {  # noqa: E731
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
            (snap / "htdemucs.yaml").write_text("models: [unterminated")
            with mock.patch.dict(os.environ, {"HF_HUB_CACHE": tmp}):
                self.assertFalse(tools_status._model()["ok"])

    def test_launcher_reuses_only_castle_radio_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            (fake / ".venv" / "bin").mkdir(parents=True)
            (fake / "bin").mkdir()
            shutil.copy(root / "Open Castle Studio.command", fake)
            os.symlink(sys.executable, fake / ".venv" / "bin" / "python")
            curl = fake / "bin" / "curl"
            curl.write_text(
                "#!/bin/sh\nprintf '%s\\n' "
                '\'{"service":"castle-radio","protocol":1}\'\n'
            )
            opened = fake / "opened"
            opener = fake / "bin" / "open"
            opener.write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > '{opened}'\n")
            curl.chmod(0o755)
            opener.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake / 'bin'}:{env['PATH']}"
            result = subprocess.run(
                ["bash", str(fake / "Open Castle Studio.command")],
                cwd=fake,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("already running", result.stdout)
            self.assertEqual(opened.read_text(), "http://10.27.27.81/")

    def test_launcher_refuses_a_different_service_on_its_port(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            (fake / ".venv" / "bin").mkdir(parents=True)
            (fake / "bin").mkdir()
            shutil.copy(root / "Open Castle Studio.command", fake)
            os.symlink(sys.executable, fake / ".venv" / "bin" / "python")
            curl = fake / "bin" / "curl"
            curl.write_text(
                "#!/bin/sh\nprintf '%s\\n' '{\"service\":\"someone-else\"}'\n"
            )
            curl.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake / 'bin'}:{env['PATH']}"
            result = subprocess.run(
                ["bash", str(fake / "Open Castle Studio.command")],
                cwd=fake,
                env=env,
                input="\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("being used by another app", result.stdout)


if __name__ == "__main__":
    unittest.main()
