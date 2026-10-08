# Integrated firmware validation

The Integrated overlay selects firmware label 5.78, 16 MB flash, octal PSRAM, SD GPIO39/40/41 and stereo I2S. It was compiled successfully on October 7 against application commit 3078e35 with ESPHome 2026.9.1 and ESP-IDF 5.5.5. See [the build receipt](../qa/firmware-build-20261007.json). The source hashes and binary hashes bind that evidence; actual flash/PSRAM detection and playback still require a board.

From the Integrated directory run `bash firmware-reference/verify.sh /path/to/halloween_esp`. Use the repository's pinned dependencies and generate its ignored audio assets first with its documented build workflow. The script copies a fresh firmware tree into ignored `local-checks/firmware`, uses dummy credentials, compiles and records stereo/pins/resampler/codecs, octal/flash SDK settings, source hashes and image hashes. It never flashes hardware. Set `ESPHOME_CLI` if the command is outside the source repository's `.venv`; cache locations can be overridden through the named environment variables in the script.

A fresh checkout of this PR provides the merged application source and this overlay. Builds on another date/machine can have different image hashes because build metadata is embedded. Credentials used for actual devices belong in ignored private files; the dummy validation image is not a commissioned device image.

Printed v3.3a retains `firmware/castle_feather_s3.yaml` and its existing mono behavior. These binaries use different hardware profiles; the Integrated overlay is not a runtime switch or an image to flash onto v3.3a.
