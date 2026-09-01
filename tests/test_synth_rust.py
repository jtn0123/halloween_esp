"""castle-core's RNG against numpy's default_rng, draw for draw — B3's gate.

render_audio.py seeds np.random.default_rng(zlib.crc32(scene_id)) and every
synth draws from it, so a Rust render can only match the Python one if the
random stream is identical. core/src/rng.rs carries numpy's SeedSequence +
PCG64 XSL-RR; this test holds it to numpy itself — raw u64s exactly, and
uniforms digit-for-digit with no tolerances.

uniform has a twist: numpy's C is `low + range * next_double`, and whether
the wheel's compiler fused that into one rounding depends on the platform
(arm64 clang fuses, baseline x86-64 does not). numpy_uniform_mode() probes
the numpy actually installed and tells the Rust dump which form to use, so
the comparison stays exact on every host instead of tolerant on some.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import unittest
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from synth_probes import kernel_modes, numpy_uniform_mode

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
DUMP = ROOT / "core" / "target" / "release" / "synth_dump"

RANGES = [
    (-1.0, 1.0),
    (-0.03, 0.03),
    (70.0, 160.0),
    (0.25, 0.9),
    (6.0, 11.0),
    (0.15, 1.4),
    (1200.0, 2600.0),
    (0.0, 1.0),
]


def fmt(v: float) -> str:
    return repr(float(v))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestSynthRngParity(unittest.TestCase):
    def test_seeded_streams_match_numpy_digit_for_digit(self) -> None:
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
        mode = numpy_uniform_mode()

        r = random.Random(int(os.environ.get("CASTLE_SYNTH_SEED", "7")))
        seeds: list[int] = [
            0,
            1,
            12345,
            2**63,
            2**96 + 17,
            *(zlib.crc32(s.encode()) for s in ("vigil", "storm", "heartbeat_hall")),
            *(r.randrange(2**128) for _ in range(6)),
        ]
        lines: list[str] = []
        want: list[str] = []
        for seed in seeds:
            lines.append(f"raw {seed} 200")
            want.append(
                " ".join(str(int(x)) for x in np.random.PCG64(seed).random_raw(200))
            )
            for lo, hi in RANGES:
                lines.append(f"uni {seed} {fmt(lo)} {fmt(hi)} 50 {mode}")
                g = np.random.Generator(np.random.PCG64(seed))
                want.append(" ".join(fmt(x) for x in g.uniform(lo, hi, 50)))

        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(want))
        checked = 0
        for i, (a, b) in enumerate(zip(want, got)):
            va = [float(x) for x in a.split()]
            vb = [float(x) for x in b.split()]
            self.assertEqual(va, vb, f"line {i} ({lines[i].split()[0]}) diverged")
            checked += len(va)
        # 13 seeds x (200 raws + 8 ranges x 50 uniforms)
        self.assertEqual(checked, len(seeds) * (200 + len(RANGES) * 50))

    def test_note_voices_match_bit_for_bit(self) -> None:
        """pipe/piano/box, whole buffers: the Rust digest (crc32 of the f64
        bytes + 16 strided probes) must equal one computed from synth.py's
        numpy output. A probe mismatch names the sample; a crc mismatch
        with equal probes means a bit flipped somewhere between them."""
        assert CARGO is not None
        subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        import synth

        r = random.Random(int(os.environ.get("CASTLE_SYNTH_SEED", "7")))
        cases: list[tuple[str, float, float, float, float]] = [
            # the notes the pieces actually play
            ("pipe", synth.nt(-19), 7.5, 0.078, synth.STOPS),
            ("pipe", synth.nt(-31), 25.5, 0.060, synth.STOPS),
            ("piano", synth.nt(-24), 1.35, 0.22, 0.0),
            ("piano", synth.nt(-5), 0.75, 0.07, 0.0),
            ("box", synth.nt(24), 1.9, 0.17, 0.0),
            ("box", synth.nt(12), 1.7, 0.15, 0.0),
        ]
        cases.extend(
            (
                r.choice(["pipe", "piano", "box"]),
                synth.nt(r.randrange(-31, 25)),
                round(r.uniform(0.3, 3.0), 3),
                round(r.uniform(0.02, 0.5), 3),
                round(r.uniform(0.1, 0.9), 3),
            )
            for _ in range(12)
        )
        modes = kernel_modes()
        lines = [
            f"note {v} {fmt(f)} {fmt(dur)} {fmt(vel)} {fmt(stops)} {modes}"
            for v, f, dur, vel, stops in cases
        ]
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
        for case, reply in zip(cases, got):
            voice, f, dur, vel, stops = case
            if voice == "pipe":
                ref = synth.pipe(f, dur, vel, stops)
            elif voice == "piano":
                ref = synth.piano(f, dur, vel)
            else:
                ref = synth.box(f, dur, vel)
            buf = np.asarray(ref, dtype="<f8")
            crc, n, *probes = reply.split()
            self.assertEqual(int(n), len(buf), case)
            stride = max(1, len(buf) // 16)
            want_probes = [float(buf[i]) for i in range(0, len(buf), stride)]
            self.assertEqual(
                [float(p) for p in probes], want_probes, f"probe diverged: {case}"
            )
            self.assertEqual(
                int(crc, 16), zlib.crc32(buf.tobytes()), f"crc diverged: {case}"
            )

    def test_pieces_match_bit_for_bit_markers_included(self) -> None:
        """The compositions: whole mixed buffers digest-equal, and the light
        markers (what the cue generators consume) float-equal."""
        assert CARGO is not None
        subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        import synth

        cases: list[tuple[str, float | None]] = [
            ("organ", None),
            ("descent", None),
            ("waltz", None),
            ("musicbox", None),
            ("toll", None),
            ("drone", 20.0),
            ("drone", 7.3),
        ]
        modes = kernel_modes()
        lines = [
            f"piece {name} {fmt(20.0 if dur is None else dur)} {modes}"
            for name, dur in cases
        ]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(cases))
        for (name, dur), reply in zip(cases, got):
            if name == "drone":
                assert dur is not None
                res: object = synth.drone(dur)
            else:
                res = getattr(synth, name)()
            sig, marks = res if isinstance(res, tuple) else (res, [])
            buf = np.asarray(sig, dtype="<f8")
            head, _, mtext = reply.partition(" | ")
            crc, n, *probes = head.split()
            self.assertEqual(int(n), len(buf), (name, dur))
            stride = max(1, len(buf) // 16)
            want_probes = [float(buf[i]) for i in range(0, len(buf), stride)]
            self.assertEqual(
                [float(p) for p in probes], want_probes, f"probe diverged: {name}"
            )
            self.assertEqual(
                int(crc, 16), zlib.crc32(buf.tobytes()), f"crc diverged: {name}"
            )
            got_marks = [
                (float(a), float(b))
                for a, b in (m.split(":") for m in mtext.split(",") if m)
            ]
            self.assertEqual(got_marks, [(float(a), float(b)) for a, b in marks], name)

    def test_butter_and_sosfilt_match_scipy_bit_for_bit(self) -> None:
        """The filter design chain and the filter run itself. numpy/scipy
        wheels carry compiler-placed fusions inside their complex kernels
        that differ per platform, so kernel_modes() probes each one on the
        installed wheels and the Rust side reproduces the observed form —
        exact everywhere, tolerant nowhere."""
        assert CARGO is not None
        subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        from scipy import signal

        modes = kernel_modes()
        umode = numpy_uniform_mode()
        r = random.Random(int(os.environ.get("CASTLE_SYNTH_SEED", "7")))
        lp_cases = [
            (n, hz) for n in (1, 2) for hz in (70.0, 150.0, 420.0, 900.0, 21950.0)
        ]
        lp_cases += [
            (r.choice([1, 2]), round(r.uniform(35.0, 21950.0), 3)) for _ in range(20)
        ]
        bp_cases = [(320.0, 620.0), (652.0, 868.0), (2755.0, 3045.0)]
        for _ in range(30):
            hz = r.uniform(35.0, 5000.0)
            bw = max(hz / r.uniform(0.7, 10.0), 20.0)
            bp_cases.append(
                (round(max(20.0, hz - bw / 2), 3), round(min(21950.0, hz + bw / 2), 3))
            )
        lines = [f"blp {n} {fmt(hz)} {modes}" for n, hz in lp_cases]
        lines += [f"bbp {fmt(lo)} {fmt(hi)} {modes}" for lo, hi in bp_cases]
        filt_cases = [
            ("sflp", 2.0, 150.0),
            ("sflp", 1.0, 630.0),
            ("sfbp", 652.0, 868.0),
        ]
        lines += [
            f"{op} {fmt(a)} {fmt(b)} 12345 8000 {umode} {modes}"
            for op, a, b in filt_cases
        ]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(lines))
        at = 0
        for n, hz in lp_cases:
            want = [
                float(v)
                for v in signal.butter(n, hz, "lowpass", fs=44100, output="sos").ravel()
            ]
            want += [0.0] * (6 - len(want))
            self.assertEqual([float(v) for v in got[at].split()], want, (n, hz))
            at += 1
        for lo, hi in bp_cases:
            want = [
                float(v)
                for v in signal.butter(
                    2, [lo, hi], "bandpass", fs=44100, output="sos"
                ).ravel()
            ]
            self.assertEqual([float(v) for v in got[at].split()], want, (lo, hi))
            at += 1
        for op, a, b in filt_cases:
            g = np.random.Generator(np.random.PCG64(12345))
            x = g.uniform(-1.0, 1.0, 8000)
            wn: float | list[float] = a if op == "sflp" else [a, b]
            sos = signal.butter(
                int(a) if op == "sflp" else 2,
                b if op == "sflp" else wn,
                "lowpass" if op == "sflp" else "bandpass",
                fs=44100,
                output="sos",
            )
            buf = np.asarray(signal.sosfilt(sos, x), dtype="<f8")
            crc, cnt, *probes = got[at].split()
            self.assertEqual(int(cnt), len(buf), (op, a, b))
            stride = max(1, len(buf) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(buf[i]) for i in range(0, len(buf), stride)],
                f"filtered probe diverged: {op} {a} {b}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(buf.tobytes()), (op, a, b))
            at += 1

    def test_atmosphere_voices_match_bit_for_bit(self) -> None:
        """wind, heartbeat, creak, shriek, whispers, thunder: numpy dice
        through scipy filters, whole buffers and markers exact under the
        probed kernel modes."""
        assert CARGO is not None
        subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        import synth

        modes = kernel_modes()
        umode = numpy_uniform_mode()
        cases: list[tuple[str, float, int]] = [
            ("wind", 8.0, 11),
            ("wind", 30.0, 21),
            ("heartbeat", 6.0, 12),
            ("heartbeat", 20.0, 22),
            ("whispers", 6.0, 13),
            ("whispers", 20.0, 23),
            ("thunder", 0.0, 14),
            ("creak", 0.0, 15),
            ("shriek", 0.0, 16),
        ]
        lines = [
            f"voice {name} {fmt(dur)} {seed} {umode} {modes}"
            for name, dur, seed in cases
        ]
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
        for (name, dur, seed), reply in zip(cases, got):
            rng = np.random.default_rng(seed)
            fn = synth.SYNTHS[name]
            res = (
                fn(rng, dur=dur)
                if name in ("wind", "heartbeat", "whispers")
                else fn(rng)
            )
            sig, marks = res if isinstance(res, tuple) else (res, [])
            buf = np.asarray(sig, dtype="<f8")
            head, _, mtext = reply.partition(" | ")
            crc, cnt, *probes = head.split()
            self.assertEqual(int(cnt), len(buf), (name, dur))
            stride = max(1, len(buf) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(buf[i]) for i in range(0, len(buf), stride)],
                f"probe diverged: {name} {dur}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(buf.tobytes()), (name, dur))
            got_marks = [
                (float(a), float(b))
                for a, b in (v.split(":") for v in mtext.split(",") if v)
            ]
            self.assertEqual(got_marks, [(float(a), float(b)) for a, b in marks], name)


if __name__ == "__main__":
    unittest.main()
