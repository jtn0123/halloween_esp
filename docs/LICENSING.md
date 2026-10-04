# Licensing: what ships, under what terms, and what is still yours to decide

This is the seller's view of docs/PRODUCTION-TODO.md §3 "Licences". It
describes what each published artifact contains that someone else wrote,
which licence each part comes under, and how the release carries the
notices. Where a licence asks something of whoever passes the software on,
this file quotes the licence rather than interpreting it. Anything that
needs a judgement is listed under [Open decisions](#open-decisions) for
you to make — it is not settled here.

The notices themselves are generated: `tools/third_party_notices.py`
writes them from reviewed tables, and `make check` fails when they go stale.

| Artifact | Notices file | How it ships |
| --- | --- | --- |
| Firmware image (`castle-fw-…factory.bin` / `.ota.bin`) | `licenses/THIRD-PARTY-NOTICES-firmware.txt` | release asset `castle-fw-feather-s3-4m2p-<tag>.notices.txt`; served beside the web flasher as `THIRD-PARTY-NOTICES.txt` and linked from its footer |
| Castle Tools desktop app (`.dmg`, `.app.tar.gz`, `-setup.exe`) | `licenses/THIRD-PARTY-NOTICES-desktop.txt` | a Tauri bundle resource, `THIRD-PARTY-NOTICES.txt` in the app's resources (`desktop/src-tauri/tauri.conf.json`) |
| castle-core zips (`castle-core-<target>-<tag>.zip`) | `licenses/THIRD-PARTY-NOTICES-castle-core.txt` | `THIRD-PARTY-NOTICES.txt` inside each zip (`tools/release_assets.py zip-core`) |
| Source zip / the uv installer's bundle | `THIRD-PARTY-NOTICES.txt` | at the top of the tree, so GitHub's "Source code" zip carries it (no `export-ignore`) |
| Web flasher page (`flasher/`) | the firmware notices | `pages.yml` copies the release's notices asset into the site |
| The sold castle's SD card (`make buyer-card`) | the firmware notices, plus the written source offer | `licenses/THIRD-PARTY-NOTICES.txt` and `licenses/SOURCE-OFFER.txt` on the card (`tools/buyer_card.py`), linked from the castle's owner page as "Licences" and "Source code" (firmware v5.77) |

## How the notices are made

- **Tables, not guesses.** `tools/notices_firmware.py`,
  `tools/notices_desktop.py` and `tools/notices_external.py` hold every
  component, its version, licence, copyright lines and source address.
  Where a package's own metadata disagrees with its licence file, or is
  missing, the table says so in the entry's note.
- **Licence texts are copies, pinned.** `licenses/texts/` holds SPDX's
  generic texts (license-list-data v3.27.0). `licenses/components/` holds a
  component's own licence or NOTICE file, verbatim, where the generic text
  would paraphrase it. Every file is pinned by sha256 in
  `tools/notices_model.py`, together with where it came from. newlib's
  1,293-line COPYING.NEWLIB is split into three files to fit the 500-line
  rule, and is joined back together when it is printed.
- **Desktop crates come from cargo.** `third_party_notices.py refresh`
  runs `cargo metadata --offline --locked` for both release targets. It
  walks the normal dependency edges from the app, leaves out proc-macros
  and build-only crates, and reads each crate's licence files from the
  local registry. The result is committed as `licenses/desktop-crates.json`,
  which lists every package in `desktop/src-tauri/Cargo.lock`. Cargo unifies
  features, so the list may include a crate that a feature split would
  drop. It never leaves one out.
- **The firmware table is checked against a real link.**
  `third_party_notices.py check-firmware <build dir>` reads the image's
  linker maps (app and bootloader), its `dependencies.lock` and ESPHome's
  `version.h`. It fails on any archive the table does not know, and on any
  ESP-IDF, toolchain, ESPHome or managed-component version other than the
  reviewed one. `firmware.yml` runs it on every firmware PR. `release.yml`
  runs it before staging an image.

The gates (`tests/test_third_party_notices.py`,
`tests/test_notices_policy.py`, `tests/test_notices_inventory.py`) fail when:

- a Cargo.lock package has no inventory entry, or the inventory lists a
  package the lock no longer has;
- castle-core gains a dependency;
- a pinned version moves: the castle-core toolchain, the Tauri CLI (which
  fixes the NSIS version), the esphome pin, or esp-web-tools;
- a committed notices file differs from what the tables generate;
- a copyleft licence (GPL, LGPL, AGPL) is chosen for a shipped component
  and the table records no reason. GPL appears in exactly the three firmware
  components below, and the test lists them by name;
- a non-commercial licence appears anywhere, whatever the table says;
- a weak-copyleft component (MPL-2.0, NSIS) gives no source address;
- something is added to the app bundle beyond castle-core's sidecar.

## What each artifact contains

### Firmware image

The image is this project's ESPHome configuration and C++ headers
(`firmware/`). It is compiled by `make build-buyer` together with:

- **ESPHome 2026.9.0, C++ runtime: GPL-3.0-only.** ESPHome's LICENSE (the
  "ESPHome License") says the C++ runtime is published under GPLv3 and the
  Python code generator under MIT. PyPI's metadata for the `esphome`
  package says only "MIT". The notices follow the LICENSE file, because
  the runtime is what the image contains.
