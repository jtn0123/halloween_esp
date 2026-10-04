# Production TODO — a castle someone else can own

Started 2026-09-30. Someone may buy a one-off castle. That changes who the
software is for: today it assumes one operator (you), one LAN, an Apple
Silicon Mac with Homebrew, and a repo checkout. A buyer has none of that.

**The plan, in one line:** the castle stands on its own from any browser;
the desktop song tools ship as a **Tauri app** (`.dmg` + `.msi`) — option B —
with a **uv bootstrap installer** (option A) as the fallback if Tauri stalls.

Ordering principle: the castle must be usable, recoverable and updatable
with NO desktop software before any packaging work starts. A buyer who only
runs the show should never need sections 4–7.

Legend: `[ ]` open · `[x]` done · **(blocker)** = cannot hand over without it
· **(decide)** = a question for you or the buyer, not engineering work.

---

## 0. Scope decisions — all answered; in docs/PRODUCTION-DONE.md

## 1. Castle stands alone — firmware (P0)

### 1.1 Wi-Fi onboarding **(blocker)**
Today `firmware/castle.yaml:328` compiles `!secret wifi_ssid/password` in,
softAP is stripped (`CONFIG_ESP_WIFI_SOFTAP_SUPPORT: "n"`), and
`captive_portal` was removed for the S2's flash/dram limits — which no longer
bind (S3: 67.9% flash, 35.2% RAM at v5.69).
- [x] Bring back softAP + `captive_portal` as the first "diet" line restored
      (CLAUDE.md lists it). Measure the flash/RAM delta; `check_image.py`.
- [x] Add `improv_serial` so the web flasher (1.2) can hand over Wi-Fi
      credentials over USB at flash time.
- [x] Build a "buyer" firmware variant with NO baked-in credentials (empty
      secrets file or a `castle_buyer.yaml` that composes the S3 build).
- [x] Decide the AP name/password scheme (e.g. `Castle-XXXX`, printed on a
      label) and what the AP serves (captive portal only).
- [ ] Prove the "Wi-Fi changed / router replaced" path: castle falls back
      to AP after N minutes of failed association, no USB needed. The buyer
      build falls back after 3 minutes (v5.74) — built, not yet proven on
      hardware. *The soak runs it unattended (`--disrupt-cmd`, docs/SOAK.md
      "Wi-Fi loss and return").*
- [x] Hostname: per-unit mDNS name so two castles on one LAN don't collide
      (`castle-feather-s3.local` is fixed today). The buyer build answers
      as `castle-xxxxxx.local` (v5.74).

### 1.2 Browser-based recovery and updates **(blocker)**
A failed OTA today means USB + ESPHome installed.
- [x] esp-web-tools page (static, GitHub Pages) with a `manifest.json` pointing
      at the release `.factory.bin`. Works in Chrome/Edge on Mac and Windows,
      nothing installed. Covers: factory reset, firmware update, Wi-Fi (Improv).
- [x] CI job that builds the buyer firmware and attaches the factory + OTA
      images to a GitHub Release, tag = firmware version.
- [ ] Windows USB driver check: the S3 Feather's native USB (CDC) should
      enumerate without a driver on Win 10/11 — verify on a real Windows box.
- [x] Document "hold BOOT, tap RESET" as the last-resort bootloader entry.
      docs/OWNER-GUIDE.md, "If it will not start at all" (photo TODO).
- [ ] Decide whether the castle's own page offers OTA upload (it already has
      `PUT /api/ota`) as the everyday update path, with the web flasher as
      the recovery path.

### 1.3 Owner-facing controls on the castle's own page
- [x] Audit `sd_web.h`'s page from an owner's eyes: play/stop, scene pick,
      volume, schedule/evening playlist, motion arm, blackout. Anything only
      reachable from the studio or Castle Radio gets a castle-page control or
      is explicitly out of scope. v5.75: the owner's page (`/owner`, and `/`
      with no card site) has the evening show, scenes, stop, blackout,
      volume, the card's songs, motion (when fitted) and every setting.
      Out of scope, written up in docs/notes/06-buyer-build.md §12.24: a
      time-of-day schedule (the clock now exists), PIR scene/cooldown, live
      light override, song import/publish, firmware updates. Built,
      unproven on hardware.
