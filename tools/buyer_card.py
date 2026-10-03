#!/usr/bin/env python3
"""A sold castle's SD card, written into a directory — no castle needed.

    tools/buyer_card.py [--tag v0.2.0] [--date 2026-10-03]
    make buyer-card [TAG=v0.2.0]

It writes `buyer-card/` in the build root: this checkout's (gitignored), or
CASTLE_BUILD's when that is set (tools/build_paths.py). No path comes from
the command line, as with the repo's other tools. That directory is the
whole card. Copy its contents onto the root of a FAT32 card:

    scenes/show.man                   the shipped show's manifest
    scenes/<id>.cue                   each scene's light show
    scenes/NN_<id>.mp3                each scene's sound, at card_bitrate
    licenses/THIRD-PARTY-NOTICES.txt  the firmware image's notices
    licenses/SOURCE-OFFER.txt         the GPLv3 §6 b) written offer

Nothing else goes on it. There are no songs, because no song ships
(docs/LICENSING.md). There is no `site/` either, so the castle serves its
built-in owner page at `/`, and that page is the one docs/OWNER-GUIDE.md
describes. The owner page links the two licence files at its foot (v5.77,
firmware/sd_web_owner.h).

THE SHOW is scenes/shipped.yaml (tools/shipped_show.py): the yard's show
minus every scene that needs an imported song. It is rendered and generated
in a sandbox. CASTLE_SCENES is a scratch copy, CASTLE_BUILD a scratch
directory, CASTLE_TRACKS an empty one, and CASTLE_HOST is empty. The same
render_audio.py and gen_esphome.py that `make publish` runs make the files,
so the bytes are the ones `sd_sync scenes` would push. The real show, the
library and the castle are never touched.

It REFUSES, and writes nothing, when:
  - a scene in the show needs a track;
  - the build made anything this list does not expect;
  - a format needs a newer castle than this tree's firmware;
  - the target holds files that are not an earlier card's.

Hidden entries (a fresh card's `.fseventsd`, `System Volume Information`)
are left alone. An earlier card's `scenes/` and `licenses/` are replaced.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import build_paths as bp
import fw_formats
import release_assets
import scene_manifest
import shipped_show
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
SHOW = shipped_show.SHIPPED
#: Where the card is written: the build root's, never a typed path.
CARD_DIR = bp.BUILD / "buyer-card"
NOTICES = ROOT / "licenses" / "THIRD-PARTY-NOTICES-firmware.txt"
REPO = "https://github.com/jtn0123/halloween_esp"
FLASHER = "https://jtn0123.github.io/halloween_esp/"
#: The card names the owner page links (firmware/sd_web_owner.h). Fixed:
#: tests/test_buyer_card.py holds the page and this list together.
CARD_NOTICES = "licenses/THIRD-PARTY-NOTICES.txt"
CARD_OFFER = "licenses/SOURCE-OFFER.txt"
#: The top-level entries a card made here holds. Anything else is foreign.
OURS = ("scenes", "licenses")
#: GPLv3 §6 b): "valid for at least three years".
OFFER_YEARS = 3
#: The sandbox's environment knobs, all set here; a shell's own are dropped.
KNOBS = ("CASTLE_SCENES", "CASTLE_BUILD", "CASTLE_TRACKS", "CASTLE_HOST")


def years_later(day: dt.date, years: int) -> dt.date:
    """`day` plus whole years. A 29 February with no twin becomes the 28th."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def source_offer(version: str, made: dt.date, tag: str | None = None) -> str:
    """The written offer, as plain text for the card.

    It follows GPLv3 §6 b) as docs/LICENSING.md quotes it. The offer lasts
    at least three years, and for as long as parts or support are offered.
    It is made to anyone who has the object code. It offers both (1) a copy
    on a durable medium at cost and (2) free network access. The section's
    closing paragraph adds the User Product clause, so the offer gives the
    Installation Information too.
    """
    until = years_later(made, OFFER_YEARS)
    release = (
        f"the release tagged {tag}:\n          {REPO}/releases/tag/{tag}"
        if tag
        else f"the release that carries firmware {version}, from:\n"
        f"          {REPO}/releases"
    )
    return f"""\
WRITTEN OFFER FOR SOURCE CODE
=============================

Halloween Castle firmware {version}. The castle's page shows its version.

This offer was made on {made.isoformat()}. It is valid until
{until.isoformat()}, or for as long as spare parts or customer support are
offered for this castle, whichever is later.

The castle's firmware is built from free software. Parts of it are licensed
under the GNU General Public License version 3: ESPHome's C++ runtime and
esp-audio-libs. THIRD-PARTY-NOTICES.txt, in this folder, names every
component in the firmware with its licence and the address of its source.

As section 6 b) of the GPLv3 requires, this is a written offer to anyone
who possesses the castle's firmware, valid for the period above, to give
the Corresponding Source of that firmware in either of these ways:

  (1) Access to copy it from a network server, at no charge:
      - this project's source code (the firmware configuration, its C++
        and the Makefile that builds it), in the repository
          {REPO}
        at {release}
        The release page's "Source code" zip is that tag's whole tree.
      - each third-party component at the version built, from the source
        address given for it in THIRD-PARTY-NOTICES.txt.

  (2) Or, on request, a copy of all of it on a durable physical medium
      customarily used for software interchange, for no more than the
      reasonable cost of physically making that copy. To ask, open an
      issue at:
          {REPO}/issues

INSTALLATION INFORMATION. To build a modified firmware image, run
`make build-buyer` in that source tree; docs/RELEASING.md in the source
explains the build. To install the image, connect the castle to a computer
with a USB-C cable and use esptool or the setup page:
    {FLASHER}
Installing over USB needs no key and no signature, and the castle does not
use secure boot. Installing over the network (PUT /api/ota) asks for the
castle key only if the owner has set one. A USB install that erases the
castle clears that key, with the Wi-Fi and the settings.
"""


