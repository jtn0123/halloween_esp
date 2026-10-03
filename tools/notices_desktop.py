"""The Rust half of the notices: the desktop app's crates, the Rust
standard library both apps link, and what the Windows installer wraps
around the app.

The crate list is an inventory, licenses/desktop-crates.json, written by
`third_party_notices.py refresh` from `cargo metadata` (offline, --locked)
and committed. It holds EVERY package in desktop/src-tauri/Cargo.lock, each
marked with the platforms it is linked into — a normal (not build- or
dev-) dependency reachable from the app, and not a proc-macro, which runs
in the compiler and never reaches the binary. The walk follows cargo's
unified features, so it may list a crate a feature split would leave out;
it never leaves one out. tests/test_third_party_notices.py compares the
inventory with the lockfile, so a dependency added without a refresh fails
before it ships unlisted.

castle-core (core/) has no dependencies at all; the same test holds its
lockfile to that, because the moment it gains one this module must learn
to list them.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path

from notices_model import LICENSES, ROOT, Component, shown

INVENTORY = LICENSES / "desktop-crates.json"
DESKTOP_LOCK = ROOT / "desktop" / "src-tauri" / "Cargo.lock"
CORE_LOCK = ROOT / "core" / "Cargo.lock"
CORE_TOOLCHAIN = ROOT / "core" / "rust-toolchain.toml"
#: The desktop release targets (tools/release_assets.DESKTOP_TARGETS), by
#: the platform name the inventory records.
TARGETS = {"macos": "aarch64-apple-darwin", "windows": "x86_64-pc-windows-msvc"}
#: The toolchain castle-core is pinned to (core/rust-toolchain.toml).
CORE_RUST = "1.88.0"
#: The Tauri CLI that bundles the app (desktop/cli/package.json) and the
#: NSIS it downloads to build the Windows installer — tauri-bundler's
#: NSIS_URL / NSIS_TAURI_UTILS_URL at tauri-cli-v2.12.1:
#: https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.1/crates/tauri-bundler/src/bundle/windows/nsis/mod.rs
TAURI_CLI = "2.12.1"
NSIS = "3.11"
NSIS_TAURI_UTILS = "0.5.3"
#: webview2-com-sys's bundled SDK (its CHANGELOG: "update WebView2 SDK to
#: 1.0.3650.58"); the crate links WebView2LoaderStatic.lib into the app.
WEBVIEW2_SDK = "1.0.3650.58"
WEBVIEW2_CRATE = ("webview2-com-sys", "0.39.1")


def rust_std(version: str) -> Component:
    """The Rust standard library as the release links it (std, core,
    alloc and compiler-builtins), from the toolchain's COPYRIGHT-library."""
    return Component(
        "Rust standard library (std, core, alloc, compiler-builtins)",
        version,
        "(MIT OR Apache-2.0) AND MIT AND Apache-2.0 WITH LLVM-exception AND Unicode-3.0",
        (
            "Copyright The Rust Project Developers (see https://thanks.rust-lang.org)",
            (
                "compiler-builtins: Copyright (c) 2009-2016 by the contributors "
                "listed in LLVM compiler-rt's CREDITS.TXT"
            ),
            "Unicode data: Copyright (c) 1991-2024 Unicode, Inc.",
        ),
        source="https://github.com/rust-lang/rust/tree/master/library",
        note="The toolchain's own file-by-file record of these notices is "
        "share/doc/rust/COPYRIGHT-library.html in every Rust install.",
    )


def uv(version: str) -> Component:
    """Astral's uv, carried in the app as castle/uv/ to set its runtime up on
    the first launch — tools/desktop_bundle.py pins it by version and
    sha256, and that pin is the version given here."""
    return Component(
        "uv",
        version,
        "MIT OR Apache-2.0",
        ("Copyright (c) 2025 Astral Software Inc.",),
        source=f"https://github.com/astral-sh/uv/tree/{version}",
        note="The unmodified release binary from github.com/astral-sh/uv, run "
        "as its own program to install Python 3.13 and the locked packages "
        "on the owner's machine. It is statically linked with Rust crates "
        "under their own permissive licences, which its release does not "
        "list; docs/LICENSING.md tracks that as an open decision.",
    )


