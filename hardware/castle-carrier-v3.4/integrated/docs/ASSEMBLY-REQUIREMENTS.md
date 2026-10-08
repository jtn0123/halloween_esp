# v3.4 Integrated assembly and fabrication requirements — 2026-10-01

This document specifies the intended process. It is not evidence that a fabricator has accepted the job or that a board has been assembled.

- Four-layer, 1.6 mm FR-4; use the stackup in the KiCad board (JLC04161H-7628 basis), 35 um outer copper. Retain the module antenna copper/component keepout on every layer. Do not substitute a different stackup without reviewing USB routing.
- Keep ENIG with 1 microinch gold, FR-4 TG135, 1 oz outer / 0.5 oz inner copper. ENIG is the selected prototype process, not an electrical necessity. HASL/OSP substitution requires a separate review. See [current assembly notes](../ASSEMBLY-NOTES.md).
- U2 must be ESP32-S3-WROOM-1-N16R8: 16 MB flash / 8 MB octal PSRAM. SD MOSI/SCK/MISO use GPIO39/40/41; GPIO35/36/37 are reserved for octal PSRAM. Use the Integrated firmware overlay.
- U6/U7 must be MAX98357AETE+T, 16-pin 3 x 3 mm TQFN. Do not substitute MAX98357B or the WLP package. Both outputs are bridged; neither speaker terminal is ground.
- Specify **nonconductive epoxy-filled, planarized and copper-capped treatment for exactly 16 plated 0.30 mm thermal holes: twelve at U2 pad 41 and two at each U6/U7 exposed pad**. Use `../out/dfm-review/filled-capped-holes.csv` and its map. Ordinary tenting is not an equivalent substitution. U1.1 and U4.5 now use off-pad vias and do not require this process. Do not fill ordinary routing vias, component lead holes, connector holes or mounting holes. A fabrication CAM operator must identify U2's holes from the CAD and coordinates rather than assume every pad drill is a component hole.
- U6/U7 ground pad: 1.5 x 1.5 mm copper; four 0.6 x 0.6 mm paste windows, 64% nominal paste-area coverage. Use a 0.10 mm laser-cut stencil as the starting specification. Filled/capped thermal holes make these solderable surfaces; do not use this stencil over open thermal holes. Assembler must verify paste/process suitability and inspect hidden joints, including X-ray for bridging/voids and continuity/function checks. Do not accept a board solely from an optical top view.
- U2 uses the existing segmented thermal-pad paste pattern. Confirm module stencil thickness/reflow profile against Espressif recommendations and the assembler's process. Moisture handling and reflow follow each component's datasheet.
- All SMT parts are on the front. R1–R11 are 1206 surface-mount parts, not axial resistors. Fit through-hole connectors, fuse holder and electrolytics in a separate suitable process. Polarity/orientation verification includes U2/U6/U7/U8 pin 1, Q1, D1/D3/D5/D6/D7, and C3/C4/C5. Fit R29 = 100 kohm for LEFT and R30 = 374 kohm for RIGHT; follow the current stereo assembly notes.
- Purchase new/changed parts by the exact manufacturer part numbers in `bom-v3.4-engineering.csv`; no substitutions based on nominal value alone. Unchanged inherited supplier selections still need stock and package confirmation at quotation time.
- Quote five PCBs, two assembled and three bare, including filled/capped vias, Standard assembly, required X-ray and through-hole work. The September 30 quote already includes ENIG; shipping/taxes and final supplier process acceptance remain open. See the [supplier review request](SUPPLIER-REVIEW.md) and [stock snapshot](../qa/jlc-stock-20261007.json). Previous prices are historical.

## Mechanical fit

J7/J8 selection is Phoenix Contact 1984617 (PT 1,5/2-3,5-H): 3.5 mm pitch, 0.9 mm pins, specified 1.2 mm PCB holes; body 7 x 7.55 mm and 9.15 mm installed height. This matches the named stock footprint family. Keep room for wire insertion and a screwdriver; support the terminal during tightening as the manufacturer instructs. Use the specified part rather than an arbitrary 3.5-mm lookalike.

Board dimensions remain 100 x 80 mm. The original v3 was 100 x 75 mm; no claim of drop-in enclosure compatibility is made. Use `out/mechanical-fit.svg` at 100% scale to compare mounting holes, outline and connectors with the actual enclosure. Allow the SD card's insertion/ejection travel and USB/barrel cable boots. The user specified an 80–82 mm board-width cap and no practical cap on the other dimension; the 80 mm side meets that target. A physical template fit is still required because wall, mounting and connector clearances were not supplied. Simplified USB/button render models are illustrations, not fit evidence.

## First-board acceptance

1. Inspect soldering/polarities and measure unpowered supply resistance. Use a current-limited regulated 5 V source for initial barrel power.
2. Verify 3.3 V startup/ripple, USB-only startup, both-source operation and absent-source backfeed. Measure cold USB insertion current and VBUS dip; U8 controls ramp rate but does not certify USB current negotiation or compliance.
3. Test USB both plug orientations, BOOT/RESET and J10 UART recovery with 3.3 V logic; J10 pin 1 is 5 V.
4. Verify 16 MB flash, 8 MB octal PSRAM, SD access, dual-OTA update and recovery using the Integrated overlay.
5. Run the intended maximum lights, both speakers, SD streaming and Wi-Fi together; record resets/dropouts, supply voltage and component/connector temperatures in the enclosure. Scope I2S and audio power transients.
6. Check speaker wiring, audio quality, EMI and RF range with final cables/enclosure. The PCB contains no proof of any of these measured results.

Sources: [TI TPS22919](https://www.ti.com/lit/ds/symlink/tps22919.pdf), [JLC via processing](https://jlcpcb.com/help/article/pcb-via-covering), [Phoenix 1984617](https://www.phoenixcontact.com/en-ca/products/printed-circuit-board-terminal-pt-15-2-35-h-1984617), [Espressif module](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf), [ADI amplifier](https://www.analog.com/media/en/technical-documentation/data-sheets/max98357a-max98357b.pdf).
