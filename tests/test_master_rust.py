"""castle-core's master chain against render_audio's, bit for bit — B3.

limit, the loop crossfade, the end fade, normalisation and the int16
quantise: the last deterministic steps between a mixed scene and the WAV
lame encodes. synth.limit was moved off np.convolve in the same change
this gate arrived (BLAS dot order is a vendor choice; cumsum differences
are defined-order), so the Python here is itself newly portable and the
Rust is held to it exactly — no filters involved, so no kernel modes,
just the uniform-form probe.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
import zlib
from pathlib import Path
from typing import cast

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cargo_gate
from synth_probes import numpy_uniform_mode

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
DUMP = ROOT / "core" / "target" / "release" / "synth_dump"
SR = 44100


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestMasterChainParity(unittest.TestCase):
    def test_every_master_step_matches_bit_for_bit(self) -> None:
        built = cargo_gate.build()
        self.assertEqual(built.returncode, 0, built.stderr)
        import synth

        umode = numpy_uniform_mode()
        cases = [
            ("limit", 41, 120_000),
            ("limit", 42, 9_000),
            ("loop", 43, 200_000),
            ("loop", 44, 50_000),
            ("fade", 45, 200_000),
            ("norm", 46, 30_000),
            ("wav", 47, 30_000),
        ]
        lines = [f"master {kind} {seed} {n} {umode}" for kind, seed, n in cases]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(cases))
        for (kind, seed, n), reply in zip(cases, got):
            amp = 1.6 if kind == "limit" else 0.8
            g = np.random.default_rng(seed)
            buf = g.uniform(-amp, amp, n)
            if kind == "limit":
                buf = synth.limit(buf)
            elif kind == "loop":
                xf = min(int(0.6 * SR), len(buf) // 4)
                head = buf[:xf].copy()
                ramp = np.linspace(0.0, 1.0, xf)
                buf[-xf:] = buf[-xf:] * (1.0 - ramp) + head * ramp
            elif kind == "fade":
                fade = min(int(0.25 * SR), len(buf))
                buf[-fade:] *= np.linspace(1.0, 0.0, fade)
            elif kind == "norm":
                peak = float(np.max(np.abs(buf)))
                buf *= 0.89 / peak
            if kind == "wav":
                pcm = (np.clip(buf, -1.0, 1.0) * 32767.0).astype("<i2")
                crc, cnt = reply.split()
                self.assertEqual(int(cnt), len(pcm))
                self.assertEqual(int(crc, 16), zlib.crc32(pcm.tobytes()), (kind, seed))
                continue
            out = np.asarray(buf, dtype="<f8")
            crc, cnt, *probes = reply.split()
            self.assertEqual(int(cnt), len(out), (kind, seed))
            stride = max(1, len(out) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(out[i]) for i in range(0, len(out), stride)],
                f"probe diverged: {kind} {seed}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(out.tobytes()), (kind, seed))

    def test_reverb_matches_bit_for_bit(self) -> None:
        """The stone hall: defined-order FFT convolution both sides, so
        the wet tail is exact — no tolerances, 2^18-point transforms."""
        cargo_gate.build(check=True)
        import synth_master

        umode = numpy_uniform_mode()
        cases = [(61, 60_000, 0.42), (62, 60_000, 0.15), (63, 20_000, 0.0)]
        lines = [f"reverb {seed} {n} {wet} {umode}" for seed, n, wet in cases]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=240,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(cases))
        for (seed, n, wet), reply in zip(cases, got):
            x = np.random.default_rng(seed).uniform(-0.5, 0.5, n)
            want = synth_master.apply_reverb(x, wet, np.random.default_rng(seed + 1))
            buf = np.asarray(want, dtype="<f8")
            crc, cnt, *probes = reply.split()
            self.assertEqual(int(cnt), len(buf), (seed, wet))
            stride = max(1, len(buf) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(buf[i]) for i in range(0, len(buf), stride)],
                f"reverb probe diverged: seed {seed} wet {wet}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(buf.tobytes()), (seed, wet))

    def test_whole_scenes_render_bit_for_bit(self) -> None:
        """The synth-score path of render_scene end to end: crc32-seeded
        dice, twelve-voice dispatch, takes, gains, tails, limiter and
        normalise — buffer AND marker dict exact, the stone hall
        included (wet scenes convolve through the defined-order FFT,
        whose dice follow the score's on the same stream)."""
        cargo_gate.build(check=True)
        import render_audio
        from synth_probes import kernel_modes

        modes = kernel_modes()
        umode = numpy_uniform_mode()
        scenes: list[dict[str, object]] = [
            {
                "id": "vigil",
                "duration_ms": 9000,
                "reverb": 0,
                "score": [
                    {"synth": "toll", "t": 0.5},
                    {"synth": "heartbeat", "t": 0.0, "dur": 8.0, "gain": 0.9},
                    {"synth": "creak", "t": 4.0, "gain": 0.5},
                ],
            },
            {
                "id": "storm",
                "duration_ms": 12000,
                "reverb": 0,
                "loop": True,
                "score": [
                    {"synth": "wind", "t": 0.0, "dur": 12.0},
                    {"synth": "thunder", "t": 2.0, "gain": 1.2},
                    {"synth": "whispers", "t": 1.0, "dur": 10.0, "gain": 0.7},
                ],
            },
            {
                "id": "ballroom",
                "duration_ms": 10000,
                "reverb": 0,
                "score": [
                    {"synth": "waltz", "t": 0.2, "take": 6.0},
                    {"synth": "musicbox", "t": 7.0, "gain": 0.8},
                    {"synth": "drone", "t": 0.0, "dur": 10.0, "gain": 0.6},
                ],
            },
            {
                "id": "procession",
                "duration_ms": 8000,
                "score": [  # no reverb key: the synth default 0.42 applies
                    {"synth": "organ", "t": 0.0, "take": 7.5, "gain": 0.9},
                    {"synth": "shriek", "t": 5.0, "gain": 0.4},
                ],
            },
            {
                "id": "seance",
                "duration_ms": 9000,
                "reverb": 0.25,
                "loop": True,
                "score": [
                    {"synth": "drone", "t": 0.0, "dur": 9.0},
                    {"synth": "toll", "t": 1.0, "gain": 0.7},
                ],
            },
        ]

        def ev_arg(ev: dict[str, object]) -> str:
            dur = ev.get("dur", "-")
            take = ev.get("take", "-")
            return f"{ev['synth']}:{ev['t']}:{ev.get('gain', 1.0)}:{dur}:{take}"

        lines = []
        for sc in scenes:
            score = cast("list[dict[str, object]]", sc["score"])
            lines.append(
                f"scene {sc['id']} {sc['duration_ms']} {sc.get('reverb', 0.42)} "
                f"{1 if sc.get('loop') else 0} {umode} {modes} "
                + ";".join(ev_arg(e) for e in score)
            )
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=240,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(scenes))
        for sc, reply in zip(scenes, got):
            want_buf, want_marks = render_audio.render_scene_py(sc, {"sample_rate": SR})
            buf = np.asarray(want_buf, dtype="<f8")
            head, _, rest2 = reply.partition(" | ")
            mtext, _, pcm_crc = rest2.partition(" | ")
            pcm = (np.clip(buf, -1.0, 1.0) * 32767.0).astype("<i2")
            self.assertEqual(int(pcm_crc, 16), zlib.crc32(pcm.tobytes()), sc["id"])
            crc, cnt, *probes = head.split()
            self.assertEqual(int(cnt), len(buf), sc["id"])
            stride = max(1, len(buf) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(buf[i]) for i in range(0, len(buf), stride)],
                f"scene probe diverged: {sc['id']}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(buf.tobytes()), sc["id"])
            got_marks: dict[str, list[list[float]]] = {}
            for chunk in mtext.split(";"):
                if not chunk:
                    continue
                name, _, pts = chunk.partition(">")
                got_marks[name] = [
                    [int(a), float(b)]
                    for a, b in (p.split(":") for p in pts.split(",") if p)
                ]
            self.assertEqual(got_marks, want_marks, sc["id"])

    def test_the_real_show_renders_byte_identical(self) -> None:
        """The closing gate of B3's synth path: five scenes straight out of
        scenes/scenes.yaml — storm, approach, visitation, ballroom, crypt,
        between them every voice class, takes, loops and the default
        reverb — rendered by castle-core and held to render_audio's f64
        buffer, marker dict AND int16 PCM, byte for byte. (The other three
        synth scenes render the same voices for longer; runtime is why
        they sit out.)"""
        cargo_gate.build(check=True)
        import render_audio
        import yaml
        from synth_probes import kernel_modes

        modes = kernel_modes()
        umode = numpy_uniform_mode()
        doc = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
        wanted = {"storm", "approach", "visitation", "ballroom", "crypt"}
        scenes = [sc for sc in doc["scenes"] if sc["id"] in wanted]
        self.assertEqual(len(scenes), 5, "scenes.yaml no longer has these ids")

        def ev_arg(ev: dict[str, object]) -> str:
            return (
                f"{ev['synth']}:{ev['t']}:{ev.get('gain', 1.0)}:"
                f"{ev.get('dur', '-')}:{ev.get('take', '-')}"
            )

        lines = [
            f"scene {sc['id']} {sc['duration_ms']} {sc.get('reverb', 0.42)} "
            f"{1 if sc.get('loop') else 0} {umode} {modes} "
            + ";".join(ev_arg(e) for e in sc["score"])
            for sc in scenes
        ]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(scenes))
        for sc, reply in zip(scenes, got):
            want_buf, want_marks = render_audio.render_scene_py(sc, {"sample_rate": SR})
            buf = np.asarray(want_buf, dtype="<f8")
            head, _, rest2 = reply.partition(" | ")
            mtext, _, pcm_crc = rest2.partition(" | ")
            crc, cnt, *probes = head.split()
            self.assertEqual(int(cnt), len(buf), sc["id"])
            stride = max(1, len(buf) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(buf[i]) for i in range(0, len(buf), stride)],
                f"show scene diverged: {sc['id']}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(buf.tobytes()), sc["id"])
            pcm = (np.clip(buf, -1.0, 1.0) * 32767.0).astype("<i2")
            self.assertEqual(int(pcm_crc, 16), zlib.crc32(pcm.tobytes()), sc["id"])
            got_marks: dict[str, list[list[float]]] = {}
            for chunk in mtext.split(";"):
                if not chunk:
                    continue
                name, _, pts = chunk.partition(">")
                got_marks[name] = [
                    [int(a), float(b)]
                    for a, b in (p.split(":") for p in pts.split(",") if p)
                ]
            self.assertEqual(got_marks, want_marks, sc["id"])


if __name__ == "__main__":
    unittest.main()