- [x] Settings that are compile-time today and an owner may want to change
      (volume cap, quiet hours, boot behaviour) → runtime prefs in NVS.
      v5.75: `vol_max=` and `quiet=` on `/api/settings` beside `boot_play=`,
      all off until set; quiet hours mute the speaker and leave the lights
      running (decided), and a castle with no clock is never quiet. Built,
      unproven on hardware.
- [x] Boot behaviour: power-on and crash boots autoplay "vigil" (OTA/soft
      restarts are silent since v5.72). Make it a setting; default for buyer.
- [x] PIR: disabled at boot since v5.69 (not wired). Ship wired+enabled, or
      remove the control from the owner page. v5.75: the buyer build has
      `pir_fitted: "false"` — the page says "not fitted", `/api/pir` answers
      409, and the native API has no motion input or PIR switch. The yard
      build is unchanged.
- [x] Castle Radio and the app follow the castle: link its `/owner` page,
      and hide their motion control when `/api/status` says `pir.fitted:
      false`. 2026-10-02: Radio's help card and the desk's castle panel
      link it; both drop the motion controls and say "not fitted".
- [x] A "Report a problem" button on the castle page that bundles
      `/api/status` + `/api/events` into a downloadable text file. v5.75:
      status, health, events and the boot log, with time, version and
      board; page script, no new route.

### 1.4 Device housekeeping
- [x] Time: NTP + timezone setting (needed for any schedule). v5.75: a
      POSIX TZ string in NVS (`tz=` on `/api/settings`, parsed by
      `castle_tz.h`, held to zoneinfo); `/api/status` `local` and the page
      show the castle's local time. SNTP was already on (v5.62). Built,
      unproven on hardware.
- [x] Factory-reset that clears NVS prefs and Wi-Fi without reflashing
      (long-press a button, or a page action).
- [x] Remove developer-only endpoints from the buyer build, or gate them.
      v5.75, audit in docs/notes/06-buyer-build.md §12.24: every HTTP writer
      was already keyed (v5.74); the buyer's native API loses Enter flash
      mode and the motion input, and the PIR settings are internal.

### 1.5 Board support — 4 MB / 2 MB only, room for more later
Decided 2026-09-30: the buyer unit is the 4 MB flash / 2 MB PSRAM S3 (what
`firmware/castle_feather_s3.yaml` already builds), on carrier v3.3a or v3.4.
- [ ] If v3.4 changes pins (amp, strips, PIR, card), add a pin-map package
      per carrier composed with the S3 build; `tools/gen_rig.py` RMT budget
      per carrier (status pixel or not). Same pins → nothing to do.
- [x] `/api/status` reports `board` (carrier + memory) and `fw_variant` NOW,
      even with one variant, so the app's updater (section 9) picks images by
      name from day one and a second variant later is additive.
- [x] Release asset names carry the variant (`castle-v3x-4m2p-vX.Y.ota.bin`).
- [ ] Deferred — 16 MB / 4 MB S3: a memory-profile package (flash size,
      partition table, PSRAM quad vs octal — must match the module), slot
      size per profile in `tools/check_image.py`, CI per variant, one web
      flasher button per variant (esp-web-tools cannot tell flash sizes apart).

