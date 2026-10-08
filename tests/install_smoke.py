"""Install Castle Tools as a buyer would, then use it — the CI half of the
Windows hands-on pass (docs/PRODUCTION-TODO.md §4.4).

    python tests/install_smoke.py stage --work DIR --core-bins DIR [--home DIR]
    python tests/install_smoke.py install --work DIR
    python tests/install_smoke.py session --work DIR
    python tests/install_smoke.py uninstall --work DIR

.github/workflows/install-smoke.yml runs the four in order on windows-latest
and macos-15. Each phase is a step of its own, so the Actions page says
which one went red; they share <work>/smoke.json.

  stage      the release a buyer downloads: `git archive` of this commit
             (export-ignore and export-subst honoured, GitHub's
             <repo>-<version>/ folder) unpacked into a folder with a space
             and non-ASCII letters, every file stamped as downloaded
             (Mark of the Web / quarantine); and castle-core zipped by
             release_assets.py from a build made beforehand, with its
             SHA256SUMS, behind the installer's resolved-release file.
  install    on the account's own environment (no cargo, no Homebrew, no
             Python, no uv on PATH): a dry run on the fresh machine, the
             real installer, a dry run over the install, and a second run
             — the dry runs change no file, the second run fetches nothing.
  session    the installed launcher (Castle Radio) and the installed
             tools/castle_emu.py: Find my castle with a typed address, three
             songs made by the installed ffmpeg and imported through the
             page's upload (one with the voice split — Demucs is timed),
             sent to the castle, checked on its card, and played.
  uninstall  --uninstall keeps the songs (a reinstall finds them in Castle
             Radio); --uninstall --purge removes them.

Outside CI, `stage` needs --home: every later phase then runs as a person
whose home is that folder, so nothing lands in the account running it.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_smoke_env as se  # tools/ on the path, then:

# isort: split
import desktop_env as de
import desktop_release as rel
import install_smoke_buyer as sb
import install_smoke_radio as radio
import install_smoke_session as session
from install_smoke_buyer import TAG, Buyer
from install_smoke_env import check


# -- stage -------------------------------------------------------------------
def stage(work: Path, core_bins: Path, home: Path | None, ref: str) -> None:
    if home is None and not sb.in_ci():
        raise SystemExit("outside CI, pass --home DIR (a throwaway profile)")
    system = platform.system()
    work.mkdir(parents=True, exist_ok=True)
    download = work / "download" / f"{sb.TREE_NAME}.zip"
    download.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "archive", "--format=zip", f"--prefix={sb.TREE_NAME}/",
         "-o", str(download), ref],
        cwd=se.ROOT,
        check=True,
    )  # fmt: skip
    unpacked = work / sb.DOWNLOADS
    shutil.rmtree(unpacked, ignore_errors=True)
    rel.safe_extract(download, unpacked)
    tree = rel.single_root(unpacked)
    check(tree.name == sb.TREE_NAME, f"the zip unpacks to one folder, {tree.name}")
    marked = se.mark_downloaded(tree, system)
    print(f"stamped {marked} files as downloaded")
    target = rel.rust_target(system, platform.machine())
    if target is None:
        raise SystemExit(f"no release castle-core target for {system}")
    release_json = sb.stage_release(TAG, target, core_bins, work / "release")
    if home is not None:
        home.mkdir(parents=True, exist_ok=True)
    se.write_state(
        work,
        {
            "tree": str(tree),
            "release_json": str(release_json),
            "home": str(home) if home else None,
        },
    )
    print(f"staged {tree}\n  castle-core {target} via {release_json}")


# -- install -----------------------------------------------------------------
def fresh_dry_run(b: Buyer) -> None:
    uv = se.uv_path(b.env, b.system)
    had_uv = uv.exists()
    print(f"uv before the install: {'present' if had_uv else 'absent'} ({uv})")
    before = {d: se.snapshot(d) for d in (b.dirs.install, b.dirs.data)}
    code, _said = b.installer(["--dry-run"], "install-dry-run-fresh")
    check(code == 0, "a dry run on a fresh machine exits 0")
    after = {d: se.snapshot(d) for d in (b.dirs.install, b.dirs.data)}
    check(before == after, "a dry run on a fresh machine creates nothing")
    check(uv.exists() == had_uv, "a dry run does not install uv")


def verify_installed(b: Buyer, said: str) -> None:
    rec = b.record()
    check(rec.get("install") == str(b.dirs.install), f"installed in {b.dirs.install}")
    check(rec.get("data") == str(b.dirs.data), f"data kept in {b.dirs.data}")
    check(
        rec.get("core") == "release" and rec.get("tag") == TAG,
        f"castle-core came from the staged {TAG} release zip",
    )
    release = b.dirs.app / "core" / "target" / "release"
    for name in rel.CORE_BINS:
        check((release / b.dirs.exe(name)).is_file(), f"{b.dirs.exe(name)} placed")
    for key in ("python", "ffmpeg", "ffprobe", "ytdlp"):
        check(Path(str(rec.get(key, ""))).is_file(), f"install.json's {key} exists")
    print(f"ffmpeg chosen: {rec['ffmpeg']}")
    check(se.uv_path(b.env, b.system).is_file(), "uv installed in ~/.local/bin")
    name = "Castle Tools.cmd" if b.system == "Windows" else "Castle Tools.command"
    launcher = b.dirs.install / name
    check(launcher.is_file(), f"the launcher {name} is placed")
    check(not se.is_marked(launcher, b.system), "the launcher is not download-marked")
    check(not se.is_marked(b.dirs.app / "tools" / "desktop_launch.py", b.system),
          "the installed app is not download-marked")  # fmt: skip
    check(b.dirs.scenes.is_file(), "the shipped show is seeded into the data dir")
    link = b.dirs.app / de.RADIO_DATA
    same = Path(os.path.realpath(link)) == Path(os.path.realpath(b.dirs.data))
    check(de.is_link(link) and same, "Castle Radio's data folder links to the data dir")
    for personal in ("devices.toml", "tracks/tracks.json"):
        check(
            not (b.dirs.app / personal).exists(),
            f"the seller's {personal} did not ship",
        )
    if b.system == "Windows":
        check(b.shortcut.is_file(), "the Start-menu shortcut exists")
        target = b.shortcut_target()
        check(Path(target) == launcher, f"the shortcut opens the launcher ({target})")
    check("Castle Tools are installed." in said, "the installer says it is done")
    if b.env.get("HF_HUB_OFFLINE"):
        # CI's cached weights (tools/model_pin.py): a Demucs that cannot read
        # them falls back to its legacy download, and nothing else would see.
        legacy = sorted((b.dirs.models / "torch").rglob("*.th"))
        check(not legacy, f"Demucs read the cached htdemucs weights {legacy[:1]}")


def install(work: Path) -> None:
    b = Buyer.load(work)
    try:
        fresh_dry_run(b)
        code, said = b.installer(b.release_flags, "install")
        check(code == 0, "the installer exits 0")
        verify_installed(b, said)

        roots = (b.dirs.install, b.dirs.data)
        before = {d: se.snapshot(d) for d in roots}
        code, said = b.installer(["--dry-run"], "install-dry-run")
        check(
            code == 0 and "dry run: nothing changed" in said,
            "a dry run over the install",
        )
        changed = [line for d in roots for line in se.diff(before[d], se.snapshot(d))]
        check(not changed, f"the dry run changed no file {changed[:5]}")

        again(b)
    finally:
        b.keep_evidence()


def again(b: Buyer) -> None:
    """A second run resumes, finds everything in place, and fetches nothing."""
    skip = frozenset({"__pycache__"})
    kept = {
        "bin": se.snapshot(b.dirs.bin),
        "core": se.snapshot(b.dirs.app / "core" / "target" / "release"),
        "models": se.snapshot(b.dirs.models),
        "data": se.snapshot(b.dirs.data),
    }
    files = set(se.snapshot(b.dirs.app, skip))
    record = b.record()
    code, said = b.installer(b.release_flags, "install-again")
    check(code == 0, "a second run exits 0")
    check(
        f"castle-core: {TAG} already installed" in said, "castle-core is not re-fetched"
    )
    again_ff = ("run: winget", "run: brew")
    managers = [line for line in said.splitlines() if line.startswith(again_ff)]
    check(not managers, f"ffmpeg is not installed again {managers}")
    now = {
        "bin": se.snapshot(b.dirs.bin),
        "core": se.snapshot(b.dirs.app / "core" / "target" / "release"),
        "models": se.snapshot(b.dirs.models),
        "data": se.snapshot(b.dirs.data),
    }
    for part in kept:
        changes = se.diff(kept[part], now[part])
        check(not changes, f"a second run leaves {part} as it was {changes[:5]}")
    check(
        set(se.snapshot(b.dirs.app, skip)) == files, "the app tree has the same files"
    )
    record.pop("installed", None)
    second = b.record()
    second.pop("installed", None)
    check(record == second, "install.json records the same install")


# -- uninstall -----------------------------------------------------------------
def uninstall(work: Path) -> None:
    b = Buyer.load(work)
    try:
        songs = se.snapshot(b.dirs.tracks)
        catalog = b.dirs.data / "catalog.json"
        rows = json.loads(catalog.read_text(encoding="utf-8"))
        check(len(rows) == len(radio.SONGS), f"{len(rows)} songs before the uninstall")
        code, _ = b.installer(["--uninstall"], "uninstall")
        check(code == 0, "--uninstall exits 0")
        check(not b.dirs.install.exists(), "--uninstall removes the app")
        if b.system == "Windows":
            check(
                not b.shortcut.exists(), "--uninstall removes the Start-menu shortcut"
            )
        check(se.snapshot(b.dirs.tracks) == songs, "--uninstall keeps every song file")
        check(catalog.is_file(), "--uninstall keeps Castle Radio's catalog")

        code, _ = b.installer([*b.release_flags, "--skip-model"], "reinstall")
        check(code == 0, "a reinstall over kept songs exits 0")
        session.library_after_reinstall(b, rows)

        code, _ = b.installer(["--uninstall", "--purge"], "uninstall-purge")
        check(code == 0, "--uninstall --purge exits 0")
        check(not b.dirs.install.exists(), "--uninstall --purge removes the app")
        check(not b.dirs.data.exists(), "--uninstall --purge removes the songs")
    finally:
        b.keep_evidence()


def utf8_output() -> None:
    """The job log reads UTF-8; Windows' Python on a pipe writes the ANSI
    code page and dies on the first character outside it — winget's
    progress bar, relayed by tee."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("phase", choices=("stage", "install", "session", "uninstall"))
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--core-bins", type=Path, help="stage: castle-core's release dir")
    ap.add_argument("--home", type=Path, help="stage: a throwaway home (local runs)")
    ap.add_argument("--ref", default="HEAD", help="stage: what to git archive")
    args = ap.parse_args(argv)
    work = args.work.resolve()
    if args.phase == "stage":
        if args.core_bins is None:
            ap.error("stage needs --core-bins")
        home = args.home.resolve() if args.home else None
        stage(work, args.core_bins.resolve(), home, args.ref)
    elif args.phase == "install":
        install(work)
    elif args.phase == "session":
        session.run(Buyer.load(work))
    else:
        uninstall(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