def windows_extras() -> list[Component]:
    """What the Windows build adds that is not a crate: the WebView2 loader
    linked into the app, and the NSIS installer the app is wrapped in."""
    return [
        Component(
            "Microsoft Edge WebView2 SDK loader (WebView2LoaderStatic.lib)",
            WEBVIEW2_SDK,
            "BSD-3-Clause",
            ("Copyright (C) Microsoft Corporation. All rights reserved.",),
            source=f"https://www.nuget.org/packages/Microsoft.Web.WebView2/{WEBVIEW2_SDK}",
            texts=("components/webview2-LICENSE.txt",),
            note=f"Linked into the Windows app by {' '.join(WEBVIEW2_CRATE)}.",
        ),
        Component(
            "NSIS (the Windows installer's runtime, plugins and LZMA decompressor)",
            NSIS,
            "LicenseRef-NSIS",
            ("Copyright (C) 1999-2025 Contributors",),
            source=f"https://github.com/kichik/nsis/tree/v{NSIS.replace('.', '')}",
            texts=("components/nsis-COPYING.txt",),
            note="Only in the Windows installer (…-setup.exe), not in the app "
            "it installs. Unmodified NSIS; its source is at the address above.",
        ),
        Component(
            "nsis_tauri_utils (NSIS plugin)",
            NSIS_TAURI_UTILS,
            "MIT OR Apache-2.0",
            (
                "Copyright (c) 2019 - 2022 Tauri Programme within The Commons Conservancy",
            ),
            source="https://github.com/tauri-apps/nsis-tauri-utils",
            note="Only in the Windows installer.",
        ),
    ]


# ── The crate inventory ───────────────────────────────────────────────
_LICENCE_FILE = re.compile(
    r"^(licen[cs]e|copying|copyright|notice|unlicense)", re.IGNORECASE
)
_COPYRIGHT = re.compile(r"^(copyright\b|\(c\)|©)", re.IGNORECASE)
_TEMPLATE = re.compile(
    r"copyright (notice|license|owner|holder|and|statement|protection|law|\[|<|\{)"
    r"|^copyright\W*$"
    r"|\[yyyy\]|<year>|\{yyyy\}|\[year\]",
    re.IGNORECASE,
)


def copyright_lines(texts: Iterable[str]) -> list[str]:
    """The copyright statements in a package's licence files, in order, once
    each — not the licence's own template lines."""
    seen: list[str] = []
    for text in texts:
        for raw in text.splitlines():
            line = " ".join(raw.strip().split())
            if not _COPYRIGHT.match(line) or _TEMPLATE.search(line) or line in seen:
                continue
            # "(c)" also opens a list item — Apache-2.0 section 4(c) — so a
            # line that starts with it must carry a year to count.
            if line[0] == "(" and not re.search(r"\b(19|20)\d\d\b", line):
                continue
            seen.append(line)
    return seen


