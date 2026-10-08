"""Where an installed Castle Tools lives, and the environment it runs in.

One answer for both installers (installer/install.sh, install.ps1) and both
launchers (`Castle Tools.command`, `Castle Tools.cmd`), so the rules are
Python with tests rather than the same paths spelled twice in two shell
languages. Stdlib only: the installer runs this under a bare uv-managed
3.13 before the environment it is about to create exists.

Two directories, kept apart on purpose (docs/PRODUCTION-TODO.md 5.3):

  INSTALL  — replaceable. `app/` (the source tree of one release), `env/`
             (the Python environment), `bin/` (downloaded ffmpeg/yt-dlp),
             `models/` (the htdemucs weights), `install.json` (what the
             installer found and fetched). `--uninstall` removes it whole.
  DATA     — the user's. `tracks/`, `scenes.yaml`, `build/`, Castle Radio's
             catalog and waveforms, `settings.json`, and `devices.toml` (the
             castle keys either app remembers — tools/castle_keys.py). Only
             `--purge` touches it.

DATA's layout is Castle Radio's `.radio-data/` layout on purpose: that
server hard-codes its data home beside its own source, so the installed
tree's `demo/castle-radio/.radio-data` is a link to DATA, and the studio's
CASTLE_TRACKS / CASTLE_SCENES / CASTLE_BUILD name the same three paths —
one library, whichever server the user opens.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # stdlib-only, like this module
import release_channel  # and this

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "CastleTools"
INSTALL_FILE = "install.json"
SETTINGS_FILE = "settings.json"
#: Bumped when install.json's meaning changes; a launcher refuses a newer one.
SCHEMA = 1
RADIO_PORT = 8871
DESK_PORT = 8765
#: Where Castle Radio keeps its data, relative to an app tree.
RADIO_DATA = Path("demo") / "castle-radio" / ".radio-data"
#: The show a first run seeds: the yard's minus its songs (no song ships —
#: tools/shipped_show.py), never scenes/scenes.yaml itself.
SHIPPED_SCENES = Path("scenes") / "shipped.yaml"


@dataclass(frozen=True)
class Dirs:
    """The two roots, and everything derived from them."""

    install: Path
    data: Path
    system: str  # platform.system(): "Windows", "Darwin", "Linux"

    @property
    def app(self) -> Path:
        return self.install / "app"

    @property
    def env(self) -> Path:
        return self.install / "env"

    @property
    def bin(self) -> Path:
        return self.install / "bin"

    @property
    def models(self) -> Path:
        return self.install / "models"

    @property
    def python(self) -> Path:
        if self.system == "Windows":
            return self.env / "Scripts" / "python.exe"
        return self.env / "bin" / "python"

    @property
    def tracks(self) -> Path:
        return self.data / "tracks"

    @property
    def scenes(self) -> Path:
        return self.data / "scenes.yaml"

    @property
    def build(self) -> Path:
        return self.data / "build"

    @property
    def devices(self) -> Path:
        """The castle key store (CASTLE_DEVICES) — the user's, never a tree's."""
        return self.data / "devices.toml"

    @property
    def install_file(self) -> Path:
        return self.install / INSTALL_FILE

    @property
    def settings_file(self) -> Path:
        return self.data / SETTINGS_FILE

    def exe(self, name: str) -> str:
        """`name` as this platform spells an executable."""
        return f"{name}.exe" if self.system == "Windows" else name


def default_dirs(
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Dirs:
    """The per-user install and data roots for `system`.

    CASTLE_TOOLS_HOME / CASTLE_TOOLS_DATA override either — the installer's
    --prefix/--data-dir set them, and the tests and a throwaway run use them
    so nothing reaches a real profile.
    """
    system = system or platform.system()
    env = os.environ if environ is None else environ
    home = home or Path.home()
    if system == "Windows":
        local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        install, data = local / "Programs" / APP_NAME, local / APP_NAME
    elif system == "Darwin":
        install = home / "Applications" / APP_NAME
        data = home / "Library" / "Application Support" / APP_NAME
    else:
        share = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
        install, data = home / ".local" / "opt" / APP_NAME, share / APP_NAME
    if env.get("CASTLE_TOOLS_HOME"):
        install = Path(env["CASTLE_TOOLS_HOME"]).expanduser()
    if env.get("CASTLE_TOOLS_DATA"):
        data = Path(env["CASTLE_TOOLS_DATA"]).expanduser()
    return Dirs(install=install, data=data, system=system)


def installed_dirs(here: Path = ROOT, system: str | None = None) -> Dirs:
    """The roots a launcher runs under: the install this file belongs to.

    A launcher runs `<install>/app/tools/desktop_launch.py`; its install root
    is two levels up and says so with install.json, which also records the
    data root the installer chose. Anything else (a dev checkout) falls back
    to the defaults.
    """
    record = read_json(here.parent / INSTALL_FILE)
    if record.get("data"):
        return Dirs(
            install=here.parent,
            data=Path(str(record["data"])),
            system=system or platform.system(),
        )
    return default_dirs(system)


def read_json(path: Path) -> dict[str, Any]:
    """A JSON object from `path`, or {} when it is missing or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write beside, then rename: a crash leaves the old file, not half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install_record(dirs: Dirs, **found: str) -> dict[str, Any]:
    """install.json's contents: the roots plus every tool path the installer
    settled on. Rewritten on every install/repair, never hand-edited."""
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "install": str(dirs.install),
        "data": str(dirs.data),
        "python": str(dirs.python),
    }
    record.update({k: v for k, v in found.items() if v})
    return record


