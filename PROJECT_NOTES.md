# Halloween Castle — Tech Upgrade

Living design/research doc. Updated as we go.

**Goal:** Take a store-bought decorative castle and add controllable RGB lighting
and spooky audio, driven by an ESP32. Short term: prototype with hardware on hand.
Long term: addressable RGB + real speakers, network-controlled.

**Chosen stack:** ESPHome (config-driven, native Home Assistant).
**Chosen board:** Adafruit **ESP32-S2** Feather — 240 MHz, 4 MB flash, 2 MB PSRAM.
Currently attached at `/dev/cu.usbmodem1101`.

---

## Where everything lives

The record outgrew one file (the repo holds every file to 500 lines, prose
included — `tools/check_loc.py`), so it is split by era under `docs/notes/`.
Nothing was summarised in the move; each part is the original text, verbatim.
Section numbers (`§2`, `§12.9`, …) are global across the parts — a reference to
`PROJECT_NOTES §12.9` anywhere in the repo means the section of that number,
whichever file it now sits in. Newest work goes at the end of the part it
belongs to; start a new part when one nears the cap.

| Part | Sections | What is in it |
|---|---|---|
| [01 — Research and architecture](docs/notes/01-research-and-architecture.md) | §1–§9 | Hardware inventory; why the RAK18060 was shelved; the DFPlayer architecture and ESPHome-vs-WLED; repurposing the RAK4630; shopping list; staged plan; effect brainstorm; open questions; buying a DFPlayer that actually works (the chip lottery, mitigations, sound sources) |
| [02 — Mockup and bill of materials](docs/notes/02-mockup-and-bom.md) | §10–§11 | The Mac-side mockup and the Castle Cue Desk as built, with its fidelity caveats; board facts that constrain everything; the parts list; power plan; wiring map; speakers; where to buy what; the DFPlayer's honest assessment; gotchas |
| [03 — Build: firmware, light, previewer, tracks](docs/notes/03-build.md) | §12–§12.8 | What was pinned down during the build; the flash wall; what compiling verified and what still needs the board; dry-run work; making audio actually drive the light; the previewer transport rebuild; custom tracks |
| [04 — Build: microSD, pins, audio capacity, benchmark, logs](docs/notes/04-build-sd-pins-audio-bench.md) | §12.9–§12.14 | microSD audio — what's true and what it costs; the eInk FeatherWing taking three pins; audio capacity numbers; the on-board MP3 decode benchmark and its results; getting logs off the board; build trees moved off the internal disk (§12.12, filed after §12.14) |
| [05 — Decision log and roadmap](docs/notes/05-decisions-and-roadmap.md) | §13–§14 | Every decision with date and rationale; the agreed roadmap and standing work |

**Reading the quality gate:** [docs/ISSUE-sonar.md](docs/ISSUE-sonar.md) —
SonarCloud reports two letters and nothing a command line can reach; that file
is how to get the findings anyway, and what each rating is actually made of.

**Open issue:** [the door ring flickers](docs/ISSUE-ring-flicker.md) — one
corrupted frame now and then on the 12 px ring, narrowed to its own signal
path (everything upstream of the pad is ruled out, with evidence). Read it
before re-deriving the theories; it also lists the next tests in order.

**Open issue:** [a scene's audio starts rough](docs/ISSUE-scene-start-audio.md)
— the song and the tones are clean, a scene start is not; A/B/C/D by ear and
the firmware evidence are in the file, with the next tests in order.

### Quick section finder

| § | Topic | File |
|---|---|---|
| 1 | Hardware inventory | 01 |
| 2 | ESP32-S2 + RAK18060 — researched, shelved | 01 |
| 3 | Recommended architecture — DFPlayer Mini; ESPHome vs WLED | 01 |
| 4 | Repurposing the RAK4630 | 01 |
| 5–8 | Shopping list; staged plan; effect brainstorm; open questions | 01 |
| 9 | Buying a DFPlayer that actually works | 01 |
| 10 | Mac-side mockup → Castle Cue Desk | 02 |
| 11 | Bill of materials, power, wiring map, speakers, DFPlayer assessment | 02 |
| 12–12.4 | Build — implemented; flash wall; verified / unverified | 03 |
| 12.5–12.8 | Dry-run work; audio-driven light; previewer transport; custom tracks | 03 |
| 12.9 | microSD audio — blockers, the one path, risks, cheaper answers | 04 |
| 12.10 | eInk FeatherWing takes three pins | 04 |
| 12.11 | Audio capacity — the 4-minute question | 04 |
| 12.13 | Decode benchmark and its results | 04 |
| 12.14 | Getting logs off this board | 04 |
| 12.12 | Build trees moved off the internal disk | 04 |
| 13 | Decision log | 05 |
| 14 | Roadmap | 05 |

Related records that were never part of this file: the ESP32-S2 hardware
findings in [`HARDWARE_FINDINGS.md`](HARDWARE_FINDINGS.md), the wiring guide in
[`docs/WIRING.md`](docs/WIRING.md) (diagram: `docs/castle-wiring.html`), the
roadmap detail in [`docs/ROADMAP.md`](docs/ROADMAP.md) and the TypeScript
migration in [`web/MIGRATION.md`](web/MIGRATION.md).

**The Rust half:** `core/` (castle-core) is why a render is the same bytes on
every machine, and it is the newest thing this record has to explain. The
three decisions behind it — why a crate at all, why zero dependencies, why the
studio was twinned instead of rewritten — are §13's last rows in
[05 — Decision log](docs/notes/05-decisions-and-roadmap.md); the contract
holding every remaining duplicate copy bit-exact is
[`docs/PARITY.md`](docs/PARITY.md), and the migration it came out of is
`.claude/typesafe-migration-plan.md`.
