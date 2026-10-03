# Halloween Castle — Build — the buyer image, the castle key, the owner's page, the shipped show (§12.23–12.25)

Part of the design record; the index is [`PROJECT_NOTES.md`](../../PROJECT_NOTES.md). Section numbers are global across the parts, so `§12.9` means the same thing in every file.

---

## 12.23 The buyer build, the castle key and boot_play (v5.74, 2026-09-30)

**What.** `firmware/castle_buyer.yaml` is `castle_feather_s3.yaml` with no
Wi-Fi compiled in: softAP `Castle-XXXX` (the MAC's last two bytes) +
`captive_portal`, `improv_serial` for the web flasher, `ap_timeout: 3min` so a
lost router brings the AP back, `name_add_mac_suffix` for a per-unit mDNS
name. Both builds gain `sd_web_prefs.h`: an optional castle key (`X-Castle-Key`,
empty = off = v5.73 exactly) guarding card writes, `/api/ota`, `/api/pir` and
the new `/api/settings` / `/api/key` / `/api/factory-reset`; `boot_play` in NVS
(yard default on, buyer off); `/api/status` adds `board`, `fw_variant`,
`locked`, `boot_play`. The buyer's ESPHome OTA (3232) password follows the key
(`castle_buyer.h`); the native API's encryption stays compile-time and off.

**Cost, compiled 2026-09-30, NOT on hardware.** Yard: image 1,253,008 B
(68.3%, +3,713 over v5.73's 1,249,295), static RAM 120,851 B (35.4%, +64).
Buyer: image 1,328,720 B (72.4%, +75,712 over the yard), RAM 121,519 B (35.6%,
+668) — softAP back in the Wi-Fi blob (`CONFIG_ESP_WIFI_SOFTAP_SUPPORT: y`,
the first diet line given back, buyer only), the portal page, its DNS server,
web-server OTA (which `captive_portal` auto-loads: open, but only while the AP
is up) and Improv.

**Port 80.** The portal's httpd and `castle_web` both want :80 and IDF's
default control socket, so `castle_buyer::share_port_80()` (1 s interval)
stops the castle's control server while the portal is active and restarts it
when the portal ends; the :8080 stream server is untouched. Unproven on the
board — the first buyer flash must watch both directions.

### The castle key's one store (2026-10-02)

**Decision.** A castle's key lives in `devices.toml` — the inventory
`tools/hosts.py` already read for a castle's address — as a `key` on the
entry whose `host` or `fallbacks` name the castle. The file is
`CASTLE_DEVICES` when that is set and non-empty, else the repo's own. Every
client reads it by one rule, `CASTLE_KEY` first: `hosts.castle_key` in
Python (sd_sync, castle_link, Castle Radio) and `core/src/hosts.rs` in the
studio's relay and the `castle` bin, held together by
`tests/test_castle_key_rust.py` (docs/PARITY.md). The installed apps point
`CASTLE_DEVICES` at a per-user file — the Tauri app at
`<app data>/radio/devices.toml` always, the uv installer at
`data/devices.toml` — and pass settings.json's `castle_key`, when set, as
`CASTLE_KEY`. A dev checkout keeps the repo's file.

**Why this file and not a new one.** The key belongs to a castle, and the
castle's identity — its address, its fallbacks — was already there: a second
store keyed by host would have to agree with the first about which castle a
host is, in three languages. One file, one match rule, one reader per
language, and Tauri needs no reader at all (it only sets the two variables).

**Why `CASTLE_KEY` wins and pins.** The settings file is the owner's
deliberate word, and an environment variable cannot be written back. So with
it set the key routes (`/studio/castle-key`, Castle Radio's
`/radio/device/key`) refuse with 409 rather than change the castle to a key
that nothing would then send.

**One writer.** `tools/castle_keys.py` is the only code that writes a key:
line-level edits that keep the owner's comments, every write read back
through `hosts.py` before it replaces the file, a missing file created
0600, a castle the file never named given a marked table that `forget` takes
away whole. `CASTLE_DEVICES` moves the store but cannot rename it: the
writer resolves symlinks and refuses anything but a `devices.toml` in a
folder that already exists (both apps make theirs at start-up), so a
mistyped or hostile value cannot aim it at another file. Castle Radio imports it; the studio spawns it with the key on
STDIN, because argv is visible to every user on the machine. Neither the
writer nor any reply, error or log line carries a key — the access log
prints a relayed `/api/key` without its query.

**The public repo.** `devices.toml` is tracked and the repo is public, so a
key written into a dev checkout's file would be one `git add -A` from
GitHub: the pre-commit hook runs `castle_keys.py check-staged` and refuses a
staged `devices.toml` that carries one. A dev who wants a key and a clean
tree sets `CASTLE_DEVICES` to a file outside the repo.

**The castle-served page.** Castle Radio served BY the castle has no
computer behind it: it keeps the key in that browser's
`localStorage.castleKey`, the name the firmware's fallback page already
used (and its successor, v5.75's owner page, still does), so a key entered
on either page opens both.

---

## 12.24 The owner's page, the clock and the limits (v5.75, 2026-10-02)

**What.** PRODUCTION-TODO §1.3, §1.4 and §2's castle side, in both builds:

- **The owner's page** (`sd_web_owner.h`), in flash like `/remote`, because
  it is the page you need when the card is the problem: `/owner` always, and
  `/` whenever the card has no site — no card, a blank card, an unpublished
  one. It replaces v5.74's fallback page, and with it the scene-id splice
  that page needed (`kFallbackSceneIds[]`, `castle_emu_flash.py`'s copy).
- **Why it last restarted**, in plain words, from `/api/health`'s
  `last_reset`: a warning box for a crash, a watchdog, a brownout, a power
  glitch or a CPU lock-up (`castle_health.h` `was_crash()`, which gained the
  last two), plus the boot and crash counts. Every reason ESP-IDF 5.5 has
  now has a word; `tests/test_firmware_contract_owner.py` holds the C, the
  shim's enum, the emulator and the page's table to one list.
- **Report a problem** is page script, not a route: status, health, events
  and the boot log, stamped with the time, version, board and build, saved
  as `castle-report-<time>.txt`. Nothing new on the castle to get wrong.
- **No card** is said on the page ("No SD card — the show is on the card"),
  and so is a card with no `scenes/show.man`. The castle boots either way
  on the compiled-in scene ids and their built-in looks. "No card" and "a
  card FATFS cannot mount" are the same failed call on the board
  (`format_if_mount_failed` is false, so a bad card is never wiped).
- **A timezone**, `tz=` on `/api/settings`: a POSIX TZ string in NVS, parsed
  by the castle's own `castle_tz.h` (ESPHome 2026.8 fixes the zone at
  compile time and newlib turns a bad one into UTC without a word), held to
  Python's zoneinfo in C and in the emulator. `/api/status` adds `tz` and
  `local` ("YYYY-MM-DD HH:MM", empty until SNTP has set the clock). The
  page offers fifteen zones or a pasted string.
- **A volume cap**, `vol_max=` 1–100: the loudest the castle may be, by any
  path — `/api/volume`, a scene's own level, the evening playlist. Lowering
  it pulls the speaker down on the next 200 ms tick; raising it gives
  nothing back, since it is a ceiling and not a level.
- **Quiet hours**, `quiet=HH:MM-HH:MM` local, crossing midnight if it
  likes, `off` to end. **Decided: the lights keep running; only the
  speaker goes quiet.** "Lights off at night" was already a blackout or a
  stopped show, and "alive but silent" was not something the castle could
  do. The level the window took, or the last one asked for during it, comes
  back when it ends. **No clock, no quiet hours**: a castle on a network
  with no internet cannot know it is 3 am, and must not be mute for ever.
- **Off by default, all three**: no zone is UTC (what the clock already
  was), a cap of 100 is no cap, no window is no quiet. The yard castle
  behaves as v5.74 did until someone saves a setting.
- **The PIR** (`pir_fitted`, false in `castle_buyer.yaml`): `pir.fitted` in
  `/api/status`, the page says "Motion sensor: not fitted", `POST /api/pir`
  answers 409 "no motion sensor" — after the key check, so a locked castle
  does not say what it has. The yard keeps its sensor and its switch.

**The developer-endpoint audit (§1.4).** The HTTP surface needed nothing
compiled out: every route that writes was keyed in v5.74, and every open one
is a read or the playback §1.6 decided to leave open.

| Door | Who uses it | Buyer build |
| --- | --- | --- |
| `GET /api/status`, `/health`, `/events`, `/bootlog`, `/api/files`, `/sd/*`, `/site/*`, `/`, `/owner`, `/remote`; `:8080` stream | the pages, the app, Castle Radio | open: reads |
| `POST /api/play`, `/scene`, `/stop`, `/show/start`, `/show/stop`, `/blackout`, `/volume`, `/light` | the pages, the remote, the desk | open: playback (§1.6); volume held to the cap and quiet hours |
| `PUT`/`DELETE /api/files/*`, `/site/*`, `/scenes/*`; `PUT /api/ota` | the app's publish and updater | castle key |
| `POST /api/settings`, `/key`, `/factory-reset`, `/pir` | the owner's page, the app | castle key (`/pir` then 409) |
| ESPHome OTA :3232 | nothing in normal use | password follows the key (§12.23) |
| captive portal + its web OTA, :80 | first setup | open, and only while the AP is up |

The native API (:6053, Home Assistant's door) takes no key at all, so the
buyer build drops what does not belong on it (`castle_buyer.yaml`, `!remove`
and `!extend`): **Enter flash mode** (one way into ROM download mode; a
buyer's recovery is the web flasher, not a button anyone on the LAN can
press) and the **Walkway motion** input, with **PIR armed / cooldown /
scene** made `internal`. What stays is what the open HTTP routes already
offer — the scenes, Blackout, the three lights and their effects, the
install trims, Soften lightning, Play SD file — or a diagnostic: SD card
present, Dump boot log, List SD card, Remount SD. Whether the API itself
follows the key is §1.6's open line, not this one.

**The owner's-page audit (§1.3).** On the page: start and stop the evening
show, stop a scene, every scene on the card, blackout, volume, the songs on
the card, the motion sensor (when fitted), boot behaviour, the cap, quiet
hours, the zone, the key, factory reset and the report. Out of scope, said
here so it is not missed: a **schedule** (time-of-day start; the clock it
needs exists now), the PIR's scene and cooldown (no sensor on the buyer's
castle; the yard sets them from the desk), live light override and the cue
desk, importing and publishing songs (the app), and firmware updates (the
app's updater and the web flasher).

**Cost, compiled by CI 2026-10-03 (ESPHome 2026.9.0), NOT on hardware.**
Yard: image 1,260,240 B (68.7%, +11,920 over v5.74), static RAM 120,979 B
(35.4%, +144). Buyer: image 1,333,328 B (72.7%, +9,872), RAM 121,299 B
(35.5%, −204) — the native-API removals alone gave back 2,048 B of image
and 332 B of RAM (a local 2026.8.1 compile). firmware/pending/README.md
names the runs.

**Proved where.** `tests/test_firmware_owner_cxx.py` (the real C and the
emulator, byte for byte: every setting, every refusal, the cap, a US Eastern
castle whose quiet hours open on Halloween night in EDT and close in EST,
no clock, no sensor, a brownout); `tests/test_firmware_boot_cxx.py`
(`web_check --boot` runs on_boot's order under ASan and UBSan over no card,
a songs-only card, seven broken manifests and a good-card control — every
one boots, answers the owner's page's questions and says what is wrong);
`web/test/e2e/owner.spec.ts` (the page's own script in Chromium). **Built,
unproven on hardware**: no board has booted v5.75, pulled a card, browned
out or crossed a quiet-hours boundary.

## 12.25 The shipped show, the buyer's card and the licences on it (v5.77, 2026-10-03)

**One shipped show.** A sold castle carries no songs (PRODUCTION-TODO §3),
and the yard's `scenes/scenes.yaml` has two scenes built on imported
tracks. `scenes/shipped.yaml` is that file minus every scene that needs a
track: an `audio_file`, a `track_*` key, or a pulse synth on `onset_*`
(`tools/shipped_show.py`, which `gen_esphome.py` re-runs on every real
generate, so it cannot fall behind). The text above `scenes:` is kept
verbatim and whole scene blocks are dropped, so the file reads like the
show it came from. It is generated and tracked, and exempt from the
500-line rule with its generator named. Everything that hands a buyer a
show reads it: the desktop app's first-run seed (`runtime.rs
seed_scenes`), the uv installer (`desktop_env.SHIPPED_SCENES`) and the
card (`make buyer-card`). The source zip still carries `scenes.yaml`,
because it is the source `make build-buyer` generates from. It holds the
song scenes' YAML and none of their audio. `tests/test_shipped_show.py`
fails on a stale file, a track scene, a `tracks/` path, or a
firmware-named scene that is not in the show.

**The buyer's card, made with no castle.** `make buyer-card [OUT=…]
[TAG=…]` (`tools/buyer_card.py`) renders and generates the shipped show in
a sandbox: scratch `CASTLE_SCENES` and `CASTLE_BUILD`, an empty
`CASTLE_TRACKS`, an empty `CASTLE_HOST`. It writes into a directory:

- `scenes/show.man`, each `<id>.cue` and each `NN_<id>.mp3` at
  card_bitrate, the files `sd_sync scenes` pushes;
- `licenses/THIRD-PARTY-NOTICES.txt`;
- `licenses/SOURCE-OFFER.txt`.

There is no `site/`, so `/` is the owner's page. It refuses, writing
nothing, when:

- a scene needs a track;
- the build made a file it did not expect;
- a format needs a newer castle than this tree's firmware;
- the target holds anything that is not an earlier card.

The 2026-10-03 card is 19 files and 1,992 KB. Every file under `scenes/`
is byte-identical to what a sandboxed `make publish` of the same show
sends: published to the emulator holding the card, `sd_sync` skipped every
mp3 and cue as unchanged.

**The evening came off the image.** Before 5.77, `show_playlist` was
generated per scene id, so every image named the yard's two songs. A
castle sold with the shipped card would have run into two "missing"
scenes on every lap of the evening. The list is the card's now:

- `show.man`'s header flag bit 0 marks that each row's last byte, the old
  pad, holds a 1-based place in the evening;
- `show.order`, or every non-motion scene, decides the places;
- `castle_scenes::evening_csv` reads them, and the script plays one scene
  per pass and re-executes itself.

An unmarked manifest plays every row but `pir_scene`, which is 5.76's
evening for the yard's show. With no manifest, the castle plays
`kFallbackEveningCsv`, and the fallback ids are now the shipped show's
only. That makes the yard's and the buyer's generated firmware identical.
The manifest's VERSION is unchanged, so firmware before 5.77 reads a new
card exactly as before.

**The licences (docs/LICENSING.md decision 2, both halves).** The card
carries the firmware notices and a GPLv3 §6 b) written offer. The offer:

- is dated, and valid three years or while parts or support are offered,
  whichever is later;
- is made to anyone who possesses the firmware;
- offers network access (this repository at the release tag, its "Source
  code" zip, and the upstream addresses in the notices) or a physical copy
  at cost, asked for through the repository's issues;
- carries the Installation Information: USB needs no key and no
  signature, there is no secure boot, and a USB install that erases the
  castle clears the castle key. The page's Factory reset is not the
  answer here, because it needs the key itself.

The owner's page links both files at its foot when the card holds them.
`.txt` is served as `text/plain`, so a browser shows it instead of
downloading it. docs/OWNER-GUIDE.md says where they are.

**Cost, compiled locally 2026-10-03 (ESPHome 2026.9.0), NOT on hardware.**
Yard: image 1,260,144 B (68.7%, +16 B over 5.76), static RAM 120,475 B
(−504 B: the playlist's chain of actions per scene is gone). Buyer: image
1,333,344 B (72.7%), factory image 1,398,880 B, RAM 120,795 B (−504 B).

**Proved where.**

- `tests/test_evening_cxx.py` runs the real `castle_scenes.h` against
  `scene_manifest.evening_ids` for marked, legacy, empty, invalid and
  missing manifests, and walks the list.
- `tests/test_firmware_web_licences.py` sends the C and the emulator the
  same licence requests and compares the full type table.
- `tests/test_buyer_card.py` checks the card file by file.
- `web/test/e2e/owner-first-run.spec.ts` shows the links appearing.

**Built, unproven on hardware**: no board has run an evening from a marked
manifest or served the licence files.