def seed_scenes(shipped: Path, target: Path) -> bool:
    """Copy the shipped show to `target` unless the user already has one.

    Exclusive create, not exists-then-copy: there is no window in which a
    second installer run (or a launcher racing it) could overwrite a show
    the user has edited. True when this call wrote the file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    body = shipped.read_bytes()
    try:
        with target.open("xb") as fh:
            fh.write(body)
    except FileExistsError:
        return False
    return True


def link_radio_data(dirs: Dirs, app: Path | None = None) -> str:
    """Point the app tree's Castle Radio data dir at DATA.

    Returns what happened: "linked", "kept" (already the right link) or
    "skipped: <why>". A real directory already there is LEFT ALONE — it is
    somebody's library (a dev checkout's), and moving it is a decision for
    a person. Windows gets a directory junction (no admin rights needed,
    unlike a symlink); everything else a symlink.
    """
    app = app or dirs.app
    link = app / RADIO_DATA
    dirs.data.mkdir(parents=True, exist_ok=True)
    if is_link(link):
        if Path(os.path.realpath(link)) == Path(os.path.realpath(dirs.data)):
            return "kept"
        remove_link(link)
    elif link.exists():
        return f"skipped: {link} is a real directory"
    link.parent.mkdir(parents=True, exist_ok=True)
    if dirs.system == "Windows":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(dirs.data)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(dirs.data, target_is_directory=True)
    return "linked"


def is_link(path: Path) -> bool:
    """A symlink or a Windows junction — something to unlink, never to recurse."""
    return path.is_symlink() or os.path.isjunction(path)


def remove_link(path: Path) -> None:
    """Remove the link itself. os.rmdir on a junction removes only the
    junction; os.unlink on a symlink only the symlink. Never rmtree."""
    if os.path.isjunction(path):
        os.rmdir(path)
    else:
        path.unlink()


def launch_env(
    dirs: Dirs,
    record: Mapping[str, Any],
    settings: Mapping[str, Any],
    base: Mapping[str, str],
) -> dict[str, str]:
    """The environment both servers and every child they spawn run under."""
    env = dict(base)
    env.update(
        CASTLE_TRACKS=str(dirs.tracks),
        CASTLE_SCENES=str(dirs.scenes),
        CASTLE_BUILD=str(dirs.build),
        CASTLE_DEVICES=str(dirs.devices),
        CASTLE_PY=str(record.get("python") or dirs.python),
        # Demucs fetches its weights through huggingface_hub; the installer
        # downloaded them here, so the import never reaches the network.
        HF_HOME=str(dirs.models / "huggingface"),
        TORCH_HOME=str(dirs.models / "torch"),
        # Windows' default text encoding is the ANSI code page, and the
        # tools read UTF-8 YAML and JSON. Harmless everywhere else.
        PYTHONUTF8="1",
    )
    for key, var in (("ffmpeg", "CASTLE_FFMPEG"), ("ytdlp", "CASTLE_YTDLP")):
        if record.get(key):
            env[var] = str(record[key])
    # The managed yt-dlp is the installer's own, in bin/: Update the
    # downloader (Castle Radio) and --update replace the one copy.
    env["CASTLE_DOWNLOADER_DIR"] = str(dirs.bin)
    # The tools that predate CASTLE_FFMPEG/CASTLE_YTDLP find them on PATH,
    # so their directories go first: ours, then wherever ffmpeg was found.
    front = [str(dirs.bin), str(Path(str(env["CASTLE_PY"])).parent)]
    front += [
        str(Path(str(record[key])).parent)
        for key in ("ffmpeg", "ytdlp")
        if record.get(key)
    ]
    rest = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    seen: list[str] = []
    for p in front + rest:
        if p not in seen:
            seen.append(p)
    env["PATH"] = os.pathsep.join(seen)
    host = str(settings.get("castle_host") or "").strip()
    # A castle named in settings.json pins both servers to it. Unnamed, both
    # follow the first castle of CASTLE_DEVICES — the per-user store "Find my
    # castle" writes (tools/castle_address.py) — so neither variable is set,
    # and one this launcher inherited is not passed on.
    env.pop("CASTLE_HOST", None)
    env.pop("CASTLE_RADIO_HOST", None)
    if host:
        env["CASTLE_HOST"] = env["CASTLE_RADIO_HOST"] = host
    # A key typed into settings.json pins it for both servers (CASTLE_KEY
    # wins over the store); one no castle could hold is not passed at all.
    held = settings.get("castle_key")
    if isinstance(held, str) and hosts.valid_key(held.strip()):
        env["CASTLE_KEY"] = held.strip()
    # The hidden pre-release opt-in (tools/release_channel.py), for every
    # child that asks GitHub — Castle Radio's "Update castle" among them. A
    # CASTLE_PRERELEASE already in `base` is left to win, as it does there.
    if release_channel.opted_in(settings, base):
        env[release_channel.ENV] = "1"
    return env


def shown(env: Mapping[str, str]) -> list[str]:
    """`env` as `KEY=value` lines for a person to read — the key withheld."""
    return [f"{k}={'<set>' if k == 'CASTLE_KEY' else env[k]}" for k in sorted(env)]


def prepare(dirs: Dirs) -> list[str]:
    """Everything a launch needs to exist under DATA, idempotently."""
    notes = []
    for d in (dirs.data, dirs.tracks, dirs.build):
        d.mkdir(parents=True, exist_ok=True)
    shipped = dirs.app / SHIPPED_SCENES
    if shipped.is_file() and seed_scenes(shipped, dirs.scenes):
        notes.append(f"seeded {dirs.scenes} from the shipped show")
    state = link_radio_data(dirs)
    if state.startswith("skipped"):
        notes.append(state)
    return notes


def main(argv: list[str] | None = None) -> int:
    """`desktop_env.py dirs|env` — print the roots or the launch environment."""
    args = sys.argv[1:] if argv is None else argv
    dirs = installed_dirs()
    if args[:1] == ["dirs"]:
        print(json.dumps({"install": str(dirs.install), "data": str(dirs.data)}))
        return 0
    if args[:1] == ["env"]:
        env = launch_env(
            dirs,
            read_json(dirs.install_file),
            read_json(dirs.settings_file),
            {"PATH": os.environ.get("PATH", "")},
        )
        print("\n".join(shown(env)))
        return 0
    print("usage: desktop_env.py dirs|env", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