### 1.6 Optional password, off by default
- [x] One setting, `password` (empty = off = today's behaviour exactly).
- [x] When set: required for `PUT /api/ota`, `/api/files/` writes, and
      settings changes. Read-only status and playback stay open (decide).
- [ ] ESPHome `ota:` password + `api:` encryption key follow the same
      setting, or stay off; decide when building it. *Partly: decided and
      compiled in v5.74 — the buyer build's OTA (3232) password follows the
      key (`castle_buyer.h`), `api:` encryption stays off — but neither has
      run on a board, and the yard build's OTA password does not follow.*
- [x] Set/clear it from the castle page and from the app; the app stores it
      per castle. Castle page: the firmware's owner page (`/owner`, v5.75)
      and Castle Radio's castle-served Run settings card
      (`localStorage.castleKey`). App: the
      desk's 🔑 section (`/studio/castle-key`) and Castle Radio's card, into
      the one store every client reads (devices.toml, per castle; settings
      `castle_key` / `CASTLE_KEY` pins it) — docs/notes/06-buyer-build.md
      §12.23, proven on `tools/castle_emu.py`, not yet on a board.
- [x] Factory reset (1.4) clears it — on the castle (the NVS erase takes the
      key). No app offers a factory reset, so there is no app copy to forget
      at that moment; the one left remembered is harmless (an unkeyed castle
      ignores the header) and the owner's clear/use replaces it.
- [x] Emulator (`tools/castle_emu_wire.py`) + `tests/test_firmware_contract.py`
      learn the header in the same commit as `sd_web.h`.

## 2. Stability — freeze and soak (P0)

- [ ] Pick the release firmware version; freeze features for it.
- [ ] Door ring frame corruption — `docs/ISSUE-ring-flicker.md` next tests;
      close it or document it as known.
- [ ] Upload watchdog cadence (v5.42, every 32 KB) is verified on the
      emulator only — exercise a large card push on hardware.
- [ ] `docs/ISSUE-scene-start-audio.md` — close or document.
- [ ] 72-hour soak on the buyer's hardware: evening playlist on a loop,
      scheduled starts, motion triggers if wired. Log `/api/events`, uptime,
      heap, Wi-Fi RSSI drops. Make a tool do the logging (no manual watching).
      *Tool built: `make soak HOST=… HOURS=72` (tools/soak.py, verdict +
      JSONL, thresholds in docs/SOAK.md), emulator-tested with injected
      faults; not yet run on hardware.*
- [ ] Power-cycle torture: 50 cold boots via a smart plug; every one reaches
      the show and the card mounts. *Tool built: `make power-cycle HOST=…
      OFF=… ON=…` (tools/power_cycle.py), tested on a fake plug; not yet run
      on hardware.*
- [ ] Wi-Fi loss/return: router reboot mid-show; castle reconnects unaided.
      *Tool built: the soak's outage detection + `--disrupt-cmd` on a
      router plug (docs/SOAK.md); not yet run on hardware.*
- [x] SD card, castle side: corrupt/missing card behaviour is graceful (page
      says so, no boot loop). v5.75: "No SD card — the show is on the card"
      and a no-show card on the owner's page; `web_check --boot` boots over
      no card, a songs-only card and seven broken manifests under
      ASan/UBSan (`tests/test_firmware_boot_cxx.py`). Built, unproven on
      hardware.
- [ ] SD card, on the board: pull the card and boot, boot a blank one.
      Spare pre-loaded card in the box.
- [ ] Brownout check at full brightness + full volume on the shipped PSU.
- [ ] Thermal: an evening's run inside the closed castle.
- [x] Watchdog/crash reporting: reset reason surfaced on the page. v5.75:
      in plain words on the owner's page, a warning for a crash, watchdog,
      brownout, power glitch or lock-up, and the boot/crash counts. Built,
      unproven on hardware.

## 3. Handover package (P0)

- [x] Owner's guide (1–2 pages, not `RUNBOOK.md`): power on, join Wi-Fi,
      open the page, pick a show, what the lights mean, what to do when it
      misbehaves, how to update, how to factory reset. docs/OWNER-GUIDE.md;
      2026-10-03: in-app update, Castle Tools' key card, adding a song and
      Find my castle written in, and 13 pictures in docs/guide/ made by
      `make guide-shots` (emulator + Castle Radio on a pretend LAN, held to
      the guide by tests/test_guide_shots.py). Still for a person with the
      hardware: the phone's Wi-Fi list, the flasher's Wi-Fi step, the
      BOOT/RESET photo, macOS Open Anyway and Windows SmartScreen — each
      named in an HTML comment where it goes.
- [x] Labels: AP name/password, URL, recovery-page URL, QR code to the guide.
      `tools/unit_label.py --mac … | --host …`: a 4×6 in card and a
      2.25×1.25 in sticker, self-contained HTML; names derived as the
      firmware does (pinned by tests/test_unit_label.py), open hotspot said
      as such, QR by `tools/qr_code.py` (no dependency). docs/SUPPORT.md.
- [x] SD card ships with NO songs (decided): scenes that need no track
      (light-only or synth audio) or an empty show. The castle page and the
      app must look sensible with zero songs — first-run "add your first
      song" state, not errors. Re-render `make publish` from that show.
      Zero songs done 2026-10-02 (/owner v5.76, Radio, desk). The show done
      2026-10-03: `scenes/shipped.yaml` (`tools/shipped_show.py`) is the
      yard's eight synth scenes, and it seeds the app and the installer.
      `make buyer-card` writes the whole card into a directory with no
      castle. The evening playlist is read from the card (v5.77), so no
      image names a song. Castle Radio's castle page hides built-in rows
      whose audio the card lacks. docs/notes/06-buyer-build.md §12.25.
- [ ] Electrical: PSU rating, which connector is which, no user-serviceable
      wiring (`docs/WIRING*.md` is for you, not them).
- [ ] Licences: Demucs (MIT) + htdemucs weights, ffmpeg (LGPL build only —
      no GPL/nonfree codecs if redistributed), ESPHome (MIT/GPL parts),
      castle-core, fonts. A `THIRD-PARTY-NOTICES` file in every bundle.
      2026-10-02: every PUBLISHED bundle carries one (firmware asset + web
      flasher, desktop app, castle-core zips, source zip), generated by
      `tools/third_party_notices.py` and gated by `make check` —
      docs/LICENSING.md. 2026-10-03: the sold castle's card carries the
      firmware notices and a GPLv3 §6 b) written source offer
      (`make buyer-card`), and the owner page links both (v5.77) —
      LICENSING.md decision 2, implemented. Left open: the repo has no
      licence, and the htdemucs weights have no terms (LICENSING.md "Open
      decisions" 1 and 4).
- [x] URL import STAYS (decided) — it is how the buyer gets songs. yt-dlp
      breaks whenever sites change: ship it as a separate standalone binary
      the app updates on its own schedule (yt-dlp releases often), not frozen
      inside the app bundle. A failed download says "update the downloader"
      with a button, not a stack trace.
      2026-10-02: `tools/ytdlp_update.py` fetches yt-dlp's latest release
      into per-user data (`exe_paths.downloader_dir()`) and checks it against
      that release's SHA2-256SUMS. Every importer runs that copy first. It
      updates when the owner presses Update the downloader in Castle Radio
      (queued behind imports, never mid-import) or when the installer runs
      `--update`/`--repair`. There is deliberately no background schedule.
      An old or missing downloader carries the button. The cue desk (hidden
      for the buyer) shows the sentence without it. desktop/README.md "The
      song downloader".

---

## 4. Cross-platform core — make it compile and run on Windows (P1)

Everything here is needed for BOTH option A and option B.

### 4.1 Rust (`core/`) — compiles on Windows; done, in docs/PRODUCTION-DONE.md

### 4.2 Python and 4.3 CI — done, in docs/PRODUCTION-DONE.md

## 5. Option B — the Tauri desktop app (P1, primary)

### 5.1 Architecture
- [ ] **One server for the buyer.** Today: Rust studio on 8765 + Python
      Castle Radio on 8871. Decide which UI the buyer sees (likely Castle
      Radio's player + import, the cue desk hidden) and make the app talk to
      ONE local server. Long-term: port Castle Radio's routes into the Rust
      studio so Python is only a worker, never a server.
- [ ] Tauri shell embeds the studio as a library (not a child process) — the
      Rust server already exists; `tauri::Builder` hosts the webview pointed
      at it. Or keep it a sidecar if that is less churn; decide.
- [x] Webview differences: WebKit (macOS) vs WebView2 (Windows). Run the
      Playwright suite against WebKit too; audio/blob/wake-lock APIs checked
      on both. `make e2e` and CI's `web` job run every spec in Chromium (as
      WebView2 is) and then WebKit. Audio and Blob uploads pass in both; the
      page's real Safari gaps were 11px buttons and a styled select's 18px
      height on a phone (previewer/mobile.css). Wake lock is Castle Radio's
      (castle-direct.js), feature-tested, and outside this suite.
- [x] The `castle-tools://` URL handler + popup bridge (castle page ↔ local
      helper) → Tauri deep-link plugin; retire `tools/castle_launcher.swift`.
- [x] Menu-bar ♜ icon → Tauri system tray (works on both OSes).
- [x] Logs: `~/Library/Logs/Castle Tools/` → Tauri app-log dir per OS.

### 5.2 Sidecars
- [x] Python: NOT bundled (decided 2026-10-03). The app carries the tree,
      castle-core and a pinned uv (`tools/desktop_bundle.py`, ~48 MB); its
      first launch runs the option-A installer under a uv-managed 3.13 into
      per-user app data (~1.7 GB), with progress and Repair (desktop/README.md
      "First launch"). `tools/desktop_smoke.py` proves it per release on both
      runners (dry run 37146724040: set up in 23 s / 50 s, then 2 s).
- [x] ffmpeg: downloaded at first launch (`--ffmpeg download`), not bundled.
- [ ] Demucs: fetched at first launch, not bundled (docs/LICENSING.md, open
      decision 4: the weights have no licence statement). Windows speed:
      measured 2026-09-30 on an M4, 4:06 song, `--two-stems vocals`:
      GPU (mps) 65 s · CPU defaults 133 s · CPU `--overlap 0.1 -j 4` 65 s.
      A mid-range Windows laptop CPU is expected to be slower than an M4.
  - [x] Tune `tools/stems.py` for CPU: `--overlap 0.1`, `-j <cores>`; check
        the stems still drive the same lights (quality A/B) before adopting.
        Done 2026-10-01 as OPT-IN only (`--fast-cpu` / `CASTLE_DEMUCS_FAST=1`):
        the A/B moved the bass stem too far to make it the default.
  - [ ] Run separation in the background with progress; never block import.
  - [ ] NVIDIA present → CUDA torch (large extra download; optional).
  - [ ] Later: drop PyTorch for an ONNX export of htdemucs on ONNX Runtime
        + DirectML (any DX12 GPU: Intel/AMD/NVIDIA), or demucs.cpp on CPU.
        Removes the biggest dependency and gives most Windows PCs a GPU path.
- [x] Rust bins: carried as `castle/bin/`; `--core-from` places them, and
      `CASTLE_CORE_BIN_DIR` lets `tools/core_bins.py` run them without cargo.

### 5.3 Data location
- [x] Today everything lives in the repo checkout (`tracks/`, `scenes/`,
      `audio/`, `.venv-desktop`). The app needs per-user dirs: tracks +
      scenes + build output under the OS app-data dir. The `CASTLE_TRACKS` /
      `CASTLE_SCENES` / `CASTLE_BUILD` knobs already exist — the app sets them.
- [x] First-run: seed scenes.yaml from the shipped show; never touch a repo.
- [x] Settings file: castle address (mDNS name + manual IP), last port.

### 5.4 Ship it
- [ ] macOS UNSIGNED from GitHub Releases (decided): Apple Silicon still
      needs an ad-hoc signature (Tauri's build applies one). A downloaded
      unsigned app is quarantined — first launch is System Settings →
      Privacy & Security → "Open Anyway" (Sequoia+; right-click → Open no
      longer bypasses it). Owner's guide gets screenshots of exactly that.
      Updates fetched BY the app are not quarantined, so it is a one-time step.
- [ ] Windows UNSIGNED (decided): SmartScreen "More info → Run anyway" on
      first install; document with screenshots. Prefer the NSIS `.exe`
      per-user installer (no admin prompt) over `.msi`.
- [ ] Auto-update: section 9.
- [x] CI release workflow: tag → build dmg/msi on macOS + Windows runners →
      sign → Release. Firmware images attached to the same Release (1.2).
- [x] Uninstaller leaves the user's tracks unless asked. Tauri's stock NSIS
      uninstaller deletes app data only when its unticked box is ticked;
      tests/test_desktop_uninstall.py holds the config to that template, and
      desktop/README.md says what removing the app on macOS keeps.

## 6. Option A — uv bootstrap installer (P1, fallback)

Worth doing first even if B is the goal: it forces sections 4 and 5.3 to be
true, and it is the dev/support path forever.
- [x] `install.sh` (macOS) and `install.ps1` (Windows): install `uv` → `uv
      python install 3.13` → `uv sync` from `requirements-desktop` (lockfile
      with hashes, like `requirements.lock`).
- [x] Prebuilt Rust bins downloaded from the matching GitHub Release (no
      cargo on the buyer's machine); checksum verified.
- [x] ffmpeg: winget/brew if present, else a pinned static download.
- [x] Replace Homebrew assumptions in `tools/install_castle_tools.sh`.
      Retired instead: `installer/install.sh` does all of it without
      Homebrew, and every pointer to the old script now names the installer.
- [x] Launchers: `Castle Tools.command` (mac) + `Castle Tools.bat`/Start-menu
      shortcut (Windows) that start the server and open the browser.
- [x] `--repair` and `--uninstall` flags; idempotent re-run.
- [x] Works from a downloaded zip of the release, not only a git clone.

## 7. Desktop-tool stability — done, in docs/PRODUCTION-DONE.md

### 4.4 Windows hands-on pass (an Opus agent on your Windows PC)
- [x] Fresh Windows user account: install from a Release, first-run, import
      a song (mp3 + wav + a non-ASCII name), separate stems, sync, play.
      2026-10-03: `.github/workflows/install-smoke.yml` does it on
      windows-latest and macos-14 (PRs that touch the installer or Castle
      Radio, weekly, by hand): the real install.ps1/install.sh from a
      git-archive tree with no cargo or Python on PATH, castle-core from a
      staged release zip, twice and as a dry run; then Castle Radio's own
      API against `tools/castle_emu.py` — three imports, one split, sync,
      the card checked file by file, a song played; uninstall keeping the
      songs, then purging them. `tests/install_smoke.py` is the driver.
- [x] Time Demucs per song on that CPU (compare: M4 CPU 133 s / tuned 65 s).
      Each install-smoke run splits a 10 s clip on the runner's CPU and
      puts the separate step's wall time in the run summary. 2026-10-03:
      windows-latest (EPYC 7763, 4 cores) 12.0 s, macos-14 (M1, 3 cores)
      12.0 s — about 1.2× the audio's length; the whole split step 23.4 s
      and 17.4 s. A buyer's own CPU is still unmeasured.
- [ ] Still a person's job, no software stands in: SmartScreen on the
      downloaded installer, and an import from a real network share.
- [ ] Flash a castle from the web flasher in Edge; check the USB driver.
- [ ] Auto-update from one Release to the next.
- [x] Run the Rust + Python suites natively; file what fails as follow-up.
      §4.3's cross-platform.yml does it on windows-latest (cargo test,
      `run_checks.py test` and `test-radio`), green and blocking.

## 8. Repo and process

- [x] Branch strategy: buyer releases tagged from `main`; a `release/x.y`
      branch if fixes must ship without new features. docs/SUPPORT.md,
      "Release branches".
- [ ] Version numbers: one release number for app + firmware + card format,
      shown in the app, on the castle page, and in the owner's guide.
      In the app: the splash and Castle Radio's tools card and diagnostics
      show the release tag; docs/SUPPORT.md "Which release is it" maps a
      firmware number to its tag. Left: the castle page (firmware, which
      knows only its own number) and the owner's guide.
- [x] `docs/RUNBOOK.md` gains a "supporting a buyer's castle" section.
      It is docs/SUPPORT.md (reading a report and a soak log), linked from
      RUNBOOK.
- [x] Repo is public: confirm nothing personal ships (Wi-Fi secrets, your
      `devices.toml`, `tracks.json` provenance with your file paths).
      `tools/ship_guard.py`: both files are export-ignore and skipped by the
      installer, no castle address is built into Castle Radio or a launcher,
      and the tree (tests/test_ship_guard.py, every `make test`) and each
      release's assets (release.yml) are scanned for secrets, a keyed
      inventory, home directories, MACs and private-LAN addresses.
- [x] Per-unit record: serial/MAC, firmware version, date, buyer — kept by
      you, not in the repo. The template is docs/SUPPORT.md.

---

## Suggested order

1. Section 0 answers.
2. 1.1 Wi-Fi onboarding + 1.2 web flasher — the castle stands alone.
3. Section 2 soak, section 3 handover package. **A run-only buyer is done here.**
4. Section 4 Windows port + CI, then 5.3 data location.
5. Section 6 (option A) as the first working cross-platform installer.
6. Section 5 (option B) on top of the same pieces; section 7 throughout.

## 9. Updates from GitHub (app, firmware, show format)

The repo is public, so the app reads Releases with no token (60 API
calls/hour unauthenticated — check once a day and on launch, not more).
- [ ] One Release = one version of everything: Tauri bundles (mac arm64,
      mac x64?, Windows x64), `latest.json` for the updater, firmware images
      named by variant (1.5), web-flasher manifest, checksums.
      *Built end to end by the release dry run on 2026-10-03 (run
      37142954369, every job green); not yet published from a tag.*
- [x] Tauri updater: its own update-signing keypair (minisign — free, NOT
      code signing; required even for unsigned apps). Private key in a
      GitHub Actions secret; losing it strands installed apps. Made
      2026-10-03: pubkey committed, both secrets set, originals kept off
      GitHub (docs/RELEASING.md); dry run 3's signatures verify.
- [x] Release workflow: tag `vX.Y` → build on macOS + Windows runners →
      build firmware → upload all assets → publish `latest.json` last.
- [x] Channels: `stable` only for the buyer; pre-releases ignored unless a
      hidden setting opts in (so you can test on your own castle first).
      Done: `"prerelease": true` / `CASTLE_PRERELEASE`, one rule
      (`tools/release_channel.py`, `channel.rs`) for app, installer, castle.
- [x] Firmware update from the app: compare `/api/status` firmware +
      `board` with the Release, download the matching OTA image, verify
      checksum, stop audio, `PUT /api/ota`, confirm the new version on
      `/api/status`. Never auto-flash — the owner presses "Update castle".
      Done: `tools/castle_update.py` + Castle Radio's card; refuses another
      board or build, says a rollback. Not yet run on the real castle.
- [x] Show/card format: app refuses to publish a format newer than the
      castle's firmware reads; tells the owner to update the castle first.
      Done: `fw_formats.refusal` — sd_sync (so `make publish`, the studio), Radio.
- [ ] Rollback: the previous Release stays downloadable; firmware keeps
      ESP-IDF's two OTA slots, so a failed boot rolls back on its own.
- [x] Option A (uv installer) path: `--update` pulls the latest Release
      zip + bins, same `latest.json`; a "new version" banner in the page.
- [x] Never break an installed buyer: old app versions keep working
      against new Releases (the update check is the only contract). Done:
      docs/RELEASING.md "What an installed app reads" +
      `tests/test_release_contract.py`.
