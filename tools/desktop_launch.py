#!/usr/bin/env python3
"""Start Castle Tools and open it — what `Castle Tools.command` / `.cmd` run.

The same promise as `Open Castle Studio.command`, in Python so both
platforms share it: check, start, open; never install. A second launch
finds Castle Radio already answering its identity route (`/radio/tools`:
`service == "castle-radio"`, `protocol == 1`) and only opens the browser.
A port held by something else is an error, not a second server.

Castle Radio (8871) is the buyer's page: player, imports, the castle link.
`--desk` also starts the cue desk studio (8765), which needs a built
previewer page and is an owner's tool. Both bind 127.0.0.1 only — no
Windows firewall prompt, nothing reachable from the LAN.

The update check is the launcher's only network use: at most once a day,
one unauthenticated call, a printed line, and never a reason not to start.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import desktop_env as de
import desktop_release as rel
import release_channel as channel

UPDATE_STAMP = "update-check.json"
UPDATE_EVERY = timedelta(days=1)
#: (url) -> parsed JSON body or None. Injectable; urllib in production.
Probe = Callable[[str], Any]


def probe_json(url: str, timeout: float = 2.0) -> Any:
    """GET a local JSON route: the body, None when nothing answers, or the
    marker "other" when something answers that is not JSON."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError:
        return "other"
    except (OSError, ValueError):
        return None
    try:
        return json.loads(body)
    except ValueError:
        return "other"


def radio_state(port: int, probe: Probe = probe_json) -> str:
    """ "ours" (Castle Radio answers), "free", or "taken" by something else."""
    answer = probe(f"http://127.0.0.1:{port}/radio/tools")
    if answer is None:
        return "free"
    if isinstance(answer, dict) and answer.get("service") == "castle-radio":
        if answer.get("protocol") == 1:
            return "ours"
    return "taken"


def desk_state(port: int, probe: Probe = probe_json) -> str:
    """The studio has no identity route; a JSON list from /api/tracks is it."""
    answer = probe(f"http://127.0.0.1:{port}/api/tracks")
    if answer is None:
        return "free"
    return "ours" if isinstance(answer, (list, dict)) else "taken"


def wait_until(
    check: Callable[[], bool], seconds: float, alive: Callable[[], bool]
) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        if not alive():
            return False
        time.sleep(0.25)
    return False


def update_due(stamp: Mapping[str, Any], now: datetime) -> bool:
    """True when the last check is missing, unreadable, or a day old."""
    try:
        last = datetime.fromisoformat(str(stamp["checked"]))
    except (KeyError, ValueError):
        return True
    return now - last >= UPDATE_EVERY


def check_for_update(
    dirs: de.Dirs,
    record: Mapping[str, Any],
    settings: Mapping[str, Any],
    fetch: rel.Fetch = rel.http_fetch,
    now: datetime | None = None,
) -> str | None:
    """One line for the console when a newer release exists, else None.
    Records the check so the next launch today does not ask again."""
    if settings.get("check_updates") is False:
        return None
    now = now or datetime.now(UTC)
    stamp_file = dirs.data / UPDATE_STAMP
    if not update_due(de.read_json(stamp_file), now):
        return None
    early = channel.opted_in(settings)
    try:
        latest = channel.newest(fetch, early).tag
    except rel.ReleaseError:
        return None
    de.write_json(
        stamp_file, {"checked": now.isoformat(timespec="seconds"), "latest": latest}
    )
    installed = str(record.get("tag") or "")
    if channel.is_newer(latest, installed, early):
        return (
            f"Castle Tools {latest} is available (you have {installed or 'a dev build'}). "
            "Run the installer with --update to get it."
        )
    return None


def listen_port(value: int) -> int:
    """The Castle Radio port a launch may ask for, as a plain int — the
    only shape of `--port` that reaches the server's command line. Below
    1024 is a privileged port no buyer's launch should want."""
    port = int(value)
    if not 1024 <= port <= 65535:
        raise ValueError(f"--port must be 1024-65535, not {port}")
    return port


def start(cmd: list[str], env: Mapping[str, str], cwd: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(cmd, env=dict(env), cwd=cwd)


def spawn(
    dirs: de.Dirs, env: Mapping[str, str], port: int, desk: bool
) -> list[subprocess.Popen[bytes]]:
    """Castle Radio first (the list's head is the one main waits on), then
    the cue desk when asked for and its port is free."""
    py = str(env["CASTLE_PY"])
    radio = [py, str(dirs.app / "demo" / "castle-radio" / "server.py"), str(port)]
    children = [start(radio, env, dirs.app)]
    if desk and desk_state(de.DESK_PORT) == "free":
        exe = dirs.app / "core" / "target" / "release" / dirs.exe("studio")
        children.append(
            start([str(exe), str(de.DESK_PORT), "--localhost"], env, dirs.app)
        )
    return children


def run_children(
    children: list[subprocess.Popen[bytes]],
    port: int,
    url: str,
    show: Callable[[str], object],
) -> int:
    """Wait for Castle Radio to answer, open it, and hold the window until
    it exits; every child goes with it."""
    radio = children[0]
    up = wait_until(
        lambda: radio_state(port) == "ours", 30, lambda: radio.poll() is None
    )
    if not up:
        print("Castle Tools did not start. See the messages above.")
        for child in children:
            child.terminate()
        return 1
    show(url)
    print(f"Castle Tools are running at {url}")
    print("Close this window (or press Ctrl-C) to stop them.")
    try:
        return radio.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=de.RADIO_PORT)
    ap.add_argument("--desk", action="store_true", help="also start the cue desk")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    try:
        port = listen_port(args.port)
    except ValueError as exc:
        print(exc)
        return 2

    dirs = de.installed_dirs()
    record = de.read_json(dirs.install_file)
    if int(record.get("schema", 0)) > de.SCHEMA:
        print("This launcher is older than the install. Run the installer again.")
        return 1
    settings = de.read_json(dirs.settings_file)
    env = de.launch_env(dirs, record, settings, os.environ)
    url = f"http://127.0.0.1:{port}/"
    show = (lambda u: None) if args.no_browser else webbrowser.open

    threading.Thread(
        target=lambda: print(check_for_update(dirs, record, settings) or "", end=""),
        daemon=True,
    ).start()

    state = radio_state(port)
    if state == "ours":
        print(f"Castle Tools are already running: {url}")
        show(url)
        return 0
    if state == "taken":
        print(f"Port {port} is in use by another program. Close it and try again.")
        return 1

    for note in de.prepare(dirs):
        print(note)
    return run_children(spawn(dirs, env, port, args.desk), port, url, show)


if __name__ == "__main__":
    raise SystemExit(main())