def _env(scenes: Path, build: Path, tracks: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in KNOBS}
    env.update(
        CASTLE_SCENES=str(scenes),
        CASTLE_BUILD=str(build),
        CASTLE_TRACKS=str(tracks),
        CASTLE_HOST="",
    )
    return env


def _run(script: str, env: dict[str, str]) -> None:
    r = subprocess.run(
        [sys.executable, str(TOOLS / script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"buyer card: {script} failed:\n{r.stdout}{r.stderr}")


def refuse_tracks(doc: dict) -> None:
    """No scene that needs a song reaches a card that is sold."""
    bad = {
        str(s["id"]): why for s in doc["scenes"] if (why := shipped_show.needs_track(s))
    }
    if bad:
        lines = [f"  {sid}: {', '.join(why)}" for sid, why in bad.items()]
        raise SystemExit(
            "buyer card: these scenes need an imported song, and no song "
            "ships:\n" + "\n".join(lines)
        )


def expected_scene_files(doc: dict) -> set[str]:
    """Every file the card's scenes/ must hold, and nothing more."""
    out = {"show.man"}
    for i, s in enumerate(doc["scenes"], start=1):
        out |= {f"{s['id']}.cue", f"{scene_manifest.audio_token(i, str(s['id']))}.mp3"}
    return out


def render_show(show: Path, into: Path) -> list[str]:
    """Render and generate `show` in a sandbox, then copy the card's scenes/
    into `into`. Returns the ids, in order."""
    doc = yaml.safe_load(show.read_text(encoding="utf-8"))
    refuse_tracks(doc)
    with tempfile.TemporaryDirectory(prefix="castle-buyer-card-") as td:
        tmp = Path(td)
        (tmp / "tracks").mkdir()
        shutil.copyfile(show, tmp / "scenes.yaml")
        env = _env(tmp / "scenes.yaml", tmp / "build", tmp / "tracks")
        _run("render_audio.py", env)
        _run("gen_esphome.py", env)
        audio = tmp / "build" / "audio"
        made = audio / "card" / "scenes"
        want = expected_scene_files(doc)
        into.mkdir(parents=True)
        for name in sorted(want):
            # The card_bitrate copy when render_audio made one (audio/card/),
            # else the only render — sd_sync scenes' own preference.
            src = next(
                (
                    p
                    for p in (made / name, audio / "card" / name, audio / name)
                    if p.is_file()
                ),
                None,
            )
            if src is None:
                raise SystemExit(f"buyer card: the build made no {name}")
            shutil.copyfile(src, into / name)
        extra = {p.name for p in made.iterdir()} - want
        if extra:
            raise SystemExit(
                f"buyer card: unexpected files in the show: {sorted(extra)}"
            )
    ids = scene_manifest.scene_ids(into / "show.man")
    if ids != [str(s["id"]) for s in doc["scenes"]]:
        raise SystemExit(f"buyer card: show.man names {ids}, the show is not that")
    why = fw_formats.refusal(
        {"version": fw_formats.this_firmware()}, fw_formats.heads(into.iterdir())
    )
    if why:
        raise SystemExit(f"buyer card: {why}")
    return ids


def check_target(out: Path) -> None:
    """Refuse a target that holds anything but an earlier card."""
    if not out.exists():
        return
    if not out.is_dir():
        raise SystemExit(f"buyer card: {out} is not a directory")
    foreign = sorted(
        p.name
        for p in out.iterdir()
        if p.name not in OURS
        and not p.name.startswith(".")
        and p.name != "System Volume Information"
    )
    if foreign:
        raise SystemExit(
            f"buyer card: {out} already holds {', '.join(foreign)} — "
            "name an empty directory, a fresh card or an earlier buyer card"
        )


def build(
    out: Path,
    show: Path = SHOW,
    tag: str | None = None,
    made: dt.date | None = None,
    notices: Path = NOTICES,
) -> list[str]:
    """Write the card into `out`; return its scene ids."""
    check_target(out)
    made = made or dt.datetime.now(dt.UTC).astimezone().date()
    version = fw_formats.this_firmware()
    with tempfile.TemporaryDirectory(prefix="castle-card-stage-") as td:
        stage = Path(td)
        ids = render_show(show, stage / "scenes")
        lic = stage / "licenses"
        lic.mkdir()
        shutil.copyfile(notices, stage / CARD_NOTICES)
        (stage / CARD_OFFER).write_text(
            source_offer(version, made, tag), encoding="utf-8", newline="\n"
        )
        out.mkdir(parents=True, exist_ok=True)
        for name in OURS:
            if (out / name).exists():
                shutil.rmtree(out / name)
            shutil.copytree(stage / name, out / name)
    return ids


#: What a release tag's `-suffix` may hold (release_assets.TAG_RE).
_SUFFIX = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.-"


def release_tag(text: str) -> str:
    """--tag as the release workflow takes one (release_assets.TAG_RE),
    rebuilt from its parts: the numbers as numbers, and each suffix
    character as _SUFFIX's own, so no byte of the typed text reaches the
    offer. main() calls this itself rather than as argparse's `type=`,
    which SonarCloud does not follow (its path findings on #77)."""
    m = release_assets.TAG_RE.match(text)
    if m is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a release tag (vMAJOR.MINOR.PATCH[-suffix])"
        )
    major, minor, patch = (int(g) for g in m.group(1, 2, 3))
    suffix = "".join(next(a for a in _SUFFIX if a == c) for c in m.group(4) or "")
    return f"v{major}.{minor}.{patch}{suffix}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--tag", help="the release tag the castle's firmware is from")
    ap.add_argument("--date", type=dt.date.fromisoformat, help="the offer's date")
    args = ap.parse_args(argv)
    try:
        tag = release_tag(args.tag) if args.tag else None
    except argparse.ArgumentTypeError as exc:
        ap.error(f"argument --tag: {exc}")
    ids = build(CARD_DIR, SHOW, tag, args.date)
    files = sorted(p for p in CARD_DIR.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in files)
    print(f"buyer card: {bp.rel(CARD_DIR)} — {len(files)} files, {size // 1024} KB")
    print(f"  show: {', '.join(ids)}")
    print(f"  {CARD_NOTICES} + {CARD_OFFER}; no songs, no site (/ is the owner page)")
    print("  copy the directory's contents onto the root of a FAT32 card")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
