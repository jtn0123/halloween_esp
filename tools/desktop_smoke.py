#!/usr/bin/env python3
"""The desktop app's first-run smoke, on a machine that has never run it.

    desktop_smoke.py BUNDLE_DIR [--timeout SECONDS]

BUNDLE_DIR is Tauri's bundle output (src-tauri/target/<target>/release/
bundle). macOS starts the .app in macos/; Windows installs the NSIS setup
silently, per user, the way a buyer's double-click does, and starts what it
installed. Then:

  1. the first launch has no runtime, so it sets one up from its bundled
     castle/ (desktop/README.md "Where the servers come from") — Castle
     Radio must answer GET /radio/tools as itself within --timeout, able to
     import, import from a link and split voices, and the cue desk studio
     must answer beside it with the shipped show (scenes/shipped.yaml) as
     the owner's — the one show the app carries and seeds;
  2. the app is quit, started again, and must answer within a few minutes
     WITHOUT setting anything up a second time.

The app's log is printed when either fails. release.yml's desktop job runs
this on both runners (docs/RELEASING.md). Both servers get free ports
(CASTLE_STUDIO_PORT / CASTLE_DESK_PORT), so a runner's 8871 is never asked.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

IDENTIFIER = "io.github.jtn0123.castletools"
LOG_NAME = "castle-tools.log"
#: setup.rs's log lines: a setup starting, and one that finished.
SETUP_BEGAN = "setup ("
SETUP_DONE = "setup finished in"
#: And one that failed: the app then waits on its splash for Try again, so a
#: smoke that only asked the port would sit out the whole --timeout.
SETUP_FAILED = "setup failed: "
#: supervisor.rs's, when Castle Radio starts from runtime.rs's Source::Bundled
#: — the app's own, not a configured install or a checkout the runner has.
STARTED_OWN = "starting Castle Radio from the app's own runtime"
#: What /radio/tools must say a fresh setup can do (castle_tools_status.py).
CAPABILITIES = ("importing", "url_importing", "separation")
#: The show a first run seeds (childenv.rs, desktop_env.SHIPPED_SCENES), in
#: the checkout this smoke runs from. A studio answers whatever it found, so
#: it must read back exactly these ids: an empty list is a show that was
#: never seeded, and any other list is a show the app should not have.
SHIPPED = Path(__file__).resolve().parent.parent / "scenes" / "shipped.yaml"
SECOND_START = 180.0
STUDIO_START = 120.0
POLL = 2.0

Json = dict[str, object]
Run = Callable[..., object]


class SmokeError(RuntimeError):
    """The app did not do what a buyer's first and second launch need."""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def get_json(port: int, path: str, timeout: float = 3.0) -> Json | None:
    """GET http://127.0.0.1:port/path as a JSON object, or None."""
    try:
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def is_radio(body: Json) -> bool:
    """probe.rs's identity for Castle Radio."""
    return body.get("service") == "castle-radio" and body.get("protocol") == 1


def is_studio(body: Json) -> bool:
    """probe.rs's identity for the cue desk studio."""
    return isinstance(body.get("tracks"), list) and isinstance(body.get("scenes"), list)


def show_ids(path: Path) -> list[str]:
    """The `  - id:` lines under `scenes:`, the rule core/src/studio.rs
    scene_ids reads a show by."""
    section, ids = "", []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line[:1] not in ("", " ", "\t", "#"):
            section = line.split(":", 1)[0].strip() if ":" in line else section
            continue
        if section == "scenes" and line.startswith("  - id: "):
            ids.append(line[8:].split("#", 1)[0].strip().strip("\"'"))
    return [i for i in ids if i]


def log_path(system: str, env: dict[str, str]) -> Path:
    """Tauri's app_log_dir for this identifier, and the app's log in it."""
    if system == "Windows":
        return Path(env["LOCALAPPDATA"]) / IDENTIFIER / "logs" / LOG_NAME
    return Path(env["HOME"]) / "Library" / "Logs" / IDENTIFIER / LOG_NAME


def mac_app(bundle: Path) -> Path:
    """The executable of the one .app under bundle/macos."""
    apps = sorted((bundle / "macos").glob("*.app"))
    if len(apps) != 1:
        raise SmokeError(f"expected one .app in {bundle / 'macos'}, found {apps}")
    info = plistlib.loads((apps[0] / "Contents" / "Info.plist").read_bytes())
    return apps[0] / "Contents" / "MacOS" / str(info["CFBundleExecutable"])


def windows_app(bundle: Path, env: dict[str, str], run: Run = subprocess.run) -> Path:
    """Install the NSIS setup silently (per user, no prompt), and return the
    app it installed."""
    setups = sorted((bundle / "nsis").glob("*-setup.exe"))
    if len(setups) != 1:
        raise SmokeError(f"expected one setup .exe in {bundle / 'nsis'}: {setups}")
    run([str(setups[0]), "/S"], check=True, timeout=600)
    local = Path(env["LOCALAPPDATA"])
    for folder in (local / "Castle Tools", local / "Programs" / "Castle Tools"):
        exes = [
            p for p in sorted(folder.glob("*.exe")) if "uninst" not in p.name.lower()
        ]
        if exes:
            return exes[0]
    raise SmokeError(f"the setup installed no Castle Tools .exe under {local}")


def read_from(log: Path, offset: int) -> str:
    """The log from `offset` on — from the top when it is now shorter than
    that, because logfile.rs moved a big one aside as the app started."""
    if size(log) < offset:
        offset = 0
    try:
        with log.open("rb") as fh:
            fh.seek(offset)
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def size(log: Path) -> int:
    try:
        return log.stat().st_size
    except OSError:
        return 0


