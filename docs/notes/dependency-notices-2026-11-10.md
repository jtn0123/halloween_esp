# ESPHome dependency notice review, 2026-11-11 UTC

PR #108 updates the hardware lock to ESPHome 2026.10.2. The firmware run
[42937822223](https://github.com/jtn0123/halloween_esp/actions/runs/42937822223)
compiled the production, carrier and buyer images and passed their size gates.
Its notice check reported only four version mismatches: ESPHome 2026.10.2,
ESP-IDF 5.5.6, esp-audio-libs 4.0.1 and mDNS 1.14.0. It reported no unknown
linked archive or toolchain mismatch. The archive allowlist and toolchain
pin are unchanged. The updated table still needs a fresh CI check against
real maps; the failed run did not retain downloadable firmware artifacts.

## Upstream source checks

The following complete upstream licence files were downloaded and hashed.
Their bytes are identical between the old and new versions:

- [ESPHome 2026.10.2 LICENSE](https://github.com/esphome/esphome/blob/2026.10.2/LICENSE)
  equals 2026.9.1: SHA-256
  `1f312390122725ee382f85af00e3df84f2490cd8fa61b9ea172bc6591cc0ac63`.
- [esp-audio-libs v4.0.1 LICENSE](https://github.com/esphome-libs/esp-audio-libs/blob/v4.0.1/LICENSE)
  equals v3.2.1, with that same SHA-256. Both have Git blob
  `215460a32c17c1b9a83c877c828e3ee7db512295`. The first ten lines still
  match the committed ESPHome licence preamble, SHA-256
  `303c8acbf9c56a1902586c2f40917aaf9da489a52b50ea2d02015fac19b65d03`.
- [mDNS v1.14.0 LICENSE](https://github.com/espressif/esp-protocols/blob/mdns-v1.14.0/components/mdns/LICENSE)
  equals v1.12.0: SHA-256
  `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
  [ESP-IDF v5.5.6 LICENSE](https://github.com/espressif/esp-idf/blob/v5.5.6/LICENSE)
  has that same Apache-2.0 text. No licence choice or copyleft override changed.

The v5.5.6 source copies of the FreeRTOS and HTTP Parser licence files,
FatFs header lines 1-18, and the lwIP submodule's COPYING all match the
committed component text hashes in `notices_model.TEXTS`. No copied text
or expected digest was changed; only the source-version descriptions moved.

## Embedded versions checked beyond the CI mismatch report

- ESP-IDF v5.5.6 pins Mbed TLS at commit
  `2b96dd8eebe880f304c69976b3c2fa0c5100cbb6`.
  Its [build_info.h](https://github.com/espressif/mbedtls/blob/2b96dd8eebe880f304c69976b3c2fa0c5100cbb6/include/mbedtls/build_info.h)
  names **3.6.7**, so the notices also move from 3.6.6 to 3.6.7.
  The version header retains `Apache-2.0 OR GPL-2.0-or-later`.
- The v5.5.6 FreeRTOS task header still names V10.5.1.
- Its lwIP gitlink is `c6f2f878e7b0f86033214b85547d579be43351e3`;
  `src/include/lwip/init.h` still names 2.2.0.
- `components/fatfs/src/ff.h` still names R0.15.
- `components/mbedtls/esp_crt_bundle/cacrt_all.pem` still dates the Mozilla
  certificate data to 2025-02-25, converted by mk-ca-bundle.pl 1.29.

The generated firmware notices and licensing overview now describe these
versions. No checker, licence policy, test skip, or build gate was disabled.
The negative fixture uses ESP-IDF 0.0.0 so that 5.5.6 is no longer both
its supposedly invalid version and the reviewed version.
