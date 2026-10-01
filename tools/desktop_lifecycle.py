"""--update and --uninstall for the desktop installer (desktop_install.py).

Update: ask GitHub ONCE for the latest stable release. Not newer than what
install.json records → re-run this tree's own installer (idempotent: it
only refreshes yt-dlp). Newer → download that release's source zip, unpack
it, and run ITS installer over this install with the release already
resolved (`--release-json`), so the new code installs the new code and the
API is not asked a second time.

Uninstall: removes the install root and the Start-menu shortcut. The data
root — tracks, scenes, settings — stays unless `--purge`. Both refuse a
path that could be someone's home or a filesystem root, whatever an
override said.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import desktop_env as de
import desktop_install as di
import desktop_release as rel

START_MENU_LINK = "Castle Tools.lnk"


def start_menu_shortcut(environ: dict[str, str], home: Path) -> Path:
    """Where install.ps1 puts the Start-menu shortcut (per-user, no admin)."""
    appdata = Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
    return (
        appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / START_MENU_LINK
    )


def removable(path: Path, home: Path) -> str | None:
    """Why `path` must not be deleted wholesale, or None when it may be."""
    p = Path(os.path.abspath(path))
    h = Path(os.path.abspath(home))
    if p.parent == p:
        return f"{p} is a filesystem root"
    if p == h or p in h.parents:
        return f"{p} is your home folder or contains it"
    return None


def _rmtree(path: Path) -> None:
    """rmtree that clears Windows' read-only bit instead of failing on it."""

    def retry(func: Callable[..., object], name: str, _exc: BaseException) -> None:
        os.chmod(name, stat.S_IWRITE)
        func(name)

    shutil.rmtree(path, onexc=retry)


def uninstall(
    dirs: de.Dirs,
    purge: bool,
    dry_run: bool,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    say: Callable[[str], None] = print,
) -> int:
    env = dict(os.environ if environ is None else environ)
    home = home or Path.home()
    targets = [dirs.install] + ([dirs.data] if purge else [])
    for t in targets:
        why = removable(t, home)
        if why:
            say(f"refusing to uninstall: {why}")
            return 1
    if dirs.install.exists() and not dirs.install_file.is_file():
        say(
            f"refusing to uninstall: {dirs.install} has no {de.INSTALL_FILE} — not ours"
        )
        return 1
    if dirs.data == dirs.install or dirs.data in dirs.install.parents:
        say(f"refusing to uninstall: the data dir {dirs.data} contains the install")
        return 1
    verb = "[dry-run] would remove" if dry_run else "removing"
    link = dirs.app / de.RADIO_DATA
    if de.is_link(link):
        say(f"{verb} the library link {link}")
        if not dry_run:
            de.remove_link(link)  # before rmtree, which must never reach the data
    shortcut = start_menu_shortcut(env, home)
    if dirs.system == "Windows" and shortcut.exists():
        say(f"{verb} {shortcut}")
        if not dry_run:
            shortcut.unlink()
    for t in targets:
        if t.exists():
            say(f"{verb} {t}")
            if not dry_run:
                _rmtree(t)
    if not purge:
        say(f"kept your songs and show in {dirs.data} (--purge removes them)")
    say(
        "uv and its Python stay installed; they are shared with anything else that uses uv."
    )
    return 0


def passthrough(args: argparse.Namespace) -> list[str]:
    """The flags a re-run installer should inherit from this one."""
    out = [
        "--" + flag.replace("_", "-")
        for flag in ("repair", "from_source", "skip_model", "dry_run")
        if getattr(args, flag)
    ]
    if args.castle_host is not None:
        out += ["--castle-host", args.castle_host]
    if args.ffmpeg != "auto":
        out += ["--ffmpeg", args.ffmpeg]
    return out


def update(
    args: argparse.Namespace,
    dirs: de.Dirs,
    fetch: rel.Fetch = rel.http_fetch,
    run: Callable[[list[str]], int] = lambda cmd: (
        subprocess.run(cmd, check=False).returncode
    ),
    say: Callable[[str], None] = print,
) -> int:
    record = de.read_json(dirs.install_file)
    if not record:
        say(
            f"nothing installed in {dirs.install} — run the installer without --update first"
        )
        return 1
    installed = str(record.get("tag") or "")
    latest = rel.find_release(fetch)  # the run's one API call
    scratch = Path(tempfile.mkdtemp(prefix="castle-update-"))
    resolved = scratch / "release.json"
    resolved.write_text(
        json.dumps(
            {
                "tag": latest.tag,
                "assets": dict(latest.assets),
                "source_zip": latest.source_zip,
            }
        ),
        encoding="utf-8",
    )
    common = [
        "--prefix", str(dirs.install), "--data-dir", str(dirs.data),
        "--uv", str(args.uv), "--release-json", str(resolved), "--update",
    ]  # fmt: skip
    if not rel.is_newer(latest.tag, installed):
        say(
            f"Castle Tools {installed or '(unknown)'} is up to date (latest: {latest.tag})"
        )
        src = dirs.app
    else:
        say(f"updating Castle Tools {installed or '(unknown)'} -> {latest.tag}")
        if args.dry_run:
            say(f"[dry-run] would download {latest.source_zip}")
            return 0
        archive = rel.download(latest.source_zip, scratch / "source.zip", fetch)
        unpacked = scratch / "source"
        rel.safe_extract(archive, unpacked)
        src = rel.single_root(unpacked)
    script = src / "tools" / Path(di.__file__).name
    cmd = [
        sys.executable,
        str(script),
        "--source",
        str(src),
        *common,
        *passthrough(args),
    ]
    return run(cmd)
