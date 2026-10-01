# Castle Tools — the desktop app

A Tauri 2 shell around the two servers the repo already has. It is option B
of docs/PRODUCTION-TODO.md (sections 5 and 9): the buyer installs one app, it
starts the servers, and its window is Castle Radio.

```
desktop/
  dist/            the local splash page (status, retry, open log, version)
  src-tauri/       the Rust app; tauri.conf.json + per-platform overrides
  sidecar/castle/  filled by the release build, gitignored (layout below)
```

## What it runs

Two sidecars, each under its own `Supervisor` (`src/supervisor.rs`, one per
`Service` in `src/service.rs`):

| Server | Program | Port | Identity probe |
|---|---|---|---|
| Castle Radio — the window | `python demo/castle-radio/server.py PORT` | 8871 (`CASTLE_STUDIO_PORT`) | `GET /radio/tools` → `{"service":"castle-radio","protocol":1}` |
| The cue desk studio — the light desk, rebuild, publish | castle-core's `studio PORT` | 8775 (`CASTLE_DESK_PORT`) | `GET /studio/tracks` → `{"tracks":[…],"scenes":[…]}` |

8871 is fixed because the castle's own page opens its companion popup on that
origin. The studio is deliberately **not** on 8765: that is the developer's
`make studio`, pointed at the repo's show, and the app reuses a server that
answers its probe, so sharing the port would let a buyer's edit land in a
checkout. Each supervisor reuses a server already answering as itself (a
crashed earlier run, say), refuses a port held by anything else, and stops
only what it started. Children run in their own process group (Unix) or a
kill-on-close job object (Windows), so ffmpeg, Demucs and the importers die
with the app (`src/child.rs`, `src/signals.rs`). A missing studio binary
fails the light desk only; Castle Radio still opens.

### Data — never a repo

Both children get the same environment (`runtime::child_env`):

| Variable | Value |
|---|---|
| `CASTLE_RADIO_DATA` | `<app data>/radio` — Castle Radio's library root (`radio_paths.py`) |
| `CASTLE_TRACKS` | `<app data>/radio/tracks` |
| `CASTLE_SCENES` | `<app data>/radio/scenes.yaml` |
| `CASTLE_BUILD` | `<app data>/radio/build` |
| `CASTLE_HOST` | `""` — explicitly no castle for the toolchain |
| `CASTLE_RADIO_HOST` | `castle_host` from settings.json, when set |
| `CASTLE_PY` | the runtime's interpreter, so the studio's children use it |

