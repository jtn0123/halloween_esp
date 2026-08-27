"""The Python half of the cross-language parity fuzz, held to its contract.

fuzz_check.py does not compare — web/test/fuzz_parity.ts does — but two of
its properties decide whether that comparison can be trusted, and neither
was tested: stdout must stay parseable JSON even when the generators print
(the chatter is diverted to stderr), and norm() must never smooth away a
real difference between the two generators' answers. A checker that hides
divergence is worse than none; it reads as green.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fuzz_check as fc

SCENE_YAML = """
- id: fz
  name: Fuzz check
  kind: custom
  duration_ms: 4000
  volume: 0.7
  base: {towerL: candle, towerR: candle, door: candle}
  pulse:
    - {synth: toll, intensity: 0.5, decay: 0.9, color: [1.0, 0.2, 0.1, 0.0]}
  cues: []
"""

CASE = {
    "dur_ms": 4000,
    "scene_yaml": SCENE_YAML,
    "hits_by_synth": {"toll": [[500, 0.8], [1500, 0.6]]},
}


class TestRunCase(unittest.TestCase):
    def test_both_generators_agree_on_a_simple_case(self) -> None:
        out = fc.run_case(dict(CASE))
        self.assertTrue(out["esphome"], "no strikes came back at all")
        self.assertEqual(out["esphome"], out["previewer"])

    def test_norm_does_not_hide_a_divergence(self) -> None:
        """Tamper with one generator and the answers must differ — if norm()
        ever smooths that away, the fuzz upstream is decorative."""
        real = fc.gp.to_previewer

        def crooked(scene, idx, raw, markers):
            out = real(scene, idx, raw, markers)
            for c in out["cues"]:
                if c.get("op") == "strike" and "intensity" in c:
                    c["intensity"] = (c.get("intensity") or 0) + 0.5
            return out

        with mock.patch.object(fc.gp, "to_previewer", side_effect=crooked):
            out = fc.run_case(dict(CASE))
        self.assertNotEqual(out["esphome"], out["previewer"])


class TestMainContract(unittest.TestCase):
    def test_stdout_is_json_even_when_generators_chatter(self) -> None:
        stdin = io.StringIO(json.dumps({"cases": [CASE]}))
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(fc.sys, "stdin", stdin),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            fc.main()
        body = json.loads(stdout.getvalue())  # the whole contract
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["esphome"], body["results"][0]["previewer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