- **esp-audio-libs 3.2.1: GPL-3.0-only.** Same licence file; the parts
  linked in are C++.
- **GCC runtime (libgcc, libstdc++) 14.2.0:** GPL-3.0-or-later WITH
  GCC-exception-3.1. Section 1 of the exception permits propagating Target
  Code made by an Eligible Compilation Process under terms of your choice.
  libstdc++ also carries the HP and SGI STL notices, which are printed.
- **Mbed TLS 3.6.6:** Apache-2.0 OR GPL-2.0-or-later. The notices comply
  with Apache-2.0.
- **Mozilla CA certificate list: MPL-2.0.** ESP-IDF embeds it as the
  certificate bundle. The notices give its source address.
- **Permissive (Apache-2.0, MIT, BSD-2/3-Clause, ISC):** ESP-IDF 5.5.5 and
  its precompiled Wi-Fi/PHY libraries, FreeRTOS, lwIP, wpa_supplicant,
  FreeBSD net80211, the TLSF allocator, http_parser, the OpenBSD-derived
  SD/MMC driver, UBSAN, the Xtensa HAL, micro-mp3, micro-opus and Opus,
  micro-wav, mdns, multipart-parser, and the Improv SDK.
- **Own licence texts:** FatFs R0.15 (a one-clause BSD-style notice) and
  newlib 4.3.0 (COPYING.NEWLIB, a collection of BSD-style notices).

### Castle Tools (desktop app)

The app and its castle-core sidecar are this project's own code. Linked in:

- 272 crates out of the lock's 518. 204 are in both builds, 42 are macOS
  only and 26 are Windows only. Licences complied with: MIT (239),
  Unicode-3.0, Apache-2.0, BSD-3-Clause, ISC and Zlib, plus MPL-2.0 for
  `cssparser`, `selectors`, `dtoa-short` and `option-ext`. Each of those
  four lists its crates.io source address. Where a crate has an OR choice,
  the notices name the licence they comply with.
- 52 crates have no copyright line anywhere in their package. Their entry
  says so and names the Cargo.toml authors instead of inventing a line.
- The Rust standard library: MIT OR Apache-2.0, with LLVM-exception for
  compiler-builtins and Unicode-3.0 for its data tables.
- Windows only: Microsoft's WebView2 loader (BSD-3-Clause, statically
  linked by `webview2-com-sys`). The installer also carries NSIS 3.11:
  zlib/libpng terms, bzip2's licence, and CPL-1.0 for its LZMA module.
  NSIS's own COPYING file is printed in full and its source address is
  given. The installer's `nsis_tauri_utils` plugin is MIT OR Apache-2.0.

### castle-core zips, the source zip, the web flasher

castle-core has no third-party crates (`core/Cargo.lock` lists only
itself). What it links that someone else wrote is the Rust standard
library, pinned at 1.88.0. The source tree carries no third-party source;
`git ls-files` finds no vendored licence headers, and `web/` has no
runtime npm dependencies (all four are dev-only). The web flasher page is
this project's HTML. It loads esp-web-tools 10.4.0 (Apache-2.0) from
unpkg.com into the visitor's browser, and does not carry it.

### The sold castle's SD card

`make buyer-card [TAG=<release tag>]` writes the card a castle is sold
with: the shipped show (`scenes/shipped.yaml`: the yard's scenes that need
no imported song, with synthesised sound), the firmware notices and the
written source offer. It copies nothing from `tracks/`, and it refuses a
scene that needs a song. The scene audio is this project's own, rendered
from scenes.yaml. The offer's text is `tools/buyer_card.py`
`source_offer`, and `tests/test_buyer_card.py` holds it to the §6 b)
points quoted below.

