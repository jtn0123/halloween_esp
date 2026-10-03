"""One buyer's machine for tests/install_smoke.py: who they are, where their
Castle Tools lands, and the two programs they start by hand.

`Buyer.load(work)` rebuilds the same person in every phase from the state
`stage` wrote: the fresh-account environment (install_smoke_env.buyer_env),
the per-user folders the installer will choose, the unpacked download and
the staged release. Its methods are the buyer's actions — run the installer
with some flags, double-click the launcher, start the emulated castle — each
logged under <work>/logs for the artifact a failed run uploads.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import install_smoke_env as se  # tools/ on the path, then:

# isort: split
import desktop_env as de
import desktop_launch as dl
import desktop_lifecycle as life
import desktop_release as rel

#: The release this tree pretends to be. A pre-release tag, so nothing that
#: asks GitHub could ever mistake it for a published one.
TAG = "v0.0.0-smoke"
#: GitHub names a source zip's single folder <repo>-<tag without the v>.
TREE_NAME = f"halloween_esp-{TAG.removeprefix('v')}"
#: Where the buyer unpacked the zip: a space and letters outside ASCII, as
#: in a profile named "Zoë" — every path the installer is handed passes
#: through cmd, PowerShell 5.1 and Python on its way.
DOWNLOADS = "Téléchargements de Zoë"


def in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


@dataclass
class Buyer:
    work: Path
    system: str
    env: dict[str, str]
    dirs: de.Dirs
    tree: Path
    release_json: Path

    @classmethod
    def load(cls, work: Path) -> Buyer:
        state = se.read_state(work)
        system = platform.system()
        home = Path(state["home"]) if state.get("home") else None
        if home is None and not in_ci():
            raise SystemExit(
                "outside CI this needs `stage --home DIR`: the install goes into "
                "that profile, never the account running the test"
            )
        env = se.buyer_env(os.environ, system, home)
        return cls(
            work=work,
            system=system,
            env=env,
            dirs=se.install_dirs(env, system),
            tree=Path(state["tree"]),
            release_json=Path(state["release_json"]),
        )

    @property
    def logs(self) -> Path:
        return self.work / "logs"

    @property
    def release_flags(self) -> list[str]:
        """How this test hands the installer its castle-core build: the
        installer's own resolved-release file (the one --update writes),
        naming a staged zip and SHA256SUMS by file:// URL, so the download,
        checksum and unpack code all run with nothing published."""
        return ["--release-json", str(self.release_json)]

    @property
    def shortcut(self) -> Path:
        return life.start_menu_shortcut(
            self.env, se.profile_home(self.env, self.system)
        )

    def installer(self, flags: list[str], log: str) -> tuple[int, str]:
        """The README's command, from where a buyer would be: the unpacked
        installer folder (a double-click) or their home (Terminal)."""
        cwd = (
            self.tree / "installer"
            if self.system == "Windows"
            else se.profile_home(self.env, self.system)
        )
        print(f"\n== installer {' '.join(flags)} ==", flush=True)
        cmd = se.installer_command(self.tree, self.system, flags)
        return se.tee(cmd, self.env, self.logs / f"{log}.log", cwd=cwd)

    def record(self) -> dict[str, Any]:
        return de.read_json(self.dirs.install_file)

    def launch(self, port: int, log: str = "castle-radio") -> se.Child:
        """Double-click the launcher; returns once Castle Radio answers."""
        cmd = se.launcher_command(self.dirs, port)
        child = se.Child(cmd, self.env, self.logs / f"{log}.log", self.dirs.install)
        try:
            se.wait_for(
                lambda: dl.radio_state(port) == "ours",
                120,
                "Castle Radio to answer /radio/tools",
                child.alive,
            )
        except SystemExit:
            print(child.tail())
            child.stop()
            raise
        return child

    def castle(self, port: int, card: Path) -> se.Child:
        """The emulated castle, run from the INSTALLED tree by its Python."""
        cmd = [
            str(self.dirs.python), "-u", str(self.dirs.app / "tools" / "castle_emu.py"),
            str(port), "--dir", str(card), "--variant", "buyer",
        ]  # fmt: skip
        child = se.Child(cmd, self.env, self.logs / "castle-emu.log", self.dirs.app)

        def answers() -> bool:
            try:
                return "version" in se.castle_status(port)
            except (OSError, ValueError):
                return False

        se.wait_for(answers, 60, "the emulated castle to answer", child.alive)
        return child

    def shortcut_target(self) -> str:
        """The Start-menu shortcut's target, read back through the same COM
        object install.ps1 wrote it with."""
        script = (
            "(New-Object -ComObject WScript.Shell).CreateShortcut("
            f"'{self.shortcut}').TargetPath"
        )
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip()

    def keep_evidence(self) -> None:
        """Copy what the installer and the servers wrote into the logs, for
        the artifact a failed run uploads."""
        for src in (
            self.dirs.install_file,
            self.dirs.settings_file,
            self.dirs.data / "catalog.json",
            self.dirs.devices,
        ):
            if src.is_file():
                shutil.copyfile(src, self.logs / f"{src.parent.name}-{src.name}")


def stage_release(tag: str, target: str, bin_dir: Path, out: Path) -> Path:
    """castle-core as a release ships it — release_assets.py's own zip and
    SHA256SUMS — and the resolved-release file naming them by file:// URL.
    Returns that file's path."""
    import release_assets

    shutil.rmtree(out, ignore_errors=True)
    release_assets.zip_core(tag, target, bin_dir, out)
    (out / rel.SUMS).write_text(
        release_assets.sha256sums(out), encoding="utf-8", newline="\n"
    )
    assets = {p.name: p.resolve().as_uri() for p in sorted(out.iterdir())}
    doc = {"tag": tag, "assets": assets, "source_zip": ""}
    path = out.parent / "release.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path
