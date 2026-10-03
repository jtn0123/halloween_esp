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
| `CASTLE_HOST`, `CASTLE_RADIO_HOST` | `castle_host` from settings.json, when set — a pin. Otherwise neither is set (an inherited one is removed), and both servers talk to the first castle of `CASTLE_DEVICES`, re-read as it changes |
| `CASTLE_DEVICES` | `<app data>/radio/devices.toml` — the castle Find my castle chose (`tools/castle_address.py`, Castle Radio's Your castle page) and the keys either app remembers (`tools/castle_keys.py`); never a checkout's tracked file |
| `CASTLE_APP_VERSION`, `CASTLE_APP_LOG` | this app's version and its log file, for Castle Radio's Copy diagnostics (`src/supervisor.rs`) |
| `CASTLE_KEY` | `castle_key` from settings.json, only when set — it then wins over that store |
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
                     castle-core-<target>-<tag>.zip), ffmpeg — never yt-dlp,
                     which lives in app data ("The song downloader" below)
   castle/models/    a Hugging Face hub cache with the Demucs model
   ```
2. **Configured install** — `CASTLE_INSTALL_DIR`, else `install_dir` in
   settings.json (the option-A installer's tree, or any checkout). A broken
   one is an error, not a silent fall-through.
3. **Developer checkout** — the repo this crate was built from. The studio
   is `core/target/release/studio` there (`make rust`).

## What an import can take

A failed import says one sentence: what happened, then what to do. The
words are `tools/import_reason.py`'s, and the light desk's are castle-core's
copy (`core/src/studio_reason_words.rs`), held word for word by
`tests/test_import_reason.py` (docs/PARITY.md). The tools' own output stays
in the log and behind the queue's **Details**; no exit code, exception name
or traceback reaches the sentence.

| What the owner tries | What happens |
|---|---|
| A song longer than **15 minutes** | Refused before anything is converted, with its length and the limit. A start and length that pick a shorter part still import. A link is refused by yt-dlp before it downloads (`--match-filters "!is_live & duration <=? 900"`); live streams never start. |
| A file that is not audio (a document, a renamed picture) | "… does not look like playable audio — choose an MP3, WAV, FLAC, M4A or OGG file instead." Castle Radio refuses an unknown type at upload, before the bytes are kept. |
| A full disk | "The disk is full — …", from the conversion, the copy of the source, the download, the splitter or the upload. Nothing half-made is left: the conversion writes a `.part` beside the track and renames last, and an upload that fails is deleted. |
| A name in any alphabet | The title keeps its letters, composed (NFC): a Mac's decomposed "Café" and a typed one are one song. The track id — a file name on the castle's FAT card — is ASCII: accents fold away, and a name with no Latin letters gets a stable `song_<hash>`. |
| A read-only or network location (a CD, a share, a NAS, `\\server\share`) | Read, never written. The kept source is a plain copy without the read-only bit, so a re-import can replace it. A file this user may not read, or a share that drops mid-read, says "Castle Tools was not allowed to read or save a file it needed — copy the song to a folder on this computer and choose it again." |
| Cancel | Ends the job as **Cancelled** at any stage — never as a failure. |

**Why 15 minutes.** Not a format limit: castle-core's onset analysis holds
the whole song in memory, and measured on 2026-10-02 it peaks at about
250 MB per minute of audio (30 minutes → 7.5 GB, 2 hours → 24.6 GB).
Fifteen minutes is about 3.8 GB, which an 8 GB laptop survives; a 2-hour
file would swap the machine to a halt instead of failing. The number is
`MAX_IMPORT_SECONDS` in `tools/import_convert.py`; streaming the analysis
would lift it.

## The song downloader

yt-dlp fetches a pasted link. It is **not** in the app bundle: websites
change under it every few weeks, so it is a separate program in per-user
app data — `<app data>/radio/downloader/` (`tools/exe_paths.py`
`downloader_dir()`: `CASTLE_DOWNLOADER_DIR`, else `downloader/` in
`CASTLE_RADIO_DATA`) — that every importer runs first when it is there
(`exe_paths.ytdlp()`, `core/src/portable.rs`).

**Update the downloader** — a button in Castle Radio's import panel, and on
any job whose link failed because the downloader is old ("The downloader may
be out of date — Update the downloader, then try the link again.") — runs
`tools/ytdlp_update.py`: one call to GitHub's Releases API for yt-dlp's
latest stable release, that same release's `SHA2-256SUMS` and this
computer's standalone build, a refusal unless the bytes match, a trial
`--version` run, then one rename over the old copy. Any failure keeps the
old copy and says why in one sentence. The update is queued on the same
one-at-a-time worker as the imports, so it waits for the import ahead of
it and is never swapped in under one; nothing updates by itself. The
option-A installer runs the same updater into its `bin/` on install,
`--update` and `--repair`.

yt-dlp is released into the public domain (the Unlicense); its standalone
build bundles third-party code listed in its own `THIRD_PARTY_LICENSES.txt`.
This project never ships or redistributes it — THIRD-PARTY-NOTICES.txt lists
it under what the buyer's machine downloads (`tools/notices_external.py`).

## Settings

`settings.json` in `app_config_dir()`; every key optional, a broken file is
the defaults plus a log line:

```json
{ "castle_host": "castle-feather-s3.local",
  "castle_key": "the key the castle was locked with",
  "install_dir": "/Users/me/CastleTools",
  "python": "/Users/me/CastleTools/.venv/bin/python" }
```

`castle_host` accepts a host name, IPv4 address or `host:port` and nothing
that could reshape a URL (`settings::valid_host`). Most owners never need it
either: Castle Radio's **Find my castle** (Your castle page) browses the LAN
for it, or takes a typed address, and remembers it in `CASTLE_DEVICES`,
where the light desk reads it too; a `castle_host` here pins the castle
instead, and Find my castle then says so rather than change it. `castle_key` (firmware
v5.74's optional lock) is trimmed and passed only if the castle could hold it
— 1-64 printable characters, no spaces (`settings::valid_key`); anything else
is dropped with a log line that names the rule, never the key. Most owners
never need it: entering the key in either app's Settings remembers it in
`CASTLE_DEVICES`, and a key set here pins it instead (the apps then refuse to
change it, since the store could not follow).

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

A release bundle needs the Tauri CLI — the exact one CI uses is locked in
`desktop/cli/` (`npm ci --ignore-scripts --prefix desktop/cli`) — and, for a
self-contained app, a staged `desktop/sidecar/castle/` (layout above):

```sh
npm ci --ignore-scripts --prefix desktop/cli
cd desktop/src-tauri
TAURI=../cli/node_modules/@tauri-apps/cli/tauri.js
node $TAURI build                                     # no sidecar, no updater artifacts
node $TAURI build --config tauri.release.conf.json    # CI: bundles castle/, signs latest.json
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
- The sidecar tree (python-build-standalone, site-packages, ffmpeg,
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
