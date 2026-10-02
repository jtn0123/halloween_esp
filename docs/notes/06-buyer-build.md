# Halloween Castle — Build — the buyer image, the castle key, boot_play (§12.23)

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
away whole. Castle Radio imports it; the studio spawns it with the key on
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
used, so a key entered on either page opens both.
