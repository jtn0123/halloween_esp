"""What the golden harness ASKS — the corpus, and nothing that runs.

Split from golden_case.py along the seam that was already there: this file
is the QUESTIONS (which routes, which malformed scene blocks), that one is
the machinery (sandbox, HTTP, normalisation, the golden files themselves).
The corpus is the half that grows — every new refusal the desk can show
adds a case here — and it was already within two lines of the 500-line cap
sitting in one file with the driver.

Both `tools/gen_golden.py` (which records the Python studio's answers) and
`tests/test_studio_golden.py` (which replays them against the Rust one)
read the corpus from here, so there is exactly one definition of what the
goldens cover.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The show the sandbox serves: the repo's own preamble (hardware, zones,
#: palette — the parts scene_schema validates against) plus two tiny scenes.
SCENES_TAIL = """\
  - id: vigil
    name: Vigil
    kind: ambient
    volume: 0.45
    duration_ms: 2000
    loop: true
    base: {towerL: candle, towerR: candle, door: ember}
    score:
      - {t: 0, synth: toll, gain: 0.5}
    cues: []

  - id: storm
    name: Storm
    kind: triggered
    volume: 1.0
    duration_ms: 1500
    base: {towerL: candle, towerR: candle, door: ember}
    score:
      - {t: 0.0, synth: wind, dur: 1.5, gain: 0.6}
    cues:
      - {t: 80, op: strike, ms: 70, pixels: scatter, note: "lightning"}
