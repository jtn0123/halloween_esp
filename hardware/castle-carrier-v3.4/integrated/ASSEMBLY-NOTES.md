# V3.4 Integrated assembly requirements

Use the soldered **ESP32-S3-WROOM-1-N16R8** module. This footprint and firmware overlay are for the 16 MB flash / 8 MB octal PSRAM variant. SD uses GPIO39/40/41; do not substitute the Feather overlay or the old GPIO35/36/37 SD assignment.

## Selected fabrication process — 2026-10-01

Keep **ENIG with 1 microinch gold** for the Integrated prototype batch, matching the September 30 JLC quote. This is the selected assembly finish for flat pads under the small audio chips, not a claim that ENIG is electrically mandatory. Do not substitute HASL or OSP without a separate process decision. Use four-layer, nominal 1.6 mm FR-4 TG135, 1 oz outer / 0.5 oz inner copper; supplier must confirm the actual stackup. Red and black solder-mask quotes are alternative batches; the final color remains an order choice.

Retain exactly the 16 designated epoxy-filled, planarized, copper-capped thermal holes. Ordinary routing vias are not subject to this treatment. The selected N16R8 module requires JLC Standard assembly and X-ray inspection. The intended batch is five PCBs, with two assembled and three bare. Confirm CAM, stencil/paste, component orientation and the exact hole map before production. This update is not fabrication release or powered qualification.

Both MAX98357A TQFN chips are mounted directly on the carrier. No audio breakout boards, sockets or standoff pins are used. R1–R11 now use 1206 surface-mount footprints: R1–R4 are 100 ohm, R5–R11 are 10 kohm. BOM selections are Yageo RC1206FR-07100RL and RC1206FR-0710KL, respectively, 1%, 0.25 W at 70 C. Do not fit the old axial parts.

The outline is 100 × 80 mm. The four existing mounting holes are retained; use the current 1:1 template. Speaker terminals face their respective side edges and USB-C faces the bottom edge. Leave enclosure openings and outside wire/cable-bend space; dimensions alone do not establish assembled fit. Keep metal and wiring away from the module antenna keepout.

U1 pin 1 and U4 pin 5 now use off-pad vias. Exactly **16 thermal holes** still require **epoxy filling, planarization and copper capping**: four MAX98357A exposed-pad vias and twelve module exposed-pad holes. Use the coordinate list and map in `out/dfm-review/`. Do not fill component lead or mounting holes. Obtain supplier confirmation of the exact process, paste/stencil design, footprint compatibility, orientation, sourcing and assembly yield before release.

Use the same MEAN WELL GST25A05-P1J 5 V / 4 A barrel supply. All lighting, audio and logic share its 20 W output budget. The specified fuse is supplementary protection, not a tested precision load limiter. USB is for service/logic; barrel power is required for lighting/audio. Validate USB/barrel coexistence, startup and load transients on hardware.

These files are engineering prototypes. Confirm boot, flash and PSRAM detection, microSD read/write, both audio channels, lighting, sensors, regulator and amplifier temperatures, Wi-Fi operation, and actual enclosure fit on assembled hardware. No powered or physical fit test has been performed.

## Stereo revision — 2026-09-29

Fit R29 = **100 kohm, RC0603FR-07100KL** for U6 LEFT and R30 = **374 kohm, RC0603FR-07374KL** for U7 RIGHT. Both are 0603, 1%, 0.1 W, 100 ppm/C. The old 1 Mohm selectors produce dual mono and must not be fitted. DIN, BCLK and LRCLK remain shared; no extra audio GPIO is needed. Use this variant's stereo firmware overlay; the common legacy mono configuration is insufficient for the specified stereo pipeline. J7 is LEFT and J8 is RIGHT.

See [stereo circuit and firmware notes](../shared/STEREO-AUDIO.md) and [channel test](../stereo-bench/README.md). Confirm actual left/right separation, mono-file duplication and SD playback on hardware. Neither output terminal of a speaker may be connected to ground or another amplifier output.


## Smart-fix revision

Use firmware version 5.71 in this variant's overlay. Both variants include MP3, WAV and Opus decoders and a resampler between the player and the I2S speaker. The physical stream is fixed at 44.1 kHz / 16-bit; normal 44.1 kHz audio passes through, while supported decoded files at other rates are converted. This preserves stereo content; it cannot invent a second channel from a mono recording. Validate 22.05/24 kHz files, 48 kHz Opus, and normal 44.1 kHz playback under the full show load, watching CPU, heap and underruns. Compilation is not playback evidence.

The exact 22 uF BOM selection is Samsung CL21A226MAYNNNE, the manufacturer's recommended replacement for NRND CL21A226MAQNNNE. L1 is now explicitly Sunlord SWPA6045S4R7MT rather than an unspecified 4.7 uH. Nominal capacitance and catalog current ratings do not replace bias/transient/thermal checks. Other inherited purchasing lines and all stock/assembly quotes remain unresolved.

Printed v3.3a uses its existing Feather configuration and remains dual mono. Profiles are chosen at build time, not through a runtime stereo toggle. Do not flash either V3.4 binary onto it. See [closure report](../audits/06-smart-fixes/REPORT.md).
