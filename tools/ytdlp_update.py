"""The song downloader — yt-dlp — as a program the owner updates on purpose.

Websites change under yt-dlp every few weeks and an old copy stops working;
an import then says "The downloader may be out of date — Update the
downloader" (import_reason.DOWNLOADER_OLD), and this is what that button
runs. The copy it manages lives in `exe_paths.downloader_dir()` — per-user
app data, never the signed app bundle or the Python environment — and once
it exists every importer runs it (exe_paths.ytdlp, core/src/portable.rs).

An update is one sequence, all or nothing:

  1. ask GitHub's Releases API for yt-dlp's latest stable release (one call)
     — or, when the API turns this address away (60 unauthenticated calls
     an hour per address, shared by everyone behind one router), ask
     GitHub's website, whose /releases/latest redirects to the same tag;
  2. fetch that SAME release's SHA2-256SUMS and this computer's standalone
     build, and refuse the bytes unless they match;
  3. run the new file once (`--version`): a build that will not start is
     not installed;
  4. swap it in with one rename, and note what was installed in
     `downloader.json` beside it.

A failure at any step leaves the old copy exactly as it was. Nothing here
runs by itself: Castle Radio queues it behind the imports when the owner
presses the button (demo/castle-radio/downloader_routes.py), and the
installer runs it on a fresh install, `--update` and `--repair`. It is never
called in the middle of an import.

yt-dlp is released into the public domain (the Unlicense). Each computer
downloads it from yt-dlp's own GitHub releases; this repo never ships or
redistributes it (THIRD-PARTY-NOTICES.txt, tools/notices_external.py).

Stdlib only, like desktop_release.py: the installer runs this under a bare
Python before the environment it is building exists.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desktop_release as rel
import exe_paths
import import_reason as ir
import portable_fs

API_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
#: The same releases on GitHub's website: `<it>/latest` redirects to
#: `<it>/tag/<tag>`, and each asset is at `<it>/download/<tag>/<name>`.
WEB_RELEASES = "https://github.com/yt-dlp/yt-dlp/releases"
#: The API's "not now, not this address": its hourly limit is a 403 that
#: says "rate limit exceeded", or a 429.
TURNED_AWAY = (403, 429)
SUMS = "SHA2-256SUMS"
RECORD = "downloader.json"

#: Runs `[program, "--version"]` and returns what it printed.
Version = Callable[[str], str]
#: (url) -> the URL it lands on after its redirects.
Resolve = Callable[[str], str]

OFFLINE = (
    "Castle Tools could not reach GitHub to fetch the downloader — check that "
    "this computer is online, then try again."
)
UNREADABLE = "GitHub's answer about the downloader could not be used — try again later."
TURNED_AWAY_SAID = (
    "GitHub is turning away requests from this network for now — try again in an hour."
)
TAMPERED = (
    "The downloaded update did not match its published checksum, so it was "
    "not installed — try again later."
)
BROKEN = (
    "The new downloader would not start, so the old one was kept — try again later."
)
NO_BUILD = (
    "There is no ready-made downloader for this kind of computer — song "
    "files still import, but links need yt-dlp installed by hand."
)
NO_HOME = (
    "Castle Tools has no folder to keep the downloader in — reinstall Castle "
    "Tools to repair it."
)
IN_USE = (
    "The downloader is busy with an import — wait for the import to finish, "
    "then update again."
)


class UpdateError(rel.ReleaseError):
    """str() is the owner's sentence; `detail` is for whoever helps. A
    ReleaseError, so the installer's one "install failed" ending says it
    rather than a traceback."""

    def __init__(self, said: str, detail: str = "") -> None:
        super().__init__(said)
        self.detail = detail


def asset_name(system: str, machine: str) -> str | None:
    """yt-dlp's standalone build for this computer. Windows on ARM runs the
    x64 build under emulation; macOS's is universal."""
    m = machine.lower()
    if system == "Windows":
        return "yt-dlp.exe"
    if system == "Darwin":
        return "yt-dlp_macos"
    if system == "Linux" and m in ("x86_64", "amd64"):
        return "yt-dlp_linux"
    if system == "Linux" and m in ("aarch64", "arm64"):
        return "yt-dlp_linux_aarch64"
    return None


def api_url() -> str:
    """The Releases-API answer to read: GitHub's, unless a test (or a
    mirror) names another with CASTLE_DOWNLOADER_RELEASES."""
    return os.environ.get("CASTLE_DOWNLOADER_RELEASES", "").strip() or API_LATEST


