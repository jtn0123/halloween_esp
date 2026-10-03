"""Copy diagnostics — what someone helping with this Castle Radio would ask
for, as one text the owner copies or saves (castle-help.js).

Nothing is sent anywhere. The page asks this server; this server asks the
castle on the LAN the four questions the castle's own "Report a problem"
asks (firmware/sd_web_owner.h, v5.75) and hands the text back to the page.
The text keeps that report's shape: its header, its `== /api/... ==`
sections in its order, "(no answer)" where the castle said nothing. After
them come this computer's half: the app's version, the tools it found, the
recent imports and syncs, and the tail of the desktop app's log.

Two things never leave in it:

- a castle key. CASTLE_KEY and every key the devices store holds are
  blanked wherever they appear, raw or URL-quoted, and so is whatever
  follows an X-Castle-Key header or a `new=`/`key=` query. No castle
  endpoint quoted here carries one; the app log is the reason for the net.
  A key that is a common word blanks that word too — uglier, never leakier.
- a folder name. An absolute path on this computer (a home directory names
  its owner) is cut to its last part, the file name.
"""

import json
import os
import platform
import re
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import radio_env  # the sandbox first, then tools/ on the path

# isort: split
import castle_tools_status
import device_bridge
import hosts
import radio_jobs
import remote_library

#: The castle's own report, in its order (sd_web_owner.h report()).
CASTLE_PATHS = ("/api/status", "/api/health", "/api/events", "/api/bootlog")
NO_ANSWER = "(no answer)"
REMOVED = "[castle key removed]"
TIMEOUT = 4
#: What one castle answer may add: the event ring and the boot log are a
#: few KB on the board; anything bigger is not what they are.
ANSWER_CAP = 64 * 1024
JOB_LIMIT = 20
LOG_LINES = 200
#: One name in a path, and the rest of a path after its first part. A
#: folder with spaces in it ("Application Support", "Jo Smith") counts when
#: each later word is capitalised or a number and a separator follows it, so
#: the words after a file name ("python3 then 3/4 done") stay words.
_NAME = r"[^\\/\s'\"<>|;,()\[\]]+"
_ENDS = rf"(?!{_NAME})"
_REST = rf"(?:[\\/](?:{_NAME}(?: [A-Z0-9]{_NAME})*(?=[\\/])|{_NAME})?)*"
#: Where a path on a Mac, Linux or Windows computer starts. A castle route
#: (/api/…, /radio/…) or a card path (/sd/…) is none of these and stays.
_POSIX = re.compile(
    r"(?<![\w.:/~])(?:~|/(?:Users|home|private|var|tmp|Volumes|opt|"
    r"Applications|Library|System|root|mnt|usr|nix|snap|etc))" + _ENDS + _REST
)
_WINDOWS = re.compile(r"(?<!\w)[A-Za-z]:(?=[\\/])" + _REST)
_HEADER = re.compile(
    r"(X-Castle-Key[\"']?\s*[:=,]\s*[\"']?)[^\s\"',}\]]+", re.IGNORECASE
)
_QUERY = re.compile(r"([?&](?:new|key)=)[^&\s\"'#]+")


def _basename(match: re.Match[str]) -> str:
    parts = [p for p in re.split(r"[\\/]+", match.group(0)) if p]
    return parts[-1] if parts else match.group(0)


def _roots() -> list[str]:
    """This computer's own folders, longest first, so one with a space in
    it (Application Support, a home called "Jo Smith") goes whole."""
    found = {
        str(radio_env.DATA),
        str(radio_env.ROOT),
        str(Path.home()),
        tempfile.gettempdir(),
    }
    log = os.environ.get("CASTLE_APP_LOG", "")
    if log:
        found.add(str(Path(log).parent))
    return sorted((r for r in found if len(r) > 1), key=len, reverse=True)


def _keys() -> set[str]:
    """Every key this computer could hold: CASTLE_KEY and the store's."""
    keys = {os.environ.get("CASTLE_KEY", "").strip()}
    try:
        doc = tomllib.loads(hosts.devices_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        doc = {}
    for table in doc.values():
        if isinstance(table, dict) and isinstance(table.get("key"), str):
            keys.add(table["key"].strip())
    keys.discard("")
    return keys | {urllib.parse.quote(k, safe="") for k in keys}


def scrub(text: str) -> str:
    """Paths cut to file names, then every castle key blanked."""
    for root in _roots():
        text = re.sub(re.escape(root) + _ENDS + _REST, _basename, text)
    text = _WINDOWS.sub(_basename, _POSIX.sub(_basename, text))
    text = _QUERY.sub(rf"\1{REMOVED}", _HEADER.sub(rf"\1{REMOVED}", text))
    for key in sorted(_keys(), key=len, reverse=True):
        text = text.replace(key, REMOVED)
    return text


def _ask(host: str, path: str) -> str:
    """One castle answer as the owner page quotes it: the body, "HTTP n: "
    before a refusal's, NO_ANSWER for silence. Read-only routes, so no key
    goes with the request."""
    try:
        with urllib.request.urlopen(
            hosts.castle_url(host, path), timeout=TIMEOUT
        ) as response:
            body: bytes = response.read(ANSWER_CAP)
            return body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read(ANSWER_CAP).decode('utf-8', 'replace')}"
    except (OSError, ValueError):
        return NO_ANSWER


