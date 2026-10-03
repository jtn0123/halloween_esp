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
   **pre-release**: published, but ignored by the app's update check unless
   opted in, and the web flasher keeps serving the previous full release.
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
| `castle-fw-feather-s3-4m2p-<tag>.ota.bin` | The app image alone, what `PUT /api/ota` takes. Size-gated by `tools/check_image.py` (fails at 97% of the 1,835,008-byte slot). | The desktop app's "Update castle". |
| `castle-fw-feather-s3-4m2p-<tag>.notices.txt` | The two images' third-party notices (`licenses/THIRD-PARTY-NOTICES-firmware.txt`, docs/LICENSING.md). | Anyone given an image; `pages.yml` serves it beside the flasher. |
| `castle-core-<target>-<tag>.zip` | `analyze_track`, `scene_render`, `studio` (`.exe` on Windows) and `THIRD-PARTY-NOTICES.txt`, flat. Targets: `x86_64-pc-windows-msvc`, `aarch64-apple-darwin`, `x86_64-apple-darwin`. | The desktop app's sidecars. |
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
page). One-time setup: repository **Settings → Pages → Source: GitHub
Actions**.

## The updater key (minisign) — read before the first desktop release

The desktop app updates itself from `latest.json`, and it installs only a
bundle whose signature verifies against the public key compiled into it.
That key pair is Tauri's own **minisign** update-signing key: free, made
locally, and NOT code signing (the app is still unsigned in the Apple /
Microsoft sense, by decision — TODO 5.4). The rules:

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

- **A tagged Windows build.** castle-core and the Python suites build and
  pass on Windows in `cross-platform.yml`, blocking since 2026-10-01; the
  release's Windows `core` job has not yet been exercised by a tag.
- **The buyer firmware** (`firmware/castle_buyer.yaml`, `make build-buyer`)
  and **the desktop app** (`desktop/`) land from their own branches; until
  both are on the tagged commit, the `firmware` job fails on the missing
  target and the `desktop` job is skipped.
- **Intel Macs** get castle-core but not the app: TODO 9 leaves "mac x64?"
  open. Adding it is a matrix row in `desktop` and an entry in
  `release_assets.DESKTOP_TARGETS`.
