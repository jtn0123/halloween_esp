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