def castle_answers() -> tuple[str, dict[str, str]]:
    """(host or the reason there is none, path -> answer). A castle that is
    silent on /api/status is not asked the other three: four timeouts in a
    row are a minute the owner spends looking at a button."""
    try:
        host = device_bridge.castle()
    except OSError as exc:
        return str(exc), dict.fromkeys(CASTLE_PATHS, NO_ANSWER)
    answers = {CASTLE_PATHS[0]: _ask(host, CASTLE_PATHS[0])}
    for path in CASTLE_PATHS[1:]:
        silent = answers[CASTLE_PATHS[0]] == NO_ANSWER
        answers[path] = NO_ANSWER if silent else _ask(host, path)
    return host, answers


def _json(text: str) -> dict:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def app_version() -> str:
    """The release tag the desktop app was built from (src/supervisor.rs),
    else the one the installer's archive stamped, else this is a checkout —
    shown on the tools card (/radio/tools) and in every diagnostics report,
    docs/SUPPORT.md "Which release is it"."""
    version = os.environ.get("CASTLE_APP_VERSION", "").strip()
    if version:
        return f"Castle Tools {version}"
    try:
        stamped = (radio_env.ROOT / "installer" / "VERSION").read_text(encoding="utf-8")
    except OSError:
        stamped = ""
    stamped = stamped.strip()
    if stamped and not stamped.startswith("$Format"):
        return f"Castle Radio {stamped}"
    return "Castle Radio (a checkout, no release version)"


def _summary(host: str, answers: dict[str, str]) -> list[str]:
    status, health = (_json(answers[p]) for p in CASTLE_PATHS[:2])
    if not status:
        return [f"castle {host} · not answering"]
    line = f"castle {host} · firmware {status.get('version', '?')}"
    if health:
        line += (
            f" · last restart {health.get('last_reset', '?')}"
            f" · switched on {health.get('boots', '?')} times,"
            f" {health.get('crashes', '?')} crashes"
        )
    return [line]


def _tools() -> list[str]:
    checks = castle_tools_status.status().get("checks")
    return [
        f"{c.get('name')}: {'ok' if c.get('ok') else 'MISSING'}"
        f" · {str(c.get('detail', '')).strip()[:120]}"
        for c in (checks if isinstance(checks, list) else [])
        if isinstance(c, dict)
    ]


def _jobs() -> list[str]:
    with radio_jobs.LOCK:
        imports = [dict(j) for j in radio_jobs.JOBS.values()]
    syncs = remote_library.jobs()
    lines = [
        f"import {j.get('id')} · {j.get('title') or j.get('source')}"
        f" · {j.get('phase')}{' · ' + str(j['error']) if j.get('error') else ''}"
        for j in imports[-JOB_LIMIT:]
    ]
    lines += [
        f"sync {j.get('key')} · {j.get('phase')} · {j.get('percent', 0)}%"
        f"{' · ' + str(j['error']) if j.get('error') else ''}"
        for j in syncs[-JOB_LIMIT:]
    ]
    return lines or ["(none since Castle Radio started)"]


def _log_tail() -> list[str]:
    path = os.environ.get("CASTLE_APP_LOG", "")
    if not path:
        return ["(no app log: Castle Radio is not running inside the Castle app)"]
    try:
        with open(path, encoding="utf-8", errors="replace") as log:
            lines = log.read().splitlines()
    except OSError as exc:
        return [f"(the app log could not be read: {exc.strerror})"]
    return lines[-LOG_LINES:] or ["(empty)"]


def report(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    host, answers = castle_answers()
    status = _json(answers[CASTLE_PATHS[0]])
    head = [
        "Castle problem report",
        f"made {now.isoformat()} ({now.astimezone().ctime()})",
        " · ".join(
            f"{label} {status.get(field, '?')}"
            for label, field in (
                ("version", "version"),
                ("board", "board"),
                ("build", "fw_variant"),
            )
        ),
        f"page {app_version()} diagnostics",
        "",
    ]
    castle = [f"== {path} ==\n{answers[path]}\n" for path in CASTLE_PATHS]
    radio = [
        "== Castle Radio ==",
        app_version(),
        f"python {platform.python_version()} · {platform.platform()}",
        *_summary(host, answers),
        "",
        "== tools ==",
        *_tools(),
        "",
        f"== recent jobs (last {JOB_LIMIT} of each) ==",
        *_jobs(),
        "",
        f"== app log (last {LOG_LINES} lines) ==",
        *_log_tail(),
    ]
    return scrub("\n".join([*head, *castle, *radio]) + "\n")


def get_diagnostics(handler, _parsed):
    now = datetime.now(UTC)
    handler.reply(
        {
            "text": report(now),
            "name": f"castle-report-{now.strftime('%Y-%m-%dT%H-%M-%S')}.txt",
        }
    )


GET_ROUTES = {"/radio/diagnostics": get_diagnostics}
