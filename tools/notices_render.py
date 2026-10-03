"""Turn the notices tables into the plain-text files that ship.

One file per artifact whose third-party content differs (ARTIFACTS); each
is complete on its own — every component with its licence, copyright lines
and source, then every licence text it needs, once. Plain text, not
Markdown: the file is opened by whoever finds it in a zip, an app bundle or
a release page, with whatever their computer opens .txt files with.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable
from dataclasses import dataclass

import desktop_bundle
import notices_desktop
import notices_firmware
from notices_external import EXTERNAL
from notices_model import ROOT, Component, chosen, licence_text, texts_for

REPO = "https://github.com/jtn0123/halloween_esp"
WIDTH = 78
OURS = "the Halloween Castle project's own work (copyright jtn0123)"


@dataclass(frozen=True)
class Artifact:
    key: str
    path: str  # repo-relative
    title: str
    intro: tuple[str, ...]
    external: bool = False


ARTIFACTS: dict[str, Artifact] = {
    a.key: a
    for a in (
        Artifact(
            "firmware",
            "licenses/THIRD-PARTY-NOTICES-firmware.txt",
            "the castle's firmware image",
            (
                (
                    "This file accompanies the castle's firmware image, "
                    "castle-fw-feather-s3-4m2p-<release>.factory.bin and .ota.bin, "
                    "on the GitHub release and on the web flasher that installs it."
                ),
                (
                    "The image is the castle's own configuration and C++ "
                    f"(firmware/ in {REPO}), {OURS}, compiled together with the "
                    "components below. It is built by `make build-buyer` from the "
                    "release's tag of that repository, with the versions listed: "
                    f"esphome {notices_firmware.ESPHOME} from PyPI fetches ESP-IDF "
                    f"{notices_firmware.IDF_VERSION}, the xtensa-esp-elf "
                    f"{notices_firmware.TOOLCHAIN} toolchain and the managed "
                    "components, each from the source address shown with it."
                ),
                (
                    "The web flasher page loads its flashing tool, esp-web-tools "
                    "(Apache-2.0), from unpkg.com into your browser; the page "
                    "does not carry it."
                ),
            ),
        ),
        Artifact(
            "desktop",
            "licenses/THIRD-PARTY-NOTICES-desktop.txt",
            "Castle Tools, the desktop app",
            (
                (
                    "This file is inside Castle Tools, the desktop app for macOS "
                    "and Windows, and so inside its installers and updates."
                ),
                (
                    f"The app and the castle-core programs it carries (studio, "
                    f"analyze_track, scene_render) are {OURS}. They are built "
                    "with the Rust crates below, from crates.io, and the Rust "
                    'standard library. Entries marked "macos only" or "windows '
                    "only\" are in that platform's app alone."
                ),
                (
                    "The app also carries a copy of the project's own source "
                    "tree, which its first launch sets Castle Radio up from, "
                    "and Astral's uv (listed below). That first launch uses uv "
                    "to download Python 3.13, and downloads the other programs "
                    "and data named at the end of this file, onto your "
                    "computer. None of them is part of the app."
                ),
            ),
            external=True,
        ),
        Artifact(
            "castle-core",
            "licenses/THIRD-PARTY-NOTICES-castle-core.txt",
            "castle-core (studio, analyze_track, scene_render)",
            (
                "This file is inside each castle-core-<target>-<release>.zip.",
                (
                    f"castle-core is {OURS}. It has no third-party crate "
                    "dependencies (core/Cargo.lock lists only castle-core itself); "
                    "what it links that someone else wrote is the Rust standard "
                    "library."
                ),
            ),
        ),
        Artifact(
            "source",
            "THIRD-PARTY-NOTICES.txt",
            "the source tree and the Castle Tools installer",
            (
                (
                    "This file is at the top of the castle's source tree, and so "
                    'in every release\'s "Source code" zip — the bundle the '
                    "Castle Tools installer (installer/) unpacks."
                ),
                (
                    f"Everything in the tree is {OURS}; it carries no third-party "
                    "source code. The castle-core programs the installer adds "
                    "from the release are covered below. The other shipped "
                    "artifacts carry their own notices, also in this tree: "
                    "licenses/THIRD-PARTY-NOTICES-firmware.txt (the firmware "
                    "image), licenses/THIRD-PARTY-NOTICES-desktop.txt (the "
                    "desktop app) and licenses/THIRD-PARTY-NOTICES-castle-core.txt."
                ),
            ),
            external=True,
        ),
    )
}


def components(key: str) -> list[Component]:
    """What one artifact carries, in the order its notices list it."""
    if key == "firmware":
        return notices_firmware.components()
    if key == "desktop":
        doc = notices_desktop.load()
        std = notices_desktop.rust_std("the stable toolchain of the release build")
        return [
            std,
            *notices_desktop.crates(doc),
            notices_desktop.uv(desktop_bundle.UV_VERSION),
            *notices_desktop.windows_extras(),
        ]
    return [notices_desktop.rust_std(notices_desktop.CORE_RUST)]


def _para(text: str, indent: str = "") -> str:
    return textwrap.fill(
        text,
        WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_on_hyphens=False,
    )


def _heading(text: str, rule: str = "-") -> str:
    return f"{text}\n{rule * len(text)}"


def _component(n: int, c: Component) -> list[str]:
    terms = " AND ".join(chosen(c.license))
    lic = c.license if terms == c.license else f"{c.license} (complied with: {terms})"
    numeric = c.version[:1].isdigit() or c.version.startswith("V")
    head = f"{c.name} {c.version}" if numeric else f"{c.name} ({c.version})"
    out = [f"{n}. {head}", f"   Licence: {lic}"]
    if c.source:
        out.append(f"   Source:  {c.source}")
    out += [f"   {line}" for line in c.copyright]
    if c.note:
        out.append(_para(f"Note: {c.note}", "   "))
    if c.override:
        out.append(_para(f"Why this licence ships: {c.override}", "   "))
    return [*out, ""]


def _ordered(items: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _external() -> list[str]:
    out = [
        _heading("Not included: what your own computer downloads"),
        "",
        _para(
            "The programs and data below are used by the castle's software "
            "but are not part of any file this project publishes. The "
            "installer or the app fetches them from their publishers, on your "
            "machine, under the publisher's terms:"
        ),
        "",
    ]
    for e in EXTERNAL:
        out.extend(
            [
                f"* {e.name} — {e.version}",
                _para(f"Terms: {e.terms}", "  "),
                _para(f"How it arrives: {e.obtained}", "  "),
                *([_para(e.note, "  ")] if e.note else []),
                "",
            ]
        )
    return out


def render(key: str) -> str:
    """The notices file for one artifact."""
    art = ARTIFACTS[key]
    comps = components(key)
    title = f"THIRD-PARTY NOTICES — {art.title}"
    out = [title, "=" * len(title), ""]
    for para in art.intro:
        out += [_para(para), ""]
    out += [
        _para(
            "Generated by tools/third_party_notices.py from the tables in "
            "tools/notices_*.py and licenses/; edit those and regenerate, "
            "not this file."
        ),
        "",
        _heading("Components"),
        "",
    ]
    for n, c in enumerate(comps, 1):
        out += _component(n, c)
    if key == "desktop":
        for who, text in notices_desktop.notice_texts(notices_desktop.load()).items():
            out += [_heading(f"NOTICE file of {who}"), "", text.rstrip(), ""]
    generic = _ordered(
        t for c in comps for term in chosen(c.license) for t in texts_for(term)
    )
    own = _ordered(t for c in comps for t in c.texts)
    out += [
        _heading("Licence texts"),
        "",
        _para(
            "Each licence appears once. A generic text's own copyright line "
            "is a template: the holders are the copyright lines listed with "
            "each component above."
        ),
        "",
    ]
    for rel in generic:
        out += [f"=== {rel.removeprefix('texts/').removesuffix('.txt')} ===", ""]
        out += [licence_text(rel).rstrip(), ""]
    if own:
        out += [_heading("Licence and notice files of individual components"), ""]
        for rel in own:
            out += [
                f"=== {rel.removeprefix('components/').removesuffix('.txt')} ===",
                "",
            ]
            out += [licence_text(rel).rstrip(), ""]
    if art.external:
        out += _external()
    return "\n".join(out).rstrip() + "\n"


def outputs() -> dict[str, str]:
    """Repo-relative path -> the text that belongs there."""
    return {a.path: render(a.key) for a in ARTIFACTS.values()}


def stale() -> list[str]:
    """The committed notices files that differ from what the tables make."""
    errors = []
    for rel, text in outputs().items():
        path = ROOT / rel
        if not path.is_file():
            errors.append(
                f"{rel} is missing — run: tools/third_party_notices.py generate"
            )
        elif path.read_text(encoding="utf-8") != text:
            errors.append(
                f"{rel} is stale — run: tools/third_party_notices.py generate"
            )
    return errors