def linked(meta: dict) -> set[str]:
    """Package ids a `cargo metadata --filter-platform` graph links into its
    root: normal edges only, proc-macros pruned."""
    pkgs = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen: set[str] = set()
    stack = [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for dep in nodes[pid]["deps"]:
            if all(k["kind"] is not None for k in dep["dep_kinds"]):
                continue
            target_kinds = {k for t in pkgs[dep["pkg"]]["targets"] for k in t["kind"]}
            if "proc-macro" not in target_kinds:
                stack.append(dep["pkg"])
    seen.discard(root)
    return seen


def _entry(pkg: dict, platforms: list[str], read: Callable[[Path], str]) -> dict:
    name, version = pkg["name"], pkg["version"]
    entry: dict = {"name": name, "version": version, "linked": platforms}
    if not platforms:
        return entry
    folder = Path(pkg["manifest_path"]).parent
    files = sorted(
        p for p in folder.iterdir() if p.is_file() and _LICENCE_FILE.match(p.name)
    )
    texts = {p.name: read(p) for p in files}
    lines = copyright_lines(texts.values())
    if not lines:
        authors = ", ".join(a.split(" <")[0] for a in pkg.get("authors") or [])
        lines = [
            "No copyright line in the package; authors per Cargo.toml: " + authors
            if authors
            else "No copyright line or author in the package"
        ]
    license_ = pkg.get("license") or f"LicenseRef-file:{pkg.get('license_file')}"
    entry.update(
        license=license_,
        copyright=lines,
        source=f"https://crates.io/crates/{name}/{version}",
    )
    notices = [t for n, t in texts.items() if n.lower().startswith("notice")]
    if notices:
        entry["notice"] = "\n".join(notices)
    return entry


def inventory(
    lock_packages: set[tuple[str, str]],
    metas: dict[str, dict],
    read: Callable[[Path], str] | None = None,
) -> dict:
    """The inventory document: every lockfile package, with what each
    platform's metadata graph links. METAS maps platform -> metadata."""
    reader = read or (lambda p: p.read_text(encoding="utf-8", errors="replace"))
    by_key: dict[tuple[str, str], dict] = {}
    platforms: dict[tuple[str, str], list[str]] = {}
    ours = ("", "")
    for platform, meta in sorted(metas.items()):
        pkgs = {p["id"]: p for p in meta["packages"]}
        root = pkgs[meta["resolve"]["root"]]
        ours = (root["name"], root["version"])
        for pid in linked(meta):
            pkg = pkgs[pid]
            key = (pkg["name"], pkg["version"])
            by_key[key] = pkg
            platforms.setdefault(key, []).append(platform)
    out = []
    for key in sorted(lock_packages):
        if key == ours:
            continue
        pkg = by_key.get(key, {"name": key[0], "version": key[1]})
        out.append(_entry(pkg, platforms.get(key, []), reader))
    return {
        "generated_by": "tools/third_party_notices.py refresh (cargo metadata)",
        "app": {"name": ours[0], "version": ours[1]},
        "targets": TARGETS,
        "packages": out,
    }


def lock_packages(lock: Path) -> set[tuple[str, str]]:
    """(name, version) of every [[package]] in a Cargo.lock."""
    doc = tomllib.loads(lock.read_text(encoding="utf-8"))
    return {(p["name"], p["version"]) for p in doc.get("package", [])}


def cargo_metadata(manifest_dir: Path, target: str) -> dict:
    """`cargo metadata` for one target, from the local registry cache only
    (`cargo fetch --locked` in desktop/src-tauri fills it)."""
    out = subprocess.run(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--offline",
            "--filter-platform",
            target,
        ],
        cwd=manifest_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise SystemExit(
            "cargo metadata failed — run `cargo fetch --locked` in "
            f"{shown(manifest_dir)} first:\n{out.stderr.strip()}"
        )
    meta: dict = json.loads(out.stdout)
    return meta


def refresh() -> dict:
    """Rebuild the inventory from cargo and the registry cache."""
    metas = {p: cargo_metadata(DESKTOP_LOCK.parent, t) for p, t in TARGETS.items()}
    return inventory(lock_packages(DESKTOP_LOCK), metas)


def load(path: Path = INVENTORY) -> dict:
    """The committed inventory (licenses/desktop-crates.json)."""
    doc: dict = json.loads(path.read_text(encoding="utf-8"))
    return doc


def dump(doc: dict) -> str:
    """One package per line: the inventory diffs like the lockfile it mirrors."""
    head = {k: v for k, v in doc.items() if k != "packages"}
    lines = json.dumps(head, indent=2, ensure_ascii=False)[:-2]
    rows = ",\n".join(
        "    " + json.dumps(p, ensure_ascii=False, sort_keys=True)
        for p in doc["packages"]
    )
    return f'{lines},\n  "packages": [\n{rows}\n  ]\n}}\n'


def crates(doc: dict, platform: str | None = None) -> list[Component]:
    """The inventory's linked crates as components — for one platform, or
    for any when PLATFORM is None."""
    out = []
    for p in doc["packages"]:
        if not p["linked"] or (platform and platform not in p["linked"]):
            continue
        where = (
            "" if len(p["linked"]) == len(TARGETS) else f"{', '.join(p['linked'])} only"
        )
        out.append(
            Component(
                p["name"],
                p["version"],
                p["license"],
                tuple(p["copyright"]),
                source=p["source"],
                note=where,
            )
        )
    return out


def notice_texts(doc: dict) -> dict[str, str]:
    """name version -> the NOTICE file text a linked crate carries."""
    return {
        f"{p['name']} {p['version']}": p["notice"]
        for p in doc["packages"]
        if p.get("notice")
    }


def staleness(doc: dict) -> list[str]:
    """The inventory against the lockfiles and pins it was written from."""
    errors: list[str] = []
    have = {(p["name"], p["version"]) for p in doc["packages"]}
    app = doc["app"]
    want = lock_packages(DESKTOP_LOCK) - {(app["name"], app["version"])}
    errors.extend(
        f"{n} {v} is in desktop/src-tauri/Cargo.lock but not in the notices "
        "inventory — run: tools/third_party_notices.py refresh"
        for n, v in sorted(want - have)
    )
    errors.extend(
        f"{n} {v} is in the notices inventory but no longer in Cargo.lock — "
        "run: tools/third_party_notices.py refresh"
        for n, v in sorted(have - want)
    )
    core = lock_packages(CORE_LOCK)
    if {n for n, _ in core} != {"castle-core"}:
        errors.append(
            "core/Cargo.lock now has dependencies; castle-core's notices say it "
            f"has none — list them in tools/notices_desktop.py: {sorted(core)}"
        )
    pin = re.search(
        r'^channel\s*=\s*"([^"]+)"',
        CORE_TOOLCHAIN.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if pin is None or pin.group(1) != CORE_RUST:
        errors.append(f"core/rust-toolchain.toml is no longer Rust {CORE_RUST}")
    cli = json.loads(
        (ROOT / "desktop" / "cli" / "package.json").read_text(encoding="utf-8")
    )
    if cli.get("devDependencies", {}).get("@tauri-apps/cli") != TAURI_CLI:
        errors.append(
            f"desktop/cli pins a Tauri CLI other than {TAURI_CLI}: re-check the "
            "NSIS and nsis_tauri_utils versions its bundler downloads"
        )
    if WEBVIEW2_CRATE not in have:
        errors.append(
            f"{' '.join(WEBVIEW2_CRATE)} is gone from Cargo.lock: re-check the "
            "WebView2 loader version the Windows notices name"
        )
    return errors