def web_url() -> str | None:
    """The website to ask when the API turns this address away: GitHub's,
    or CASTLE_DOWNLOADER_WEB's — and none for a mirror (or a test) that
    named its own API answer and no website, so a test never reaches out."""
    named = os.environ.get("CASTLE_DOWNLOADER_WEB", "").strip()
    if named:
        return named.rstrip("/")
    return (
        None
        if os.environ.get("CASTLE_DOWNLOADER_RELEASES", "").strip()
        else WEB_RELEASES
    )


def landing(url: str) -> str:
    """Where `url` lands after its redirects — a HEAD, so the page itself is
    never downloaded."""
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": rel.USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return str(resp.geturl())


def latest_from_web(web: str, asset: str, resolve: Resolve) -> rel.Release:
    """The latest release as the website names it: the tag its /latest
    redirects to, and that tag's `asset` and sums at their download URLs."""
    landed = resolve(f"{web}/latest")
    _before, found, tag = landed.split("?", 1)[0].rstrip("/").rpartition("/tag/")
    tag = urllib.parse.unquote(tag)
    if not found or not tag or "/" in tag:
        raise rel.ReleaseError(f"{web}/latest led to {landed}, not a release")
    assets = {name: f"{web}/download/{tag}/{name}" for name in (asset, SUMS)}
    return rel.Release(tag=tag, assets=assets)


