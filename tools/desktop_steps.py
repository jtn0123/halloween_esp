"""One installer run's state, its effects, and the two programs it fetches
from someone else — the base tools/desktop_install.py's Installer builds on.

Every effect goes through `step`/`run`, so a dry run is the same code path
with the effects swapped for a printed plan. ffmpeg is required (the
package manager's, PATH's, or the pinned static build, desktop_thirdparty.py);
yt-dlp (ytdlp_update.py) is not: a fetch that fails is said, and the install
finishes without it (`no_ytdlp`).
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import desktop_env as de
import desktop_progress as progress
import desktop_release as rel
import desktop_thirdparty as tp
import ytdlp_update as yu


class Steps:
    """One run: what it was asked, where it puts things, what it found."""

    def __init__(
        self,
        args: argparse.Namespace,
        dirs: de.Dirs,
        fetch: rel.Fetch = rel.http_fetch,
        which: tp.Which = shutil.which,
        machine: str = "",
        say: Callable[[str], None] = print,
    ) -> None:
        self.args = args
        self.dirs = dirs
        self.fetch = fetch
        self.which = which
        self.machine = machine or platform.machine()
        self.say = say
        self.record = de.read_json(dirs.install_file)
        self.found: dict[str, str] = {}
        self.scratch = Path(tempfile.mkdtemp(prefix="castle-install-"))

    # -- effects ---------------------------------------------------------
    def step(self, what: str, fn: Callable[[], object]) -> None:
        self.say(("[dry-run] would " if self.args.dry_run else "") + what)
        if not self.args.dry_run:
            fn()

    def run(
        self, cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> None:
        self.step(
            "run: " + " ".join(cmd),
            lambda: subprocess.run(cmd, check=True, cwd=cwd, env=env),
        )

    # -- ffmpeg / yt-dlp -------------------------------------------------
    def our_ffmpeg(self) -> tuple[str, str] | None:
        """The pinned pair a previous run downloaded into bin/ — never on
        --repair, which re-fetches it."""
        ff, probe = (self.dirs.bin / self.dirs.exe(n) for n in ("ffmpeg", "ffprobe"))
        if ff.is_file() and probe.is_file() and not self.args.repair:
            return str(ff), str(probe)
        return None

    def existing_ffmpeg(self) -> tuple[str, str] | None:
        """Ours from a previous run, else one on PATH, else what the
        package manager can install."""
        have = self.our_ffmpeg() or tp.ffmpeg_on_path(self.which, self.dirs.exe)
        if have:
            return have
        cmd = tp.package_manager_command(self.dirs.system, self.which)
        if not cmd:
            return None

        def landed() -> tuple[str, str] | None:
            """Where the package manager puts it, off this process's PATH:
            this run's install, or an earlier run's (not installed again)."""
            extra = tp.after_package_manager(self.dirs.system, Path.home())
            return tp.ffmpeg_on_path(lambda n: tp.find_in(extra, n), self.dirs.exe)

        if earlier := landed():
            return earlier
        self.run(cmd)
        return tp.ffmpeg_on_path(self.which, self.dirs.exe) or landed()

    def ffmpeg(self) -> None:
        choice = self.args.ffmpeg
        if choice not in ("auto", "download"):
            self.found["ffmpeg"] = choice
            self.found["ffprobe"] = str(
                Path(choice).with_name(self.dirs.exe("ffprobe"))
            )
            return
        # `download` (the desktop app's choice: the pinned build, whatever
        # is on PATH) still keeps the copy an earlier run downloaded.
        have = self.existing_ffmpeg() if choice == "auto" else self.our_ffmpeg()
        if have:
            self.found["ffmpeg"], self.found["ffprobe"] = have
            self.say(f"ffmpeg: {have[0]}")
            return

        def fetch() -> None:
            ff, probe = tp.fetch_pinned_ffmpeg(
                self.dirs.system, self.machine, self.dirs.bin, self.fetch, self.scratch
            )
            self.found["ffmpeg"], self.found["ffprobe"] = ff, probe

        self.step(f"download the pinned static ffmpeg into {self.dirs.bin}", fetch)

    def ytdlp(self) -> None:
        """The managed song downloader in bin/ — ytdlp_update, the code
        Castle Radio's Update the downloader button runs: fetched when
        missing, brought to the latest release on --update, re-fetched on
        --repair, and verified against its release's SHA2-256SUMS each time.
        The one step that may fail without failing the install (no_ytdlp)."""
        mine = self.dirs.bin / self.dirs.exe("yt-dlp")
        if mine.is_file() and not (self.args.repair or self.args.update):
            self.found["ytdlp"] = str(mine)
            return

        def fetch() -> None:
            try:
                got = yu.update(
                    self.dirs.bin,
                    self.fetch,
                    system=self.dirs.system,
                    machine=self.machine,
                    force=self.args.repair,
                )
            except (rel.ReleaseError, OSError) as exc:
                self.no_ytdlp(exc, mine)
                return
            self.found["ytdlp"] = str(got["path"])

        self.step(f"download the standalone yt-dlp into {self.dirs.bin}", fetch)

    def no_ytdlp(self, exc: Exception, mine: Path) -> None:
        """yt-dlp is optional: only links need it. A fetch that failed is
        said and the install goes on, keeping any copy already there —
        without one, Castle Radio's downloader card says "Links need the
        downloader" and its Update the downloader button (the same
        ytdlp_update.update, into the same bin/) fetches it later."""
        detail = getattr(exc, "detail", "")
        why = progress.failure(exc) + (f"; {detail}" if detail else "")
        if mine.is_file():
            self.found["ytdlp"] = str(mine)
            self.say(f"yt-dlp: not downloaded ({why}); kept the copy already there")
            return
        self.say(
            f"yt-dlp: not downloaded ({why}). Everything but importing from a "
            "link works without it; Update the downloader in Castle Radio "
            "fetches it later."
        )
