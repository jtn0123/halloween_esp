"""Which releases an owner is offered: stable ones, unless they opted in.

The buyer's channel is stable only. GitHub's `releases/latest` never names a
pre-release (the release workflow marks every `vX.Y.Z-suffix` tag as one),
and that is the one API call each updater makes by default: the installer's
`--update` (tools/desktop_lifecycle.py), the launcher's daily notice
(tools/desktop_launch.py), the castle's firmware update
(tools/castle_update.py), and — through `releases/latest/download/
latest.json` — the desktop app's own updater (desktop/src-tauri/src/
updater.rs).

The opt-in is hidden on purpose, with no switch on any page:
`"prerelease": true` in the app's settings.json, or CASTLE_PRERELEASE=1 in
the environment. The apps hand the setting to every child as that variable
(tools/desktop_env.py, desktop/src-tauri/src/runtime.rs), so one owner's
servers and tools all answer alike. Opted in, the newest release of EITHER
kind wins — one list call in place of `latest` — and the desktop app asks
Castle Radio for that release's latest.json (`/radio/app/release`) rather
than deciding a second time. desktop/src-tauri/src/channel.rs holds the
same tag rule for the check it makes on what it is handed;
tests/test_release_channel.py walks both over one table.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

import desktop_release as rel

ENV = "CASTLE_PRERELEASE"
SETTING = "prerelease"
API_LIST = f"https://api.github.com/repos/{rel.REPO}/releases?per_page=30"
#: Where an app release's updater manifest lives, by tag.
LATEST_JSON = f"https://github.com/{rel.REPO}/releases/download/{{tag}}/latest.json"
STABLE_LATEST_JSON = (
    f"https://github.com/{rel.REPO}/releases/latest/download/latest.json"
)
_YES = frozenset({"1", "true", "yes", "on"})
#: release_assets.TAG_RE: vMAJOR.MINOR.PATCH, a `-suffix` making it a
#: pre-release. ASCII digits only, as desktop/src-tauri/src/release.rs reads it.
_TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?")

Key = tuple[tuple[int, int, int], int, tuple[tuple[int, int, str], ...]]


def opted_in(
    settings: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """CASTLE_PRERELEASE when it is set to anything, else the setting."""
    env = (os.environ if environ is None else environ).get(ENV, "").strip()
    if env:
        return env.lower() in _YES
    return settings is not None and settings.get(SETTING) is True


def version_key(tag: str) -> Key | None:
    """A release tag in semver precedence order — a pre-release sorts below
    its release, numeric identifiers numerically and below words — or None
    for anything that is not a release tag."""
    m = _TAG.fullmatch(tag.strip())
    if m is None:
        return None
    core = (int(m[1]), int(m[2]), int(m[3]))
    if m[4] is None:
        return core, 1, ()
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in m[4].split("."))
    return core, 0, ids


def accepts(tag: str, prerelease: bool) -> bool:
    """Whether this channel may offer `tag` at all."""
    key = version_key(tag)
    return key is not None and (prerelease or key[1] == 1)


def is_newer(candidate: str, installed: str, prerelease: bool = False) -> bool:
    """Should an update move from `installed` to `candidate`? Never to a tag
    the channel does not take, never down; an installed version that is no
    tag (a from-source install) loses to any release the channel takes."""
    new = version_key(candidate)
    if new is None or not accepts(candidate, prerelease):
        return False
    old = version_key(installed)
    return old is None or new > old


def newest(fetch: rel.Fetch, prerelease: bool = False) -> rel.Release:
    """The release this channel offers. ONE API call either way."""
    if not prerelease:
        return rel.find_release(fetch)
    try:
        rows = json.loads(fetch(API_LIST))
    except OSError as exc:
        raise rel.ReleaseError(f"cannot reach GitHub ({exc})") from exc
    except ValueError as exc:
        raise rel.ReleaseError(
            f"GitHub answered something that is not JSON: {exc}"
        ) from exc
    best: dict[str, Any] | None = None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("draft"):
            continue
        key = version_key(str(row.get("tag_name") or ""))
        if key is not None and (best is None or key > best["_key"]):
            best = {**row, "_key": key}
    if best is None:
        raise rel.ReleaseError("GitHub lists no release")
    best.pop("_key")
    return rel.release_from_api(json.dumps(best).encode(), prerelease=True)


def app_manifest(release: rel.Release) -> str:
    """The desktop updater's latest.json for `release`."""
    return LATEST_JSON.format(tag=release.tag)