def run_version(program: str) -> str:
    """`program --version`'s first line; raises when it will not run."""
    out = subprocess.run(
        [program, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    ).stdout
    lines = out.strip().splitlines()
    if not lines:
        raise subprocess.SubprocessError(f"{program} printed no version")
    return lines[0].strip()


def version_of(program: str | None, run: Version | None = None) -> str | None:
    if not program:
        return None
    try:
        return (run or run_version)(program)
    except (OSError, subprocess.SubprocessError):
        return None


def read_record(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def status(run: Version | None = None) -> dict[str, Any]:
    """What an import would run right now, and whether it is the managed
    copy this module can update. Local only: no network."""
    home = exe_paths.downloader_dir()
    program = exe_paths.ytdlp()
    managed = exe_paths.managed_ytdlp()
    record = read_record(home) if home else {}
    return {
        "installed": program is not None,
        "path": program,
        "managed": program is not None and program == managed,
        "version": version_of(program, run),
        "updated_at": record.get("installed_at") if managed else None,
        "home": str(home) if home else None,
    }


def _latest(fetch: rel.Fetch, asset: str, resolve: Resolve) -> rel.Release:
    try:
        body = fetch(api_url())
    except urllib.error.HTTPError as exc:
        web = web_url()
        if exc.code not in TURNED_AWAY or web is None:
            raise UpdateError(OFFLINE, f"{api_url()}: {exc}") from exc
        return _from_web(web, asset, resolve, f"{api_url()}: {exc}")
    except OSError as exc:
        raise UpdateError(OFFLINE, f"{api_url()}: {exc}") from exc
    try:
        return rel.release_from_api(body)
    except rel.ReleaseError as exc:
        raise UpdateError(UNREADABLE, str(exc)) from exc


def _from_web(web: str, asset: str, resolve: Resolve, why: str) -> rel.Release:
    """The API said no; the website, outside the API's limit, names the
    same release. Turned away there too is GitHub's word, not the network's."""
    try:
        return latest_from_web(web, asset, resolve)
    except urllib.error.HTTPError as exc:
        said = TURNED_AWAY_SAID if exc.code in TURNED_AWAY else UNREADABLE
        raise UpdateError(said, f"{why}; {web}/latest: {exc}") from exc
    except OSError as exc:
        raise UpdateError(OFFLINE, f"{why}; {web}/latest: {exc}") from exc
    except rel.ReleaseError as exc:
        raise UpdateError(UNREADABLE, f"{why}; {exc}") from exc


def _sums(release: rel.Release, asset: str, fetch: rel.Fetch) -> str:
    """The published sha256 of `asset`, from the same release's sums."""
    if asset not in release.assets or SUMS not in release.assets:
        detail = f"release {release.tag} lists no {asset} or no {SUMS}"
        raise UpdateError(UNREADABLE, detail)
    try:
        sums = rel.parse_sums(fetch(release.assets[SUMS]).decode("utf-8"))
    except OSError as exc:
        raise UpdateError(OFFLINE, f"{SUMS}: {exc}") from exc
    except (rel.ReleaseError, UnicodeDecodeError) as exc:
        raise UpdateError(UNREADABLE, f"{SUMS}: {exc}") from exc
    if asset not in sums:
        raise UpdateError(UNREADABLE, f"{SUMS} of {release.tag} does not list {asset}")
    return sums[asset]


def _saved(exc: OSError, what: str) -> UpdateError:
    said = ir.for_os_error(exc) or NO_HOME
    return UpdateError(said, f"{what}: {exc}")


def _fetch_checked(url: str, part: Path, digest: str, fetch: rel.Fetch) -> None:
    try:
        body = fetch(url)
    except OSError as exc:
        raise UpdateError(OFFLINE, f"{url}: {exc}") from exc
    try:
        part.write_bytes(body)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise _saved(exc, str(part)) from exc
    try:
        rel.verify(part, digest)  # deletes the part when it does not match
    except rel.ReleaseError as exc:
        raise UpdateError(TAMPERED, str(exc)) from exc


def _swap_in(part: Path, target: Path, run: Version, what: str) -> str:
    """Run the verified `part` once, then rename it over `target`; the
    version it reported. On any failure the part goes and the old copy
    stays."""
    try:
        version = run(str(part))
    except (OSError, subprocess.SubprocessError) as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(BROKEN, f"{what}: {exc}") from exc
    try:
        portable_fs.replace(part, target)
    except OSError as exc:
        part.unlink(missing_ok=True)
        # The part was written beside the target, so the folder takes
        # writes: refused here, the old copy is running (Windows locks it).
        busy = isinstance(exc, PermissionError)
        said = IN_USE if busy else ir.for_os_error(exc) or IN_USE
        raise UpdateError(said, f"{target}: {exc}") from exc
    return version


def _note(home: Path, record: dict[str, object]) -> None:
    """What was installed, for whoever helps; the update stands without it."""
    try:
        (home / RECORD).write_text(
            json.dumps(record, indent=1) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def update(
    home: Path,
    fetch: rel.Fetch | None = None,
    run: Version | None = None,
    system: str = "",
    machine: str = "",
    force: bool = False,
    now: Callable[[], float] = time.time,
    resolve: Resolve | None = None,
) -> dict[str, Any]:
    """Make `home`'s yt-dlp the latest release's, verified. Returns what
    is there now, and whether this call changed it. `force` (the
    installer's --repair) re-fetches even when the version already matches."""
    fetch, run = fetch or rel.http_fetch, run or run_version
    system, machine = system or platform.system(), machine or platform.machine()
    asset = asset_name(system, machine)
    if asset is None:
        raise UpdateError(NO_BUILD, f"no standalone yt-dlp for {system}/{machine}")
    target = home / ("yt-dlp.exe" if system == "Windows" else "yt-dlp")
    release = _latest(fetch, asset, resolve or landing)
    have = version_of(str(target), run) if target.is_file() else None
    if have == release.tag and not force:
        return {"changed": False, "version": have, "path": str(target)}
    digest = _sums(release, asset, fetch)
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _saved(exc, str(home)) from exc
    # `.download.exe` on Windows, so the trial run below can start it.
    part = target.with_name(f"{target.stem}.download{target.suffix}")
    _fetch_checked(release.assets[asset], part, digest, fetch)
    if system != "Windows":
        part.chmod(0o755)
    version = _swap_in(part, target, run, f"{asset} {release.tag}")
    record: dict[str, object] = {"tag": release.tag, "asset": asset}
    record.update(sha256=digest, installed_at=round(now()))
    _note(home, record)
    return {"changed": True, "version": version, "path": str(target)}


def main(argv: list[str] | None = None) -> int:
    """`ytdlp_update.py [status|update]` — for whoever helps an owner."""
    ap = argparse.ArgumentParser(description="Show or update the managed yt-dlp.")
    ap.add_argument("action", nargs="?", choices=("status", "update"), default="status")
    ap.add_argument("--force", action="store_true", help="re-fetch the same version")
    args = ap.parse_args(argv)
    if args.action == "status":
        print(json.dumps(status(), indent=1))
        return 0
    home = exe_paths.downloader_dir()
    if home is None:
        print(NO_HOME, file=sys.stderr)
        return 1
    try:
        got = update(home, force=args.force)
    except UpdateError as exc:
        print(exc, file=sys.stderr)
        print("    " + exc.detail, file=sys.stderr)
        return 1
    print(json.dumps(got, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
