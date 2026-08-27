"""castle-core's pulse dynamics against pulse_dynamics.py, digit for digit.

B2 of the typesafe plan: core/src/pulse.rs re-implements the per-stream
dynamics that exist in tools/pulse_dynamics.py and web/src/track_lights.ts.
This gate throws a seeded corpus at the Rust `pulse_dump` line protocol and
demands every answer equal the Python's exactly — no tolerances; the whole
module is f64 arithmetic both sides evaluate in the same order.

thin_pulses is compared by IDENTITY (which original indices survive), so
the ranking and both tie-breaks are pinned, not just the surviving values.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import contextlib
import io
import os
import random
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import pulse_dynamics as pd
import pulse_expand as pe

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
SEED = int(os.environ.get("CASTLE_PULSE_SEED", "11"))
NOTES = ("hush", "verse", "chorus", "silence")
SYNTHS = ("onset_low", "onset_mid", "onset_high", "level_low")


def fmt(v: float) -> str:
    return repr(float(v))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestPulseRustParity(unittest.TestCase):
    def test_seeded_corpus_matches_digit_for_digit(self) -> None:
        assert CARGO is not None
        built = subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        self.assertEqual(built.returncode, 0, built.stderr)

        r = random.Random(SEED)
        lines: list[str] = []
        want: list[str] = []

        def gates_arg(gates: list[tuple[int, str]]) -> str:
            return ",".join(f"{t}:{n}" for t, n in gates) or "-"

        for _ in range(400):
            n = r.choice([0, 3, 7, 8, 9, 40, 300])
            times = sorted(round(r.uniform(0, 240), 4) for _ in range(n))
            lines.append("tf " + ";".join(fmt(t) for t in times))
            want.append(fmt(pd.tempo_factor(times)))

            d, f = round(r.uniform(0.7, 0.999), 4), round(r.uniform(0.7, 1.6), 4)
            lines.append(f"td {d} {f}")
            want.append(fmt(pd.tempo_decay(d, f)))

            x = r.uniform(-2, 4)
            lines.append(f"r3 {fmt(x)}")
            want.append(fmt(pd.round3(x)))

            vn = r.randint(1, 24)
            vels = [round(r.random(), 4) for _ in range(vn)]
            i = r.randrange(vn)
            lines.append("acc " + ";".join(fmt(v) for v in vels) + f" {i}")
            want.append(str(pd.is_accent(vels, i)).lower())

            gn = r.randint(0, 6)
            gates = sorted(
                (r.randrange(0, 200_000), r.choice(NOTES)) for _ in range(gn)
            )
            t_ms = r.randrange(0, 220_000)
            synth = r.choice(SYNTHS)
            lines.append(f"gm {synth} {t_ms} {gates_arg(gates)}")
            mul = pd.gate_mul(synth, gates, t_ms)
            want.append("None" if mul is None else fmt(mul))
            lines.append(f"gn {t_ms} {gates_arg(gates)}")
            want.append(pd.gate_note(gates, t_ms) or "None")

            cn = r.choice([1, 2, 3, 5])
            colors = [[round(r.random(), 3) for _ in range(4)] for _ in range(cn)]
            ci, ct = r.randrange(6), r.randrange(0, 400_000)
            lines.append(
                "db "
                + "|".join(";".join(fmt(c) for c in col) for col in colors)
                + f" {ci} {ct}"
            )
            want.append(",".join(fmt(c) for c in pd.drift_base(colors, ci, ct)))

            pn = r.choice([1, 5, 30])
            cap = r.choice([1, 3, 20, 200])
            # Coarse values on purpose: ties are where the tie-breaks live.
            cues = [
                {
                    "intensity": r.choice([0.2, 0.5, 0.5, 0.9]),
                    "t": r.choice([0, 100, 100, 5000, 9000]),
                    "i": j,
                }
                for j in range(pn)
            ]
            lines.append(
                f"thin {cap} " + "|".join(f"{c['intensity']}:{c['t']}" for c in cues)
            )
            kept = pd.thin_pulses(list(cues), cap)
            want.append(",".join(str(c["i"]) for c in kept))

        run = subprocess.run(
            [str(ROOT / "core" / "target" / "release" / "pulse_dump")],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(want))
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:  # digit-for-digit, with float-parse as the arbiter
                try:
                    self.assertEqual(float(a), float(b), f"line {i}: {lines[i]!r}")
                except ValueError:
                    self.fail(
                        f"seed {SEED} line {i}: {lines[i]!r} — py {a!r} vs rust {b!r}"
                    )

    def test_pulse_cues_expansion_matches(self) -> None:
        """pulse_expand.pulse_cues vs core's pulse_cues on random streams.

        Random key PRESENCE as much as random values: a key the corpus
        leaves out is a default the two sides must agree on."""
        assert CARGO is not None
        r = random.Random(SEED + 1)
        zones_pool = ["towerL", "towerR", "door"]
        lines: list[str] = []
        want: list[str] = []

        def color(r: random.Random) -> list[float]:
            return [round(r.random(), 3) for _ in range(r.choice([3, 4, 4]))]

        for _ in range(300):
            cfg: dict[str, object] = {"synth": r.choice(SYNTHS)}
            parts = [f"synth={cfg['synth']}"]
            if r.random() < 0.7:
                zs = r.sample(zones_pool, r.randint(1, 3))
                cfg["zones"] = zs
                parts.append("zones=" + "+".join(zs))
            if r.random() < 0.6:
                cfg["alternate"] = True
                parts.append("alternate=1")
            if r.random() < 0.4:
                bt = r.sample(zones_pool, r.randint(1, 2))
                cfg["boost_targets"] = bt
                parts.append("boost_targets=" + "+".join(bt))
                if r.random() < 0.6:
                    cfg["boost_at"] = round(r.uniform(0.4, 0.95), 2)
                    parts.append(f"boost_at={cfg['boost_at']}")
            if r.random() < 0.5:
                col = color(r)
                cfg["color"] = col
                parts.append("color=" + ",".join(map(repr, col)))
            if r.random() < 0.5:
                hot = color(r)
                cfg["color_hot"] = hot
                parts.append("hot=" + ",".join(map(repr, hot)))
            if r.random() < 0.4:
                cyc = [color(r) for _ in range(r.randint(1, 3))]
                cfg["colors"] = cyc
                parts.append("colors=" + "|".join(",".join(map(repr, c)) for c in cyc))
            for flag, key in (
                ("takeover", "takeover"),
                ("drift", "drift"),
                ("pbv", "pixels_by_vel"),
            ):
                if r.random() < 0.3:
                    cfg[key] = True
                    parts.append(f"{flag}=1")
            if r.random() < 0.3:
                cfg["pixels"] = r.choice(["center", "scatter", "ring"])
                parts.append(f"pixels={cfg['pixels']}")
            for key, lo, hi in (("intensity", 0.1, 1.2), ("decay", 0.7, 0.99)):
                if r.random() < 0.5:
                    cfg[key] = round(r.uniform(lo, hi), 3)
                    parts.append(f"{key}={cfg[key]!r}")
            if r.random() < 0.5:
                cfg["ms"] = r.randrange(40, 600)
                parts.append(f"ms={cfg['ms']}")
            if r.random() < 0.3:
                cfg["attack_ms"] = r.randrange(0, 90)
                parts.append(f"attack={cfg['attack_ms']}")

            bn = r.choice([0, 2, 9, 40])
            ts = sorted(r.sample(range(0, 200_000), bn))
            beats = [
                [t, round(r.random(), 3)]
                + ([round(r.uniform(-0.7, 0.7), 2)] if r.random() < 0.4 else [])
                for t in ts
            ]
            gn = r.randint(0, 5)
            gates = sorted(
                (r.randrange(0, 200_000), r.choice(NOTES)) for _ in range(gn)
            )
            scene = {
                "id": "s",
                "pulse": [cfg],
                "cues": [{"op": "set", "note": n, "t": t} for t, n in gates],
            }
            markers = {"s": {cfg["synth"]: beats}}
            with contextlib.redirect_stdout(io.StringIO()):
                cues = pe.pulse_cues(scene, markers)
            want.append(
                "\x1e".join(
                    "\x1f".join(
                        [
                            str(c["t"]),
                            "-" if c["targets"] is None else "+".join(c["targets"]),
                            str(c["ms"]),
                            repr(float(c["intensity"])),
                            ",".join(repr(float(x)) for x in c["color"]),
                            repr(float(c["decay"])),
                            str(c["attack"]),
                            str(c["pixels"]),
                            str(c["note"]),
                        ]
                    )
                    for c in cues
                )
            )
            beat_arg = ",".join(":".join(str(x) for x in b) for b in beats) or "-"
            g_arg = ",".join(f"{t}:{n}" for t, n in gates) or "-"
            lines.append(f"pc {g_arg} {';'.join(parts)} {beat_arg}")

        run = subprocess.run(
            [str(ROOT / "core" / "target" / "release" / "pulse_dump")],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.split("\n")[:-1]
        self.assertEqual(len(got), len(want))
        for i, (a, b) in enumerate(zip(want, got)):
            if a == b:
                continue
            # Same shape, floats judged as numbers (repr spellings differ
            # between the languages for e.g. 1e-05).
            aa, bb = a.split("\x1e"), b.split("\x1e")
            self.assertEqual(len(aa), len(bb), f"seed {SEED + 1} line {i}: cue count")
            for ca, cb in zip(aa, bb):
                fa, fb = ca.split("\x1f"), cb.split("\x1f")
                self.assertEqual(len(fa), 9, f"line {i}")
                for j, (x, y) in enumerate(zip(fa, fb)):
                    if j in (3, 5):
                        self.assertEqual(
                            float(x), float(y), f"line {i} field {j}: {lines[i]!r}"
                        )
                    elif j == 4:
                        xs, ys = x.split(","), y.split(",")
                        self.assertEqual(len(xs), len(ys), f"line {i} color len")
                        for x2, y2 in zip(xs, ys):
                            self.assertEqual(
                                float(x2), float(y2), f"line {i} color: {lines[i]!r}"
                            )
                    else:
                        self.assertEqual(
                            x, y, f"seed {SEED + 1} line {i} field {j}: {lines[i]!r}"
                        )


if __name__ == "__main__":
    unittest.main()