`<app data>` is Tauri's `app_data_dir()` for the identifier
`io.github.jtn0123.castletools`: `~/Library/Application Support/…` on macOS,
`%APPDATA%\…` on Windows. On first run the runtime's shipped
`scenes/scenes.yaml` is copied there (`runtime::seed_scenes`: copy beside, then
rename; never overwrites — after that it is the owner's file). The NSIS
uninstaller leaves this directory unless its "delete application data" box is
ticked, so songs survive an uninstall by default.

Logs: one file, `castle-tools.log`, in `app_log_dir()` (`~/Library/Logs/…`,
`%LOCALAPPDATA%\…\logs`), holding the app's own lines and both servers'
stdout/stderr. Rotated to `.log.1` past 5 MB at launch.

### Where the servers come from (`src/runtime.rs`)

First match wins:

1. **Bundled sidecar** — `<resources>/castle/`, what a release ships:
   ```
   castle/app/       the repo's tools/, demo/castle-radio/, scenes/, web/…
   castle/python/    python-build-standalone 3.13 + site-packages
   castle/bin/       studio, analyze_track, scene_render (from the Release's
                     castle-core-<target>-<tag>.zip), ffmpeg, yt-dlp
   castle/models/    a Hugging Face hub cache with the Demucs model
   ```
2. **Configured install** — `CASTLE_INSTALL_DIR`, else `install_dir` in
   settings.json (the option-A installer's tree, or any checkout). A broken
   one is an error, not a silent fall-through.
3. **Developer checkout** — the repo this crate was built from. The studio
   is `core/target/release/studio` there (`make rust`).

## Settings

`settings.json` in `app_config_dir()`; every key optional, a broken file is
the defaults plus a log line:

```json
{ "castle_host": "castle-feather-s3.local",
  "install_dir": "/Users/me/CastleTools",
  "python": "/Users/me/CastleTools/.venv/bin/python" }
```

`castle_host` accepts a host name, IPv4 address or `host:port` and nothing
that could reshape a URL (`settings::valid_host`).

## Tray, deep link, window

- **Tray** (`src/tray.rs`): ♜ in the macOS menu bar (a text title, so it
  follows light/dark), the app icon in the Windows notification area. Show,
  Start, Open in browser, Open the light desk in browser, Open log, Check for
  updates, Quit. Closing the window hides it; Quit stops both servers.
- **`castle-tools://start`** (`src/deeplink.rs`): the one action a web page may
  ask for — start both servers, leave the browser in front. Any other URL is
  logged and dropped. macOS registers it through the bundle's Info.plist,
  the NSIS installer through the registry; a debug run on Windows/Linux
  registers itself. A second launch is handed to the first
  (single-instance plugin).

## Updates (section 9)

`tauri-plugin-updater` reads
`https://github.com/jtn0123/halloween_esp/releases/latest/download/latest.json`
on launch (after 20 s) and once a day — one unauthenticated request, far
inside GitHub's 60 an hour. GitHub's "latest" never names a pre-release, and
`updater.rs` additionally refuses any version that is not a stable
`vX.Y.Z` tag (`release::is_stable`). It always asks; nothing installs
silently. The servers are stopped before the installer replaces their files.

**The signing key.** Updates are verified with a minisign key pair — free,
and required even for an unsigned app; it is not code signing. Until the
placeholder `CASTLE_TOOLS_UPDATER_PUBKEY_PLACEHOLDER` in `tauri.conf.json`
is replaced, the app skips every check and "Check for updates" says why.
Once, on the owner's machine:

```sh
npx @tauri-apps/cli signer generate -w ~/.tauri/castle-tools.key
```

1. Paste the **public** key (the contents of `castle-tools.key.pub`, one
   base64 line) over the placeholder in `plugins.updater.pubkey`; commit it.
2. Store the **private** key file's contents as the Actions secret
   `TAURI_SIGNING_PRIVATE_KEY`, its password as
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. The release build signs with them.
3. Back the private key up somewhere that is not this laptop. Losing it
   strands every installed app: they will refuse anything signed otherwise,
   and the only way out is a manual reinstall.

## Building

Debug, from a checkout (uses the checkout's `.venv` and `core/target/release/studio`):

```sh
cd desktop/src-tauri && cargo run        # or: make desktop-test / desktop-lint
```

A release bundle needs the Tauri CLI (`npx @tauri-apps/cli@2`) and, for a
self-contained app, a staged `desktop/sidecar/castle/` (layout above):

```sh
cd desktop/src-tauri
npx @tauri-apps/cli@2 build                                       # no sidecar, no updater artifacts
npx @tauri-apps/cli@2 build --config tauri.release.conf.json      # CI: bundles castle/, signs latest.json
```

Run it with `CI=true` on a desktop Mac: without it the dmg step styles the
Finder window by AppleScript, and `bundle_dmg.sh` fails where the terminal has
no Automation permission (seen 2026-09-30; with `CI=true` the dmg builds, 2.9 MB
without a sidecar, ad-hoc signed, `castle-tools` in its URL types).

`tauri.macos.conf.json` builds `.app` + `.dmg` (ad-hoc signed, `signingIdentity
"-"` — Apple Silicon needs at least that); `tauri.windows.conf.json` builds the
NSIS `.exe` only, per-user (`installMode: currentUser`, no admin prompt),
fetching WebView2 with the bootstrapper when missing. Both are unsigned by
decision (PRODUCTION-TODO §5.4): first launch is Privacy & Security → "Open
Anyway" on macOS and SmartScreen "More info → Run anyway" on Windows. Set the
bundle version from the tag (`0.1.0` here, `v0.1.0` on the Release).

A debug target is 1–2 GB and a release one about as much again (`[profile.dev]
debug = "line-tables-only"` already halves it). `cargo clean` in
`desktop/src-tauri` when done.

## Release contract

`tools/release_assets.py` names every Release asset. `src/release.rs` is the
app's copy of the names it reads; keep the pairs equal:

| release_assets.py | release.rs |
|---|---|
| `BOARD = "feather-s3-4m2p"` | `BOARD` |
| `CORE_TARGETS` | `CORE_TARGETS` |
| `TAG_RE` | `parse_tag` |
| `factory_name` / `ota_name` / `core_zip_name` | the same three |

## Integration points (open)

- `CASTLE_CORE_BIN_DIR` is set to the sidecar's `bin/`, but
  `tools/core_bins.py` still builds `analyze_track`/`scene_render` with cargo;
  a buyer has no cargo, so it must learn to read that directory first.
- The sidecar tree (python-build-standalone, site-packages, ffmpeg, yt-dlp,
  the Demucs model, the castle-core zip) is staged by the release workflow,
  which does not exist on this branch.
- `latest.json` and the Tauri bundles are Release assets that
  `release_assets.py finish` does not list yet — it refuses extras, so the
  contract has to grow them before the release workflow can publish both.
- Pre-release opt-in (a hidden setting) is not implemented: GitHub has no
  "latest pre-release" URL, so it needs an endpoint of its own.
- The firmware update (compare `/api/status` `board` and version, fetch
  `release::ota_name`, `PUT /api/ota`) is not implemented; `castle_release`
  already reports the names it will use.
