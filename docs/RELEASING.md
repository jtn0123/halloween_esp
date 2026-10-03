# Releasing

One tag is one GitHub Release, and one Release is one version of everything
a buyer's castle needs (docs/PRODUCTION-TODO.md 1.2, 5.4, 9). The workflow is
`.github/workflows/release.yml`; the asset names are spelled once, in
`tools/release_assets.py`, because the desktop app and the web flasher are
written against them.

## Cut a release

1. `make check` green on `main`, and the firmware compile (weekly, or
   `gh workflow run firmware.yml`) green on the commit you are tagging —
   it builds the buyer image the release will ship.
2. If the device build changed, the firmware's own `v5.xx` string was bumped
   (CLAUDE.md, "Commit style"). The release tag does not replace it: the tag
   versions the whole release, `v5.xx` is what the castle's panel shows.
3. Tag and push:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

   Tags are `vMAJOR.MINOR.PATCH`. A suffix (`v0.2.0-rc.1`) makes a
   **pre-release**: published, but offered by no updater unless its owner
   opted in (see "Channels" below), and the web flasher keeps serving the
   previous full release.
4. Watch the run (`gh run watch`). `publish` only starts when every build
   job is green, and refuses unless the assets are exactly the list below —
   a release is never published half-built.
5. A full release then redeploys the web flasher (`pages.yml`).

## Dry run

```sh
gh workflow run release.yml                          # dry_run defaults to true
gh workflow run release.yml -f tag=v0.1.0            # rebuild an existing tag, publish nothing
gh workflow run release.yml -f tag=v0.1.0 -f dry_run=false   # (re)publish an existing tag
```

A dry run builds every asset, writes the manifest and checksums, runs the
contract check, and keeps the lot as the run artifact
`release-dist-<tag>` for 14 days. With no `tag` it names itself
`v0.0.0-dryrun.<run number>` and can only be a dry run. Download it with
`gh run download <run-id> -n release-dist-<tag>`.

## What each asset is

| Asset | What it is | Who uses it |
| --- | --- | --- |
| `castle-fw-feather-s3-4m2p-<tag>.factory.bin` | The whole flash image from offset 0: bootloader, partition table and app. | The web flasher (new castle, recovery). |
| `castle-fw-feather-s3-4m2p-<tag>.ota.bin` | The app image alone, what `PUT /api/ota` takes. Size-gated by `tools/check_image.py` (fails at 97% of the 1,835,008-byte slot). | Castle Radio's "Update castle" (`tools/castle_update.py`). |
| `castle-fw-feather-s3-4m2p-<tag>.notices.txt` | The two images' third-party notices (`licenses/THIRD-PARTY-NOTICES-firmware.txt`, docs/LICENSING.md). | Anyone given an image; `pages.yml` serves it beside the flasher. |
| `castle-fw-feather-s3-4m2p-<tag>.json` | The images' descriptor: board, build (`fw_variant`), the firmware version `/api/status` will report, the OTA image's name and size. | "Update castle", before it downloads a megabyte. |
| `castle-core-<target>-<tag>.zip` | `analyze_track`, `scene_render`, `studio` (`.exe` on Windows) and `THIRD-PARTY-NOTICES.txt`, flat. Targets: `x86_64-pc-windows-msvc`, `aarch64-apple-darwin`, `x86_64-apple-darwin`. | The option-A installer; the desktop app carries its target's copy. |
| `flasher-manifest.json` | The esp-web-tools manifest: ESP32-S3, factory image at offset 0, Improv Wi-Fi, erase offered. | The web flasher. |
| `SHA256SUMS` | `sha256sum` format over every other asset. | Anything that downloads an asset; `pages.yml` checks it. |
| `castle-tools-aarch64-apple-darwin-<tag>.dmg` | The desktop app's macOS installer (ad-hoc signed, not notarized). | A first-time owner on a Mac. |
| `castle-tools-aarch64-apple-darwin-<tag>.app.tar.gz` (+ `.sig`) | The macOS updater bundle and its minisign signature. | The installed app's updater. |
| `castle-tools-x86_64-pc-windows-msvc-<tag>-setup.exe` (+ `.sig`) | The NSIS per-user installer, which is also the Windows updater bundle. | A first-time owner on Windows; the updater. |
| `latest.json` | The Tauri updater's pointer: version, and per platform the bundle URL and its signature. Uploaded **last**. | Every installed app, via `releases/latest/download/latest.json`. |

