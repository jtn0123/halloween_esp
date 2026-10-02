# Integrated firmware reference

`castle_v34.yaml` is the exact previously compiled Integrated overlay: 16 MB flash,
8 MB octal PSRAM, SD GPIO39/40/41 and stereo I2S. Archived provenance records
ESPHome 2026.8.1 / ESP-IDF 5.5.5 and the successful September 29 compile.
No firmware binaries or credentials are included here.

It includes `castle.yaml` and `castle_sd_common.yaml` and needs the application's
entire firmware dependency tree. It is a reference, not a standalone build or
qualification of newer application revisions. Prepare an isolated build beside
matching packages, use dummy credentials for validation, and verify merged
pins/memory/audio before compiling. Device credentials belong in ignored `secrets.yaml`.

The original compile/QA scripts remain in the USB workspace. The handoff's
`../validate.sh` checks CAD and package integrity only. Printed v3.3a retains
`firmware/castle_feather_s3.yaml` and its existing mono profile. Hardware selection
is at build time; a runtime toggle does not make these binaries interchangeable.
