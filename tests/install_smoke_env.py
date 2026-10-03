"""A fresh buyer, as the install smoke test (tests/install_smoke.py) plays one.

Everything here is decided without a network, so tests/test_install_smoke.py
holds it: the environment a new user account starts with, where the
installer will put things, what "nothing changed" means for a tree, the
command a double-click runs, and the processes the test starts and must take
down whole.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_env as de
import portable_proc

#: What a new account's shell holds, by name. Everything else in the job's
#: environment — the runner's toolcache Python, cargo, Homebrew, Chocolatey,
#: VIRTUAL_ENV, every CASTLE_* knob — is something a buyer does not have.
POSIX_KEEP = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
#: PSModulePath is left out on purpose: a pwsh-launched job hands Windows
#: PowerShell 5.1 PowerShell 7's module path, and 5.1 then cannot load its
#: own Microsoft.PowerShell.Utility. A fresh account has 5.1's default.
WINDOWS_KEEP = (
    "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMPUTERNAME",
    "COMSPEC", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS",
    "OS", "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PUBLIC",
    "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERDOMAIN", "USERNAME",
    "USERPROFILE", "WINDIR",
)  # fmt: skip
#: A Mac's PATH before anyone installs anything (no /opt/homebrew/bin).
POSIX_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
QUARANTINE = "com.apple.quarantine"
#: What Safari stamps on a download, and Archive Utility on what it unpacks.
QUARANTINE_VALUE = "0083;66f00000;Safari;"
#: What Edge writes beside a download, and Explorer's Extract All copies on
#: to every file it unpacks: the Mark of the Web.
ZONE_STREAM = ":Zone.Identifier"
ZONE_VALUE = "[ZoneTransfer]\r\nZoneId=3\r\n"


def check(ok: bool, what: str) -> None:
    """One assertion of the smoke test, said either way in the job's log."""
    if not ok:
        raise SystemExit(f"FAILED: {what}")
    print(f"ok: {what}", flush=True)


def _get(env: Mapping[str, str], name: str) -> str | None:
    """`env[name]`, case-blind as Windows reads its environment."""
    for key, value in env.items():
        if key.upper() == name.upper():
            return value
    return None


def windows_path(env: Mapping[str, str]) -> str:
    """A new Windows 11 account's PATH: the system's, plus the per-user
    WindowsApps folder where winget's alias lives."""
    root = _get(env, "SYSTEMROOT") or r"C:\Windows"
    local = _get(env, "LOCALAPPDATA") or ""
    parts = [
        rf"{root}\system32",
        root,
        rf"{root}\System32\Wbem",
        rf"{root}\System32\WindowsPowerShell\v1.0",
        rf"{root}\System32\OpenSSH",
    ]
    if local:
        parts.append(rf"{local}\Microsoft\WindowsApps")
    return ";".join(parts)


def home_vars(system: str, home: Path) -> dict[str, str]:
    """The variables that move a whole profile to `home` — for a run on a
    developer's own machine, which must never install into their account."""
    if system == "Windows":
        return {
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "APPDATA": str(home / "AppData" / "Roaming"),
        }
    return {"HOME": str(home)}


def buyer_env(
    base: Mapping[str, str], system: str, home: Path | None = None
) -> dict[str, str]:
    """The environment of a person who just made an account and downloaded
    the zip: the account's own variables and nothing a developer added.

    Two additions, said here because they are not a buyer's, and both
    give the pipe this test reads through the console's behaviour:
    PYTHONIOENCODING — a console prints any character, where Windows'
    Python on a pipe encodes in the ANSI code page and dies on the first
    "鬼" — and PYTHONUNBUFFERED, since a console is line-buffered and a pipe
    is not, so the installer's lines would reach the log out of order."""
    keep = WINDOWS_KEEP if system == "Windows" else POSIX_KEEP
    env = {name: value for name in keep if (value := _get(base, name)) is not None}
    if home is not None:
        env.update(home_vars(system, home))
    env["PATH"] = windows_path(env) if system == "Windows" else POSIX_PATH
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def profile_home(env: Mapping[str, str], system: str) -> Path:
    name = "USERPROFILE" if system == "Windows" else "HOME"
    value = _get(env, name)
    if not value:
        raise SystemExit(f"the buyer's environment has no {name}")
    return Path(value)


def install_dirs(env: Mapping[str, str], system: str) -> de.Dirs:
    """Where the installer will put Castle Tools for this buyer: its own
    per-user defaults, asked of the code that decides them."""
    return de.default_dirs(system, environ=env, home=profile_home(env, system))


def uv_path(env: Mapping[str, str], system: str) -> Path:
    """Where Astral's installer puts uv when told not to touch the PATH."""
    return (
        profile_home(env, system)
        / ".local"
        / "bin"
        / ("uv.exe" if system == "Windows" else "uv")
    )


Snapshot = dict[str, tuple[int, int] | str]


def snapshot(root: Path, skip: frozenset[str] = frozenset()) -> Snapshot:
    """Every file under `root` as (size, mtime_ns), every link as its
    target — never followed, so the library link is not walked into. Names
    in `skip` (any path component) are left out. A missing root is {}."""
    out: Snapshot = {}
    if not root.exists():
        return out
    stack = [root]
    while stack:
        here = stack.pop()
        with os.scandir(here) as entries:
            for entry in entries:
                if entry.name in skip:
                    continue
                path = Path(entry.path)
                rel = path.relative_to(root).as_posix()
                if de.is_link(path):
                    out[rel] = "-> " + os.readlink(path)
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                else:
                    st = entry.stat(follow_symlinks=False)
                    out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def diff(before: Snapshot, after: Snapshot) -> list[str]:
    """What changed between two snapshots, one line per path."""
    lines = [f"removed {p}" for p in sorted(before.keys() - after.keys())]
    lines += [f"added {p}" for p in sorted(after.keys() - before.keys())]
    lines += [
        f"changed {p}"
        for p in sorted(before.keys() & after.keys())
        if before[p] != after[p]
    ]
    return lines