def setup_failure(wrote: str) -> str | None:
    """setup.rs's failure line in what a launch wrote, when its setup failed."""
    lines = (ln.strip() for ln in wrote.splitlines() if SETUP_FAILED in ln)
    return next(lines, None)


def wait_for(
    proc: subprocess.Popen[bytes],
    ask: Callable[[], Json | None],
    good: Callable[[Json], bool],
    timeout: float,
    what: str,
    doomed: Callable[[], str | None] = lambda: None,
) -> Json:
    """Ask until `good`. The app exiting, `doomed` naming a reason it never
    will answer, or the time running out, fails."""
    deadline = time.monotonic() + timeout
    while True:
        body = ask()
        if body is not None and good(body):
            return body
        if proc.poll() is not None:
            raise SmokeError(
                f"the app exited ({proc.returncode}) before {what} answered"
            )
        why = doomed()
        if why:
            raise SmokeError(f"{what} will not answer: {why}")
        if time.monotonic() > deadline:
            raise SmokeError(f"{what} did not answer within {timeout:.0f}s")
        time.sleep(POLL)


def quit_app(
    proc: subprocess.Popen[bytes], system: str, run: Run = subprocess.run
) -> None:
    """Quit the way the OS asks an app to: SIGTERM (signals.rs: an orderly
    quit) on macOS; on Windows, the whole tree — the job objects child.rs
    puts the servers in die with the app."""
    if proc.poll() is not None:
        return
    if system == "Windows":
        run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], check=False)
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def gone(port: int, timeout: float = 30.0) -> None:
    """Wait for nothing to answer on `port` — the app's servers went with it."""
    deadline = time.monotonic() + timeout
    while get_json(port, "/radio/tools", 1.0) is not None:
        if time.monotonic() > deadline:
            raise SmokeError(f"port {port} still answers after the app quit")
        time.sleep(1.0)


def capable(status: Json) -> list[str]:
    """What a finished setup should be able to do and, per status, cannot."""
    caps = status.get("capabilities")
    have = caps if isinstance(caps, dict) else {}
    missing = [c for c in CAPABILITIES if not have.get(c)]
    if not status.get("core_ready"):
        missing.append("core_ready")
    return missing


def start(
    cmd: list[str],
    env: dict[str, str],
    ports: tuple[int, int],
    timeout: float,
    log: Path,
    system: str,
) -> tuple[Json, object, str, float]:
    """One launch, to both servers answering, then quit. Returns Castle
    Radio's /radio/tools, the studio's scenes, the log this launch wrote,
    and the seconds taken."""
    radio, desk = ports
    mark, began = size(log), time.monotonic()
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL)
    try:
        status = wait_for(
            proc,
            lambda: get_json(radio, "/radio/tools"),
            is_radio,
            timeout,
            "Castle Radio",
            lambda: setup_failure(read_from(log, mark)),
        )
        desk_body = wait_for(
            proc,
            lambda: get_json(desk, "/studio/tracks"),
            is_studio,
            STUDIO_START,
            "the cue desk studio",
        )
        took = time.monotonic() - began
    finally:
        quit_app(proc, system)
    gone(radio)
    return status, desk_body.get("scenes"), read_from(log, mark), took


def judge(
    cmd: list[str], env: dict[str, str], timeout: float, log: Path, system: str
) -> None:
    """The two launches, and what each must show."""
    ports = (free_port(), free_port())
    env = {
        **env,
        "CASTLE_STUDIO_PORT": str(ports[0]),
        "CASTLE_DESK_PORT": str(ports[1]),
    }
    print(f"smoke: {cmd[-1]}; Castle Radio on {ports[0]}, the studio on {ports[1]}")
    status, scenes, wrote, took = start(cmd, env, ports, timeout, log, system)
    print(f"first launch: both servers answered after {took:.0f}s")
    print(json.dumps(status.get("checks"), indent=2))
    if SETUP_DONE not in wrote:
        raise SmokeError(
            "the first launch answered without a finished setup in its log"
        )
    own(wrote, "first")
    missing = capable(status)
    if missing:
        raise SmokeError(f"after setup, Castle Radio still lacks {', '.join(missing)}")
    want = show_ids(SHIPPED)
    if scenes != want:
        raise SmokeError(
            f"the studio's show is {scenes}, not the shipped show's {want}: "
            "the first run seeded another show, or none"
        )
    print(f"the owner's show: the shipped one, {len(want)} scenes")
    _, _, wrote, took = start(cmd, env, ports, SECOND_START, log, system)
    print(f"second launch: both servers answered after {took:.0f}s")
    own(wrote, "second")
    if SETUP_BEGAN in wrote:
        raise SmokeError("the second launch set the runtime up again")


def own(wrote: str, which: str) -> None:
    """The launch's log says Castle Radio ran from the app's own runtime —
    which also proves the log this smoke reads is the app's."""
    if STARTED_OWN not in wrote:
        raise SmokeError(
            f"the {which} launch's log never started Castle Radio from the "
            "app's own runtime"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args(argv)
    system, env = platform.system(), dict(os.environ)
    log = log_path(system, env)
    try:
        exe = (
            mac_app(args.bundle)
            if system == "Darwin"
            else windows_app(args.bundle, env)
        )
        judge([str(exe)], env, args.timeout, log, system)
    except (SmokeError, OSError, subprocess.SubprocessError) as exc:
        print(f"desktop smoke FAILED: {exc}", file=sys.stderr)
        tail = read_from(log, 0).splitlines()[-300:]
        print("\n".join([f"--- {log}, last lines ---", *tail]), file=sys.stderr)
        return 1
    print("desktop smoke: the first launch set up, the second reused it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
