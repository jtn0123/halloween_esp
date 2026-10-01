# Releasing

One tag is one GitHub Release, and one Release is one version of everything
a buyer's castle needs (docs/PRODUCTION-TODO.md 1.2, 5.4, 9). The workflow is
`.github/workflows/release.yml`; the asset names are spelled once, in
`tools/release_assets.py`, because the desktop app and the web flasher are
written against them.

## Cut a release

1. `make check` green on `main`, and the weekly firmware compile (or
   `gh workflow run ci.yml`) green on the commit you are tagging.
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
| `castle-core-<target>-<tag>.zip` | `analyze_track`, `scene_render`, `studio` (`.exe` on Windows), flat. Targets: `x86_64-pc-windows-msvc`, `aarch64-apple-darwin`, `x86_64-apple-darwin`. | The desktop app's sidecars. |
| `flasher-manifest.json` | The esp-web-tools manifest: ESP32-S3, factory image at offset 0, Improv Wi-Fi, erase offered. | The web flasher. |
| `SHA256SUMS` | `sha256sum` format over every other asset. | Anything that downloads an asset; `pages.yml` checks it. |

`feather-s3-4m2p` is the ESP32-S3 Feather #5477 (4 MB flash, 2 MB PSRAM) and
is the same string the buyer firmware reports as `board` in `/api/status`, so
the app matches a castle to its image by equality. The firmware is
`firmware/castle_buyer.yaml`: no Wi-Fi baked in, softAP and captive portal,
Improv over USB. The `firmware` job fails if the run's fake CI Wi-Fi secret
turns up in the image.

## The web flasher

`flasher/index.html` is a static page using esp-web-tools (pinned version).
GitHub's release-download URLs redirect to a host that sends no CORS
headers, so the page cannot fetch the image from the Release directly;
`pages.yml` copies the latest full release's manifest and factory image next
to the page and deploys the three files together, after checking them
against `SHA256SUMS`.

It deploys when `release.yml` publishes a full release, when a release is
published by hand, and on `gh workflow run pages.yml` (after editing the
page). One-time setup: repository **Settings → Pages → Source: GitHub
Actions**.

## Not yet in the release

- **Windows castle-core.** castle-core does not compile on Windows until the
  port (TODO 4.1) lands, so the Windows `core` job — and with it every tagged
  release — fails today. That is deliberate: a release without its Windows
  half is not one. `cross-platform.yml` shows the same failure on every
  push, non-blocking, until then.
- **The Tauri desktop app** and the updater's `latest.json` (TODO 5.4, 9).
  `release.yml` holds a commented placeholder where the job goes; it will
  need the minisign update-signing key as a repository secret, and
  `latest.json` must be the last asset uploaded.
