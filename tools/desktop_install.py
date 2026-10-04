#!/usr/bin/env python3
"""The desktop installer proper — what installer/install.sh and install.ps1 run,
and what the desktop app runs on its first launch (desktop/README.md).

The shell scripts do only what Python cannot do for itself: install uv,
have uv install a 3.13, and hand over to this file under that interpreter.
Everything after is here, once, for both platforms:

  1. stage the source tree (a release zip or a clone) into <install>/app
  2. a uv environment from requirements-desktop.lock, --require-hashes
  3. castle-core's binaries from the matching GitHub Release, sha256-checked
     (or `--from-source`: cargo build, when cargo is present; or
     `--core-from DIR`: the copies the desktop app carries)
  4. ffmpeg (desktop_thirdparty.py), and the managed yt-dlp (ytdlp_update.py),
     the one fetch whose failure the install survives: only links need it
     (desktop_steps.py)
  5. the htdemucs weights, downloaded once into <install>/models
  6. the user's data dir: tracks, a seeded scenes.yaml, settings.json
  7. the launcher, and install.json recording all of the above

Re-running is safe and cheap: every step checks before it acts. `--repair`
redoes the steps that check (env reinstall, tools re-fetched). `--update`
asks GitHub once for the latest release and, when it is newer, re-runs the
NEW release's installer over this install (desktop_lifecycle.py).
`--uninstall` removes <install> and keeps the data dir unless `--purge`.
`--dry-run` prints the plan and changes nothing. `--progress` adds the
lines the desktop app's splash page reads (desktop_progress.py).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_env as de
import desktop_progress as progress
import desktop_release as rel
import desktop_tree as dt
import release_channel as channel
from desktop_steps import Steps

LAUNCHERS = {"Windows": "Castle Tools.cmd"}
LAUNCHER_DEFAULT = "Castle Tools.command"
VERSION_FILE = Path("installer") / "VERSION"
MODEL_SCRIPT = (
    "from demucs.pretrained import get_model; get_model('htdemucs'); "
    "print('htdemucs is ready')"
)


class Installer(Steps):
    """One install, repair or update: the eight steps in order (`install`),
    on the effects and tools of desktop_steps.Steps."""

    def phase(self, n: int) -> None:
        """Step `n` begins — a line for the desktop app (desktop_progress.py)."""
        if self.args.progress:
            self.say(progress.step_line(n))

    # -- the source tree -------------------------------------------------
    def stage_app(self, src: Path) -> None:
        """Copy `src`'s file list (desktop_tree.py) into <install>/app."""
        app = self.dirs.app
        if src.resolve() == app.resolve():
            self.say("app: running from the installed tree, nothing to copy")
            return
        self.step(f"copy {src} -> {app}", lambda: dt.stage(src, app, self.which))

    # -- python ----------------------------------------------------------
    def python_env(self) -> None:
        uv, py = self.args.uv, self.dirs.python
        if not py.exists() or self.args.repair:
            self.run(
                [
                    uv,
                    "venv",
                    "--managed-python",
                    "--python",
                    "3.13",
                    "--clear",
                    str(self.dirs.env),
                ]
            )
        lock = self.dirs.app / "requirements-desktop.lock"
        sync = [uv, "pip", "sync", "--python", str(py), "--require-hashes", str(lock)]
        self.run(sync + (["--reinstall"] if self.args.repair else []))

    # -- castle-core -----------------------------------------------------
    def release_tag(self, src: Path) -> str | None:
        """The tag this tree IS: --tag, else the VERSION file a release zip
        carries (git archive's export-subst fills it), else `git describe`
        in a clone. None means "unknown — ask GitHub for the latest"."""
        if self.args.tag:
            return str(self.args.tag)
        version = src / VERSION_FILE
        if version.is_file():
            text = version.read_text(encoding="utf-8")
            for word in (
                text.replace(",", " ").replace("(", " ").replace(")", " ").split()
            ):
                if channel.version_key(word.removeprefix("tag:")):
                    return word.removeprefix("tag:")
        git = self.which("git")
        if (src / ".git").exists() and git:
            out = subprocess.run(
                [git, "-C", str(src), "describe", "--tags", "--exact-match"],
                check=False,
                capture_output=True,
                text=True,
            )
            if out.returncode == 0 and channel.version_key(out.stdout.strip()):
                return out.stdout.strip()
        return None

    def release(self, src: Path) -> rel.Release:
        """The release to take binaries from — one API call per run, or
        none when the parent (an --update) already asked."""
        if self.args.release_json:
            data = json.loads(Path(self.args.release_json).read_text(encoding="utf-8"))
            return rel.Release(data["tag"], data["assets"], data.get("source_zip", ""))
        return rel.find_release(self.fetch, self.release_tag(src))

    def core_bins(self, src: Path) -> str:
        if self.args.core_from:
            return self.bundled_core(Path(self.args.core_from))
        target = rel.rust_target(self.dirs.system, self.machine)
        if self.args.from_source or target is None:
            cargo = self.which("cargo")
            if not cargo:
                raise rel.ReleaseError(
                    "castle-core: no release build for this machine and no cargo "
                    "to build one — install Rust (https://rustup.rs) and re-run"
                )
            bins = [a for b in rel.CORE_BINS for a in ("--bin", b)]
            self.run([cargo, "build", "--release", *bins], cwd=self.dirs.app / "core")
            return "source"
        if self.args.dry_run:
            self.say(
                f"[dry-run] would fetch {rel.core_asset(target, '<tag>')}, check SHA256SUMS"
            )
            return "release"
        found = self.release(src)
        if self.have_core(found.tag):
            self.say(f"castle-core: {found.tag} already installed")
            self.found["tag"] = found.tag
            return "release"
        asset = rel.core_asset(target, found.tag)
        self.say(f"castle-core: {asset}")
        archive = rel.fetch_verified_asset(found, asset, self.scratch, self.fetch)
        files = rel.safe_extract(archive, self.scratch / "core")
        dest = self.dirs.app / "core" / "target" / "release"
        rel.place_core_bins(
            files, dest, ".exe" if self.dirs.system == "Windows" else ""
        )
        self.found["tag"] = found.tag
        return "release"

    def bundled_core(self, folder: Path) -> str:
        """castle-core from `folder` — the copies the desktop app carries,
        built by the same release as the tree beside them: placed where
        tools/core_bins.py looks, with nothing fetched."""
        suffix = ".exe" if self.dirs.system == "Windows" else ""
        files = [folder / (b + suffix) for b in rel.CORE_BINS]
        missing = [f.name for f in files if not f.is_file()]
        if missing:
            raise rel.ReleaseError(
                f"{folder} has no {', '.join(missing)} — reinstall Castle Tools"
            )
        dest = self.dirs.app / "core" / "target" / "release"
        self.step(
            f"copy castle-core from {folder} -> {dest}",
            lambda: rel.place_core_bins(files, dest, suffix),
        )
        return "bundled"

    def have_core(self, tag: str) -> bool:
        """This release's binaries are already in place (a re-run, an
        up-to-date --update): no download. --repair always re-fetches."""
        dest = self.dirs.app / "core" / "target" / "release"
        suffix = ".exe" if self.dirs.system == "Windows" else ""
        return (
            not self.args.repair
            and self.record.get("core") == "release"
            and self.record.get("tag") == tag
            and all((dest / (b + suffix)).is_file() for b in rel.CORE_BINS)
        )

    # -- the rest --------------------------------------------------------
    def env(self) -> dict[str, str]:
        record = de.install_record(self.dirs, **self.found)
        settings = de.read_json(self.dirs.settings_file)
        return de.launch_env(self.dirs, record, settings, dict(os.environ))

    def model(self) -> None:
        if self.args.skip_model:
            self.say("htdemucs: skipped (--skip-model)")
            return
        self.run([str(self.dirs.python), "-c", MODEL_SCRIPT], env=self.env())

    def data(self) -> None:
        def prep() -> None:
            for note in de.prepare(self.dirs):
                self.say(note)
            if self.args.castle_host is not None:
                settings = de.read_json(self.dirs.settings_file)
                settings["castle_host"] = self.args.castle_host
                de.write_json(self.dirs.settings_file, settings)

        self.step(f"prepare the data dir {self.dirs.data}", prep)

    def launcher(self) -> None:
        name = LAUNCHERS.get(self.dirs.system, LAUNCHER_DEFAULT)
        src = self.dirs.app / "installer" / name
        out = self.dirs.install / name

        def place() -> None:
            shutil.copyfile(src, out)
            if self.dirs.system != "Windows":
                out.chmod(0o755)

        if self.args.no_launcher:
            self.say("launcher: none (--no-launcher)")
        else:
            self.step(f"place the launcher {out}", place)
        if self.dirs.system == "Darwin" and self.which("xattr"):
            # Files this installer verified and placed: no Gatekeeper prompt.
            cmd = ["xattr", "-dr", "com.apple.quarantine", str(self.dirs.install)]
            self.step(
                "run: " + " ".join(cmd),
                lambda: subprocess.run(cmd, check=False, capture_output=True),
            )

    def write_record(self, core: str) -> None:
        found = dict(self.found)
        if "tag" not in found and core == "release":
            found["tag"] = str(self.record.get("tag", ""))
        found.update(
            core=core,
            installed=datetime.now(UTC).isoformat(timespec="seconds"),
            source=str(self.args.source),
        )
        record = de.install_record(self.dirs, **found)
        self.step(
            f"write {self.dirs.install_file}",
            lambda: de.write_json(self.dirs.install_file, record),
        )

    def status(self) -> None:
        """The same readiness probe the website shows, run under the
        launcher's environment — informational, never fails the install."""
        probe = self.dirs.app / "tools" / "castle_tools_status.py"
        cmd = [str(self.dirs.python), str(probe), "--human"]
        self.step(
            "check: " + " ".join(cmd),
            lambda: subprocess.run(cmd, check=False, env=self.env(), cwd=self.dirs.app),
        )

    def install(self) -> int:
        src = Path(self.args.source).resolve()
        self.say(f"Castle Tools: install {self.dirs.install}, data {self.dirs.data}")
        if not self.args.dry_run:
            self.dirs.install.mkdir(parents=True, exist_ok=True)
        self.phase(1)
        self.stage_app(src)
        self.phase(2)
        self.python_env()
        self.phase(3)
        core = self.core_bins(src)
        self.phase(4)
        self.ffmpeg()
        self.phase(5)
        self.ytdlp()
        self.phase(6)
        self.model()
        self.phase(7)
        self.data()
        self.phase(8)
        self.launcher()
        self.write_record(core)
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.status()
        self.say(
            "dry run: nothing changed"
            if self.args.dry_run
            else "Castle Tools are installed."
        )
        return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", type=Path, default=de.ROOT, help="tree to install from")
    ap.add_argument("--uv", default=shutil.which("uv") or "uv", help="the uv binary")
    ap.add_argument("--prefix", type=Path, help="install root (default: per-user)")
    ap.add_argument("--data-dir", type=Path, help="data root (default: per-user)")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument(
        "--purge", action="store_true", help="with --uninstall: delete the data too"
    )
    ap.add_argument(
        "--from-source", action="store_true", help="build castle-core with cargo"
    )
    ap.add_argument(
        "--core-from", type=Path, help="castle-core's binaries are in this folder"
    )
    ap.add_argument("--no-launcher", action="store_true", help="place no launcher")
    ap.add_argument(
        "--progress", action="store_true", help="print @castle-step lines per step"
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", help="install this release's binaries (vX.Y.Z)")
    ap.add_argument(
        "--castle-host", help="the castle's address, saved in settings.json"
    )
    ap.add_argument(
        "--ffmpeg", default="auto", help="auto | download | /path/to/ffmpeg"
    )
    ap.add_argument("--skip-model", action="store_true", help="do not fetch htdemucs")
    ap.add_argument("--release-json", help=argparse.SUPPRESS)
    return ap


def resolve_dirs(
    args: argparse.Namespace, environ: dict[str, str] | None = None
) -> de.Dirs:
    env = dict(os.environ if environ is None else environ)
    if args.prefix:
        env["CASTLE_TOOLS_HOME"] = str(args.prefix)
    if args.data_dir:
        env["CASTLE_TOOLS_DATA"] = str(args.data_dir)
    dirs = de.default_dirs(environ=env)
    # An existing install remembers where its data went.
    recorded = de.read_json(dirs.install_file).get("data")
    if recorded and not args.data_dir:
        dirs = de.Dirs(
            install=dirs.install, data=Path(str(recorded)), system=dirs.system
        )
    return dirs


def main(argv: list[str] | None = None) -> int:
    import desktop_lifecycle as life

    args = build_parser().parse_args(argv)
    if args.purge and not args.uninstall:
        print("--purge only means something with --uninstall", file=sys.stderr)
        return 2
    dirs = resolve_dirs(args)
    try:
        if args.uninstall:
            return life.uninstall(dirs, purge=args.purge, dry_run=args.dry_run)
        if args.update and not args.release_json:
            return life.update(args, dirs)
        return Installer(args, dirs).install()
    except (rel.ReleaseError, subprocess.CalledProcessError, OSError) as exc:
        if args.progress:
            print(progress.failed_line(exc), flush=True)
        print(f"Castle Tools: install failed — {exc}", file=sys.stderr)
        print(
            "Fix the problem above and run the installer again; it resumes.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