def installer_command(tree: Path, system: str, flags: list[str]) -> list[str]:
    """What the README tells a buyer to run: install.cmd (a double-click,
    which runs install.ps1 under Windows PowerShell) or `sh install.sh`."""
    if system == "Windows":
        return ["cmd", "/c", str(tree / "installer" / "install.cmd"), *flags]
    return ["sh", str(tree / "installer" / "install.sh"), *flags]


def launcher_command(dirs: de.Dirs, port: int) -> list[str]:
    """The launcher the installer placed — what the Start-menu shortcut and
    a double-click in Finder run — told to use `port` and no browser."""
    flags = ["--port", str(port), "--no-browser"]
    if dirs.system == "Windows":
        return ["cmd", "/c", str(dirs.install / "Castle Tools.cmd"), *flags]
    return ["sh", str(dirs.install / "Castle Tools.command"), *flags]


def mark_downloaded(root: Path, system: str) -> int:
    """Stamp every file under `root` the way a browser download and the
    system's unzip leave it; the count of files stamped (0 on Linux)."""
    files = [p for p in root.rglob("*") if p.is_file()]
    if system == "Windows":
        for path in files:
            with open(str(path) + ZONE_STREAM, "w", encoding="ascii") as fh:
                fh.write(ZONE_VALUE)
    elif system == "Darwin":
        # os.setxattr is Linux-only; macOS's own tool does the tree at once.
        subprocess.run(
            ["xattr", "-w", "-r", QUARANTINE, QUARANTINE_VALUE, str(root)],
            check=True,
        )
    else:
        return 0
    return len(files)


def is_marked(path: Path, system: str) -> bool:
    """True while `path` still carries the download mark."""
    if system == "Windows":
        return os.path.exists(str(path) + ZONE_STREAM)
    if system == "Darwin":
        probe = subprocess.run(
            ["xattr", "-p", QUARANTINE, str(path)], capture_output=True, check=False
        )
        return probe.returncode == 0
    return False


def castle_status(port: int) -> dict[str, Any]:
    """The emulated castle's /api/status, as Castle Radio reads it."""
    url = f"http://127.0.0.1:{port}/api/status"
    with urllib.request.urlopen(url, timeout=10) as resp:
        answer: dict[str, Any] = json.loads(resp.read())
    return answer


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def tee(
    cmd: list[str],
    env: Mapping[str, str],
    log: Path,
    cwd: Path | None = None,
    timeout: float = 3600,
) -> tuple[int, str]:
    """Run `cmd` with stdin closed (so install.cmd's closing `pause` and a
    launcher's "press return" do not wait), printing and logging every line;
    (exit code, everything it said). A timeout kills its whole tree."""
    log.parent.mkdir(parents=True, exist_ok=True)
    said: list[str] = []
    started = time.monotonic()
    with log.open("a", encoding="utf-8") as out:
        out.write(f"$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(
            cmd,
            env=dict(env),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **portable_proc.group_kwargs(),
        )
        # A hung step (a prompt nobody will answer) is killed, not waited on.
        killed = threading.Event()

        def kill() -> None:
            killed.set()
            portable_proc.kill_tree(proc)

        timer = threading.Timer(timeout, kill)
        timer.start()
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                print(line, flush=True)
                out.write(line + "\n")
                said.append(line)
            code = proc.wait()
        finally:
            timer.cancel()
            proc.stdout.close()
        took = time.monotonic() - started
        out.write(f"[exit {code} after {took:.1f} s]\n")
    if killed.is_set():
        raise SystemExit(f"timed out after {timeout:.0f} s: {' '.join(cmd)}")
    return code, "\n".join(said)


class Child:
    """A server the test starts and must stop with everything it started:
    output to `log`, its own process group, killed as a tree."""

    def __init__(
        self, cmd: list[str], env: Mapping[str, str], log: Path, cwd: Path | None = None
    ) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log = log
        self._out = log.open("ab")
        self.proc = subprocess.Popen(
            cmd,
            env=dict(env),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=self._out,
            stderr=subprocess.STDOUT,
            **portable_proc.group_kwargs(),
        )

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        portable_proc.kill_tree(self.proc)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        self._out.close()
        # Windows holds a dead process's files a moment longer; the
        # uninstall that follows must not meet a lock.
        time.sleep(2 if sys.platform == "win32" else 0.2)

    def tail(self, lines: int = 40) -> str:
        text = self.log.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])


def wait_for(
    check: Callable[[], bool],
    seconds: float,
    what: str,
    alive: Callable[[], bool] = lambda: True,
    every: float = 0.25,
) -> None:
    """Poll `check` until it is true; SystemExit naming `what` otherwise —
    at once when `alive` says the thing being waited on has died."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        if not alive():
            raise SystemExit(f"{what}: the process exited first")
        time.sleep(every)
    raise SystemExit(f"{what}: not after {seconds:.0f} s")


def read_state(work: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((work / "smoke.json").read_text(encoding="utf-8"))
    return data


def write_state(work: Path, state: Mapping[str, Any]) -> None:
    (work / "smoke.json").write_text(
        json.dumps(dict(state), indent=2) + "\n", encoding="utf-8"
    )
