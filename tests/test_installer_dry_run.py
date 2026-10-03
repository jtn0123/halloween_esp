"""The bootstrap scripts' --dry-run on a machine that has uv and not yet its
Python 3.13 — a developer's laptop, or a buyer who installed uv for
something else. Both scripts used to stop there with uv's own error and exit
1 (tests/install_smoke.py found it); a dry run now says what it would do and
exits 0, having asked uv where Python is and installed nothing.

The uv here is a stand-in on PATH that logs what it is asked and finds no
Python: install.sh on POSIX, install.cmd -> install.ps1 on Windows, each in
a fresh-account environment (install_smoke_env.buyer_env) so nothing of the
machine running the test is reached."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_smoke_env as se


def fake_uv(bin_dir: Path, log: Path, system: str) -> None:
    """A uv that writes its arguments to `log` and fails every command."""
    if system == "Windows":
        script = f'@echo %* >> "{log}"\r\n@exit /b 2\r\n'
        (bin_dir / "uv.cmd").write_text(script, encoding="ascii")
        return
    uv = bin_dir / "uv"
    uv.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 2\n', encoding="utf-8")
    uv.chmod(0o755)


class ADryRunBeforePython(unittest.TestCase):
    def test_says_what_it_would_do_and_installs_nothing(self) -> None:
        system = platform.system()
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        home, bin_dir, log = tmp / "home", tmp / "bin", tmp / "uv.log"
        home.mkdir()
        bin_dir.mkdir()
        fake_uv(bin_dir, log, system)
        env = se.buyer_env(os.environ, system, home)
        env["PATH"] = os.pathsep.join([str(bin_dir), env["PATH"]])
        out = subprocess.run(
            se.installer_command(se.ROOT, system, ["--dry-run"]),
            env=env,
            cwd=home,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        said = out.stdout + out.stderr
        self.assertEqual(out.returncode, 0, said)
        self.assertIn("[dry-run] would run:", said)
        self.assertIn("python install 3.13", said)
        self.assertIn("[dry-run] would continue under that Python 3.13", said)
        asked = log.read_text(encoding="utf-8", errors="replace").splitlines()
        self.assertTrue(any("python find" in line for line in asked), asked)
        self.assertFalse(any("python install" in line for line in asked), asked)
        dirs = se.install_dirs(env, system)
        for made in (dirs.install, dirs.data, se.uv_path(env, system)):
            self.assertFalse(made.exists(), f"a dry run made {made}")


if __name__ == "__main__":
    unittest.main()