The five desktop rows exist only when the tagged tree has `desktop/` (the
`meta` job asks the tag, and the `desktop` job is skipped otherwise — the
release is then the castle-only list and `finish` refuses stray bundles).

`feather-s3-4m2p` is the ESP32-S3 Feather #5477 (4 MB flash, 2 MB PSRAM) and
is the same string the buyer firmware reports as `board` in `/api/status`, so
the app matches a castle to its image by equality. The firmware is
`firmware/castle_buyer.yaml`, built by `make build-buyer` (the same target a
developer runs, image gate included): no Wi-Fi baked in, softAP and captive portal,
Improv over USB. The `firmware` job fails if the run's fake CI Wi-Fi secret
turns up in the image.

### Inside the desktop app, and the first-run smoke

The `desktop` job stages `desktop/sidecar/castle/` with
`tools/desktop_bundle.py stage` before the Tauri build. The folder holds
the tagged tree's own files (the installer's file list, with
`installer/VERSION` set to the tag), the `core` job's castle-core zip for
the target, and uv at the version and sha256 `desktop_bundle.py` pins
(`UV_PINS`). It carries no Python, PyTorch, ffmpeg or model: the app's
first launch fetches those, the way option A does (desktop/README.md
"First launch"; docs/LICENSING.md).

After the build, `tools/desktop_smoke.py` runs the app the way a buyer
meets it. On macOS it starts the built `.app`; on Windows it installs the
NSIS setup silently, per user, and starts what that installed. Then:

1. The first launch must set itself up from nothing within 30 minutes. That
   is a real download from PyPI, uv's Python builds, ffmpeg's and yt-dlp's
   publishers and Hugging Face. Castle Radio must then answer
   `GET /radio/tools` able to import, import from a link and split voices,
   and the cue desk studio must answer beside it.
2. After a quit, the second launch must answer within three minutes
   without running a setup.

A setup that fails ends the smoke at once, with the app's own reason; it
does not wait out the 30 minutes on a splash that is waiting for Try again.
On any failure it prints the app's log. So a red smoke means the release
would not have worked on a buyer's first launch, or that one of those hosts
was down or turned the runner away; the log says which. The job's timeout
is 120 minutes for this.