"""

TINY = """\
  - id: {sid}
    name: Trial
    kind: ambient
    volume: 0.4
    duration_ms: {ms}
    base: {{towerL: candle, towerR: candle, door: ember}}
    score:
      - {{t: 0, synth: toll, gain: 0.4}}
    cues: []"""

JSON_HDRS = {"Content-Type": "application/json"}


def scenes_fixture() -> str:
    real = (ROOT / "scenes" / "scenes.yaml").read_text()
    preamble = real.split("\nscenes:\n", 1)[0]
    return preamble + "\nscenes:\n" + SCENES_TAIL


def tiny(sid: str, ms: int = 1200) -> str:
    return TINY.format(sid=sid, ms=ms)


# --------------------------------------------------------------------------
# The corpus. Adding a case here and regenerating is the whole workflow.
# --------------------------------------------------------------------------

#: Read-side routes: (name, method, path). Every one of these answers from
#: the studio's own state with no castle in reach (CASTLE_HOST=""), so the
#: bytes are the same on every machine.
READ_CASES: tuple[tuple[str, str, str], ...] = (
    ("status_castleless", "GET", "/api/status"),
    ("health_castleless", "GET", "/api/health"),
    ("status_post_castleless", "POST", "/api/status"),
    ("unknown_castle_route", "GET", "/api/nonsense"),
    ("unknown_studio_route", "GET", "/studio/nope"),
    ("unknown_root_route", "GET", "/nope"),
    ("track_no_id", "GET", "/studio/track/"),
    ("track_unknown", "GET", "/studio/track/nope"),
    ("scene_audio_unknown", "GET", "/studio/scene-audio/nope"),
    ("scene_audio_traversal", "GET", "/studio/scene-audio/%2e%2e"),
    ("waveform_unknown", "GET", "/studio/waveform/nope"),
    ("stems_unknown", "GET", "/studio/stems/nope"),
    ("job_unknown", "GET", "/studio/job/nope"),
    # Wrong verb on a real path: the desk's own typos must not read as an
    # outage either, and 404-vs-405 is a contract the two servers share.
    ("tracks_wrong_method", "DELETE", "/studio/tracks"),
    ("tracks_listing", "GET", "/studio/tracks"),
    ("tracks_listing_alias", "GET", "/api/tracks"),
)

#: The bodies the desk shows next to the field when a splice is refused.
#: Today every one of these strings comes from ONE Python implementation
#: (studio_scenes.check → scene_schema.validate, reached by the Rust studio
#: through tools/scene_check.py). These goldens are what a native Rust
#: validator would have to reproduce, sentence for sentence.
SCENE_CASES: tuple[tuple[str, bytes], ...] = (
    ("body_not_json", b"{nope"),
    ("no_id_no_yaml", json.dumps({}).encode()),
    ("id_without_yaml", json.dumps({"id": "trial", "yaml": ""}).encode()),
    ("yaml_without_id", json.dumps({"id": "", "yaml": tiny("trial")}).encode()),
    ("yaml_unparseable", json.dumps({"id": "x", "yaml": "nonsense: ["}).encode()),
    ("yaml_is_a_mapping", json.dumps({"id": "x", "yaml": "id: x\nname: X"}).encode()),
    (
        "yaml_holds_two_scenes",
        json.dumps({"id": "a", "yaml": tiny("a") + "\n" + tiny("b")}).encode(),
    ),
    ("id_mismatch", json.dumps({"id": "x", "yaml": tiny("y")}).encode()),
    (
        "head_fields_missing",
        json.dumps({"id": "bad", "yaml": "  - id: bad\n    kind: ambient"}).encode(),
    ),
    (
        "id_not_an_identifier",
        json.dumps({"id": "no-dashes", "yaml": tiny("no-dashes")}).encode(),
    ),
    (
        "duration_not_positive",
        json.dumps({"id": "trial", "yaml": tiny("trial", ms=0)}).encode(),
    ),
    (
        "volume_out_of_range",
        json.dumps(
            {"id": "trial", "yaml": tiny("trial").replace("volume: 0.4", "volume: 9")}
        ).encode(),
    ),
    (
        "loop_not_a_boolean",
        json.dumps(
            {"id": "trial", "yaml": tiny("trial") + "\n    loop: maybe"}
        ).encode(),
    ),
    (
        "audio_file_absolute",
        json.dumps(
            {"id": "trial", "yaml": tiny("trial") + "\n    audio_file: /etc/passwd"}
        ).encode(),
    ),
    (
        "base_unknown_effect",
        json.dumps(
            {
                "id": "trial",
                "yaml": tiny("trial").replace("towerL: candle", "towerL: disco"),
            }
        ).encode(),
    ),
    (
        "base_unknown_zone",
        json.dumps(
            {
                "id": "trial",
                "yaml": tiny("trial").replace("towerL: candle", "moat: candle"),
            }
        ).encode(),
    ),
    (
        "cues_not_a_list",
        json.dumps(
            {"id": "trial", "yaml": tiny("trial").replace("cues: []", "cues: soon")}
        ).encode(),
    ),
    (
        "cue_op_unknown",
        json.dumps(
            {
                "id": "trial",
                "yaml": tiny("trial").replace(
                    "cues: []", "cues:\n      - {t: 10, op: wobble}"
                ),
            }
        ).encode(),
    ),
    (
        "cue_past_the_end",
        json.dumps(
            {
                "id": "trial",
                "yaml": tiny("trial").replace(
                    "cues: []",
                    "cues:\n      - {t: 99999, op: strike, ms: 70, pixels: scatter}",
                ),
            }
        ).encode(),
    ),
    (
        "cue_set_without_a_zone",
        json.dumps(
            {
                "id": "trial",
                "yaml": tiny("trial").replace(
                    "cues: []", "cues:\n      - {t: 10, op: set, effect: candle}"
                ),
            }
        ).encode(),
    ),
    (
        "pulse_without_a_synth",
        json.dumps(
            {
                "id": "trial",
                "yaml": tiny("trial") + "\n    pulse:\n      - {zones: [towerL]}",
            }
        ).encode(),
    ),
    (
        "many_problems_at_once",
        json.dumps(
            {
                "id": "trial",
                "yaml": (
                    "  - id: trial\n"
                    "    kind: seance\n"
                    "    volume: 4\n"
                    "    duration_ms: -3\n"
                    "    base: {moat: disco}\n"
                    "    cues: [{t: 5, op: wobble}]"
                ),
            }
        ).encode(),
    ),
)

#: The thirteenth scene. Recorded separately because it needs the sandbox's
#: scenes.yaml rewritten to a show already at the board's ceiling, and the
#: read cases above must be captured before that happens (the listing names
#: the show's scenes). The sentence is the operator's whole explanation of a
#: hardware limit — freezing it is the point (grade report 2026-08-31 A8).
CEILING_CASE = (
    "scene_ceiling",
    json.dumps({"id": "one_too_many", "yaml": tiny("one_too_many")}).encode(),
)