## Not redistributed: what the buyer's own machine downloads

None of these is inside any published file today. They are listed in the
desktop and source notices so a buyer can see where each comes from.

| Component | Terms as published | How it arrives |
| --- | --- | --- |
| FFmpeg 9.0.2 (pinned static builds) | GPL-3.0, because those builds enable GPL parts; FFmpeg is LGPL-2.1-or-later when built without them | already on PATH, winget or Homebrew, or a pinned download checked by sha256 |
| yt-dlp | Unlicense; its standalone binary bundles code listed in its own THIRD_PARTY_LICENSES.txt | downloaded from its GitHub releases into per-user app data, by the installer or the owner's Update the downloader (`tools/ytdlp_update.py`), and checked against that release's SHA2-256SUMS |
| Demucs 4.1.0 | MIT | PyPI, through uv, hash-checked |
| htdemucs weights (signature 955717e8) | **no licence statement found** (the Hugging Face model card declares none; the package's MIT statement names its code) | downloaded by demucs on first use |
| PyTorch, NumPy, SciPy and the rest of `requirements-desktop.lock` | each package's own (the three named are BSD-3-Clause and bundle further libraries) | PyPI, through uv, hash-checked |
| Python 3.13 (python-build-standalone) | PSF-2.0 plus the libraries it is built with | `uv python install` |
| uv | MIT OR Apache-2.0 | astral.sh |
| WebView2 Runtime | Microsoft's runtime terms | part of Windows 10/11, else Microsoft's bootstrapper |
| DM Sans, Manrope | SIL OFL 1.1 | the browser fetches them from Google Fonts when a Castle Radio page opens on a computer; the castle's own copy of the page drops the import |

`tests/test_third_party_notices.py` pins the app's bundled resources and
the release job's sidecar step. Moving any of these into a bundle (the
plan in PRODUCTION-TODO §5.2) fails that test until the bundle gets its
notices.

## The firmware image and GPLv3

The image contains ESPHome's GPLv3 runtime, so the following GPLv3 text
applies to anyone who conveys it. Quoted from `licenses/texts/GPL-3.0.txt`:

> §5 c) You must license the entire work, as a whole, under this License to
> anyone who comes into possession of a copy. […]

> §5 d) If the work has interactive user interfaces, each must display
> Appropriate Legal Notices; however, if the Program has interactive
> interfaces that do not display Appropriate Legal Notices, your work need
> not make them do so.

> §6 You may convey a covered work in object code form under the terms of
> sections 4 and 5, provided that you also convey the machine-readable
> Corresponding Source under the terms of this License, in one of these
> ways:
>
> a) Convey the object code in, or embodied in, a physical product […],
> accompanied by the Corresponding Source fixed on a durable physical medium
> customarily used for software interchange.
>
> b) Convey the object code in, or embodied in, a physical product […],
> accompanied by a written offer, valid for at least three years and valid
> for as long as you offer spare parts or customer support for that product
> model, to give anyone who possesses the object code either (1) a copy of
> the Corresponding Source […] or (2) access to copy the Corresponding
> Source from a network server at no charge.
>
> […]
>
> d) Convey the object code by offering access from a designated place
> […], and offer equivalent access to the Corresponding Source in the same
> way through the same place at no further charge. […] If the place to
> copy the object code is a network server, the Corresponding Source may be
> on a different server (operated by you or a third party) that supports
> equivalent copying facilities, provided you maintain clear directions next
> to the object code saying where to find the Corresponding Source.
> Regardless of what server hosts the Corresponding Source, you remain
> obligated to ensure that it is available for as long as needed to satisfy
> these requirements.

> §6 […] If you convey an object code work under this section in, or with,
> or specifically for use in, a User Product, and the conveying occurs as
> part of a transaction in which the right of possession and use of the
> User Product is transferred to the recipient […], the Corresponding Source
> conveyed under this section must be accompanied by the Installation
> Information.

The image reaches people in two ways. Each one meets a different part of
§6:

1. **From the GitHub release and the web flasher**, as a download. The
   release page also holds the tag's "Source code" zip: the image's
   configuration, its headers and the Makefile that builds it. The notices
   give the source address of ESPHome, ESP-IDF and each component at the
   version built.
