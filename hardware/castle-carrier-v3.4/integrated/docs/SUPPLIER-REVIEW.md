# Integrated v3.4 supplier review request

Prepared for review; not sent, accepted or released.

Please quote five finished 100 x 80 mm PCBs, two assembled and three bare. Quote red and black separately. Keep ENIG with 1 microinch gold; four layers, 1.6 mm FR-4 TG135, 1 oz outer / 0.5 oz inner copper, with the reviewed JLC04161H-7628 stackup basis. Confirm actual stackup and USB impedance review before changing materials.

Apply epoxy fill, planarization and copper capping to exactly the 16 designated 0.30 mm thermal holes: twelve at U2 pad 41 and two at each U6/U7 exposed pad. Confirm the CSV/map against CAM, including the module's pad holes. Leave ordinary routing, component lead and mounting holes out of this treatment.

Use Standard assembly and required X-ray. All SMT parts are on the front; include through-hole assembly and identify any manual-install items. The fuse holder is F1; the separate SCHURTER 0001.2510 T4A cartridge needs installation, not a second placement. Confirm any process rails are removed after assembly; temporary 5 mm rails must not increase the delivered 80 mm capped dimension.

U2 is ESP32-S3-WROOM-1-N16R8, C2913202. U6/U7 are MAX98357AETE+T, C910544, TQFN-16-EP. Verify pin-1 orientation and hidden-joint inspection. Fit R29 = 100k LEFT and R30 = 374k RIGHT. Verify U8, Q1, all diodes and electrolytic polarity against native CAD. The placement CSV is a reference; approve its rotation convention and a populated top/bottom preview before machine use.

For U6/U7, CAD paste has four 0.6 x 0.6 mm windows over each 1.5 x 1.5 mm ground pad, 64% area coverage. Start review with a 0.10 mm laser-cut stencil and the filled/capped surface. Confirm the module's segmented paste pattern, stencil, reflow profile and moisture handling with component documentation and your process.

C20/C21/C24/C25 now select Samsung CL10C221JB8NNNC, C27675: 220 pF, 50 V, C0G, +/-5%, 0603, 1.6 x 0.8 x 0.8 mm. This replaces the out-of-stock Murata part after an engineering comparison. No other substitution is authorized by this document. Confirm final BOM stock, minimum/spare quantities and all supplied parts before quoting.

Return CAM acceptance, finalized BOM/CPL orientation approval, stencil approval, both color totals, shipping/tax, and any exception. The public stock snapshot is not a reservation. Earlier September 30 quotes are historical and do not establish today's price.

## Prepared upload references

`../out/jlc-bom-review.csv` and `../out/jlc-cpl-native-review.csv` cover 92 assembly references, excluding the separately installed fuse cartridge. The latter follows JLC's documented KiCad position export convention: native footprint origins/rotations with renamed headers. Coordinates use KiCad absolute X and inverted Y; match this frame to Gerber/CAM. These are reference inputs for matching/placement review, not an approved machine program.

The earlier quote CPL used the midpoint of numbered pad centers. For 22 parts, including U2, Q1, SD/USB connectors and through-hole parts, this differs from the native footprint origin. `../out/jlc-placement-offset-review.csv` records those differences. Neither convention alone proves a library body centroid: confirm actual part-center offsets, pin 1 and rotation against a populated overlay before approving placement. No component was moved in native CAD. See `../qa/jlc-input-review.json`.

Source: [JLC KiCad export guide](https://jlcpcb.com/help/article/how-to-generate-the-bom-and-centroid-file-from-kicad).