**Bumping uv:** download both archives from the new release, check them
against the `digest` GitHub reports for each asset, change `UV_VERSION`
and `UV_PINS`, and run `tools/third_party_notices.py generate` (the
desktop notices name uv's version).

## What an installed app reads — never rename it

An app already on a buyer's computer cannot be told that a release changed.
It looks for these, by these names, forever; rename one and every install
behind it silently stops updating. `tests/test_release_contract.py` builds a
release the way the workflow does and holds every reader's own spelling to
it, then runs a castle update against that very release.

| What | Where it is looked for | Read by |
| --- | --- | --- |
| The newest release | `GET api.github.com/repos/jtn0123/halloween_esp/releases/latest` (stable); `…/releases?per_page=30` when opted in (newest non-draft by semver) | `tools/release_channel.py` — the installer's `--update`, the launcher's daily notice, "Update castle", `/radio/app/release` |
| `latest.json` | `releases/latest/download/latest.json` (stable); `releases/download/<tag>/latest.json` for the tag Castle Radio names when opted in | The app's updater (`tauri.conf.json`, `desktop/src-tauri/src/channel.rs`) |
| `castle-fw-<board>-<tag>.json` | An asset of the release, verified against `SHA256SUMS` | `tools/castle_update.py` |
| `castle-fw-<board>-<tag>.ota.bin` | The name the descriptor's `ota` field must equal; verified, then its length must equal `ota_bytes` and its first byte be `0xE9` | `tools/castle_update.py` |
| `castle-core-<target>-<tag>.zip` | An asset, verified | `tools/desktop_release.py` (the installer) |
| `SHA256SUMS` | An asset: one line per other asset, 64 hex digits, two spaces, the name (`sha256sum`'s text format) | Everything above — a release without it, or with a name it does not cover, is refused |

The descriptor is schema 1: `{"schema": 1, "tag", "board", "fw_variant",
"version", "ota", "ota_bytes", "factory"}`. A reader refuses a schema it does
not know ("update Castle Tools first"), so a new field is free and a changed
meaning is a schema 2 — shipped only after an app that reads it. `board` and
`fw_variant` are what `/api/status` reports, and the app flashes nothing
whose board or build differs from the castle's: a yard castle is never handed
the buyer build, nor the other way round. `version` is `firmware/castle.yaml`'s
`version:`, so "the castle came back on the old version" is a rollback.

Tags never change meaning either: `vMAJOR.MINOR.PATCH`, a `-suffix` making a
pre-release (`release_assets.TAG_RE`, `release_channel.py`, `release.rs` —
one table in `channel.rs` holds all three).

### Channels

Every owner is on the stable channel. The pre-release channel is an opt-in
on no page: `"prerelease": true` in the app's `settings.json`, or
`CASTLE_PRERELEASE=1` in the environment (which wins when set). The app
passes the decision to Castle Radio and the studio as `CASTLE_PRERELEASE=1`,
so the app's own update, the castle's firmware update and the installer all
take the same releases. Opted in, the newest release of either kind is
offered — and a release that is newer than an owner's pre-release moves them
onto it.

## The web flasher

`flasher/index.html` is a static page using esp-web-tools (pinned version).
GitHub's release-download URLs redirect to a host that sends no CORS
headers, so the page cannot fetch the image from the Release directly;
`pages.yml` copies the latest full release's manifest, factory image and
firmware notices next to the page (the notices as `THIRD-PARTY-NOTICES.txt`,
which the page links) and deploys them together, after checking them against
`SHA256SUMS`.

It deploys when `release.yml` publishes a full release, when a release is
published by hand, and on `gh workflow run pages.yml` (after editing the
page). One-time setup, done 2026-10-03: repository **Settings → Pages →
Source: GitHub Actions**, and the `github-pages` environment's deployment
rules allow the tag pattern `v*.*.*` beside `main` — `release.yml` runs on
the tag, and the default rule (main only) refuses its deploy.

## The updater key (minisign) — read before the first desktop release

The desktop app updates itself from `latest.json`, and it installs only a
bundle whose signature verifies against the public key compiled into it.
That key pair is Tauri's own **minisign** update-signing key: free, made
locally, and NOT code signing (the app is still unsigned in the Apple /
Microsoft sense, by decision — TODO 5.4). The rules:

Steps 1–4 were done on 2026-10-03; the rest of this list is what a fork, or
a lost key, would need.

1. Generate it once: `npx @tauri-apps/cli signer generate -w ~/.tauri/castle-tools.key`
   (give it a password).
2. The **public** key goes in `desktop/src-tauri/tauri.conf.json`
   (`plugins.updater.pubkey`), committed. The `desktop` job refuses to run
   while that field still says `PLACEHOLDER`.
3. The **private** key goes in the repository secret
   `TAURI_SIGNING_PRIVATE_KEY` (the file's contents) and its password in
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: Settings → Secrets and variables →
   Actions. The job refuses to run without the first; `stage-desktop`
   refuses a bundle that came out with no `.sig`.
4. Back the private key and password up **outside GitHub** (a secret cannot
   be read back). Lose them and every installed app is stranded: it will
   never accept an update signed by a new key, and each owner has to
   download and install by hand again. Never rotate it casually; never
   commit it.
5. `latest.json` is uploaded after every other asset, because it names them.

## Not yet in the release

- **A published one.** Dry run 3 (2026-10-03, run 37142954369) built every
  asset on this list — buyer firmware v5.76 (OTA image 1,333,328 B),
  castle-core for three targets, the signed `.dmg` / `.app.tar.gz` and the
  NSIS `-setup.exe` — and the `publish` job's checks passed. Both updater
  signatures were verified against the committed public key outside Tauri.
  No tag has been pushed yet, so nothing is published and the web flasher
  has no release to serve.
- **A self-contained desktop app.** The bundle's `castle/` holds castle-core
  only; the app finds Python and the tools through a configured install or a
  checkout (desktop/README.md, "Where the servers come from").
- **Intel Macs** get castle-core but not the app: TODO 9 leaves "mac x64?"
  open. Adding it is a matrix row in `desktop` and an entry in
  `release_assets.DESKTOP_TARGETS`.