2. **Pre-installed in the castle you sell.** That is object code embodied
   in a physical product, the case §6 a) and b) describe. A Halloween
   decoration bought for a home is the kind of product the licence's
   "User Product" definition describes. The castle accepts a new image over
   USB (the web flasher) and over the network (`PUT /api/ota`).

   Since 2026-10-03 (decision 2 below) the card it ships with carries the
   firmware notices and a written offer under §6 b). The offer is dated and
   valid three years, or for as long as spare parts or customer support are
   offered, whichever is later. It is made to anyone who possesses the
   object code. It offers both (1) a copy on a durable physical medium at
   no more than cost, asked for through this repository's issues, and (2)
   network access at no charge: this repository at the release tag, that
   tag's "Source code" zip, and each third-party component at the source
   address its notice gives. It ends with the Installation Information.
   USB installs need no key and no signature, and the castle has no secure
   boot. Network installs ask for the castle key only when one is set, and
   a USB install that erases the castle clears it. The castle's owner page
   links both files (§5 d)).

## Open decisions

Each of these is yours to make. Decision 2 has been made and carried out;
it keeps its number because other documents cite it. The rest are open.

1. **A licence for this repository.** The repo has no LICENSE file, so
   nobody else has a licence to its code. §5 c) above concerns the
   firmware image as a whole. Decide what licence `firmware/`, and the rest
   of the tree, is offered under. The notices currently call it "the
   Halloween Castle project's own work (copyright jtn0123)".
2. **How the sold castle carries its notices and source. DECIDED
   2026-10-03: both, and IMPLEMENTED (firmware v5.77).** The castle's SD
   card carries the firmware notices and a written offer
   (`licenses/THIRD-PARTY-NOTICES.txt`, `licenses/SOURCE-OFFER.txt`, made
   by `make buyer-card`). The castle's owner page links them, and
   docs/OWNER-GUIDE.md "Licences and source code" says where they are. The
   source the offer relies on is this public GitHub repository and the
   tagged release, plus the upstream addresses in the notices. Keeping it
   there for the offer's whole period is the seller's job: §6 d)'s "you
   remain obligated". So the repository stays public and the release tag
   is never deleted. Make each card with `TAG=` naming the release the
   castle's image came from, so the offer names that tag.
3. **Installation Information.** docs/RELEASING.md and the web flasher
   describe how to install an image. If the castle ever requires a key to
   accept one (the optional password, PRODUCTION-TODO §1.6), decide how an
   owner who wants to install a modified image gets that key.
4. **The htdemucs weights.** No licence statement was found for them. They
   are downloaded on the buyer's machine and not shipped. PRODUCTION-TODO
   §5.2 plans to bundle them, and that needs terms from their publisher
   first. The demucs README describes the model as trained on MUSDB HQ
   plus an extra dataset of 800 songs.
5. **ffmpeg, if it is ever bundled** (§5.2 plans an `externalBin`). Bundle
   only an LGPL build: no `--enable-gpl`, no `--enable-nonfree`. It also
   needs its licence text and source offer in the desktop notices. The
   builds the installer downloads today are GPL builds, which is fine only
   while they are not redistributed.
6. **A bundled Python, if §5.2 lands.** python-build-standalone, PyTorch,
   NumPy, SciPy and their bundled libraries would then be redistributed,
   and each needs entries in the desktop notices.
7. **The desktop toolchain is `stable`, not a pinned version.** The app's
   Rust standard-library entry says "the stable toolchain of the release
   build" because `release.yml` does not pin one. Pin it if you want the
   notices to name the exact version.
8. **Showing the notices in the apps.** The desktop app carries its file
   as a resource, and does not show it in its interface. The castle's
   owner page links the firmware notices and the source offer when its
   card holds them (v5.77, decision 2). Whether the desktop app should show
   its notices is still a product decision. It belongs to the code that
   interface lives in.

## Maintenance

- Before a castle is sold: `make buyer-card TAG=<the release its image
  came from>` (docs/SUPPORT.md), then copy `buyer-card/` onto the card.
  The offer is dated the day it is made.

- A crate added or bumped in `desktop/src-tauri/Cargo.lock`: run
  `cargo fetch --locked` in `desktop/src-tauri`, then
  `tools/third_party_notices.py refresh`, review the new entries, and commit.
- A firmware component, ESP-IDF, toolchain or esphome change: update
  `tools/notices_firmware.py` (versions, `ARCHIVES`, entries), then run
  `tools/third_party_notices.py generate`. `firmware.yml` checks it
  against a real build on the PR.
- A new licence text: copy it verbatim into `licenses/texts/` or
  `licenses/components/`, then pin it in `notices_model.TEXTS` with its
  source and sha256.
