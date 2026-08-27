"""Which compiled form did this wheel give each numpy/scipy kernel?

Split from test_synth_rust.py at the 500-line cap along its real seam:
these functions are not tests but instruments. numpy's C is compiled per
platform, and whether `low + range*d`, the complex kernels, or sosfilt's
inner loop carry fused multiply-adds depends on the wheel (arm64 clang
fuses; baseline x86-64 does not). Each probe compares the installed
library against every known form and returns the matching mode for the
Rust dump — raising loudly on an unknown platform rather than letting a
parity test go tolerant.
"""

from __future__ import annotations

import math

import numpy as np


def numpy_uniform_mode() -> str:
    """ "fma" or "plain" — whichever this numpy wheel compiled uniform into."""
    g = np.random.Generator(np.random.PCG64(999))
    want = [float(x) for x in g.uniform(0.15, 1.4, 64)]
    raws = [int(x) for x in np.random.PCG64(999).random_raw(64)]
    ds = [(r >> 11) * (1.0 / 9007199254740992.0) for r in raws]
    if want == [math.fma(1.4 - 0.15, d, 0.15) for d in ds]:
        return "fma"
    if want == [0.15 + (1.4 - 0.15) * d for d in ds]:
        return "plain"
    raise AssertionError(
        "this numpy's uniform matches neither the fused nor the plain form"
    )


def kernel_modes() -> str:
    """Probe the installed numpy/scipy for each complex kernel's compiled
    form (fused vs naive) and return the five mode characters the Rust
    dump needs: ufunc-mult, poly-mult, division, csqrt, sosfilt. Raises
    if any kernel matches no known form — better a loud new platform than
    a silent tolerance."""
    import math

    from scipy import signal

    rng = np.random.default_rng(9)
    fma = math.fma
    quads = rng.uniform(-50, 50, (300, 4))

    def pick(name: str, forms: dict[str, object]) -> str:
        matches = [k for k, ok in forms.items() if ok]
        if not matches:
            raise AssertionError(f"{name}: no known form matches this wheel")
        return matches[0]

    mul = pick(
        "mult",
        {
            "1": all(
                complex(np.complex128(complex(ar, ai)) * np.complex128(complex(br, bi)))
                == complex(fma(ar, br, -(ai * bi)), fma(ar, bi, ai * br))
                for ar, ai, br, bi in quads
            ),
            "0": all(
                complex(np.complex128(complex(ar, ai)) * np.complex128(complex(br, bi)))
                == complex(ar * br - ai * bi, ar * bi + ai * br)
                for ar, ai, br, bi in quads
            ),
        },
    )

    def poly_a2(x: float, y: float, fused: bool) -> float:
        if fused:
            return fma(x, x, -(y * -y))
        return x * x - y * (-y)

    pr = rng.uniform(-2, 2, (300, 2))
    poly = pick(
        "poly",
        {
            "0": all(
                float(np.poly(np.asarray([complex(x, y), complex(x, -y)]))[2])
                == poly_a2(x, y, False)
                for x, y in pr
            ),
            "1": all(
                float(np.poly(np.asarray([complex(x, y), complex(x, -y)]))[2])
                == poly_a2(x, y, True)
                for x, y in pr
            ),
        },
    )

    def smith(ar: float, ai: float, br: float, bi: float, fused: bool) -> complex:
        if abs(br) >= abs(bi):
            rat = bi / br
            scl = 1.0 / (fma(bi, rat, br) if fused else br + bi * rat)
            if fused:
                return complex(fma(ai, rat, ar) * scl, fma(-ar, rat, ai) * scl)
            return complex((ar + ai * rat) * scl, (ai - ar * rat) * scl)
        rat = br / bi
        scl = 1.0 / (fma(br, rat, bi) if fused else bi + br * rat)
        if fused:
            return complex(fma(ar, rat, ai) * scl, fma(ai, rat, -ar) * scl)
        return complex((ar * rat + ai) * scl, (ai * rat - ar) * scl)

    div = pick(
        "div",
        {
            f: all(
                complex(np.complex128(complex(ar, ai)) / np.complex128(complex(br, bi)))
                == smith(ar, ai, br, bi, f == "1")
                for ar, ai, br, bi in quads
            )
            for f in ("1", "0")
        },
    )

    def csq(x: float, y: float, form: int) -> complex:
        if x == 0.0 and y == 0.0:
            return complex(0.0, y)
        h = [math.hypot(x, y), math.sqrt(x * x + y * y), math.sqrt(fma(x, x, y * y))][
            form
        ]
        t = math.sqrt((abs(x) + h) * 0.5)
        if x >= 0.0:
            return complex(t, y / (2.0 * t))
        return complex(abs(y) / (2.0 * t), math.copysign(t, y))

    sq = pick(
        "sqrt",
        {
            str(form): all(
                complex(np.sqrt(np.complex128(complex(x, y)))) == csq(x, y, form)
                for x, y in rng.uniform(-50, 50, (300, 2))
            )
            for form in (2, 1, 0)
        },
    )

    sos = signal.butter(2, 150.0, "lowpass", fs=44100, output="sos")
    xs = [float(v) for v in rng.uniform(-1, 1, 400)]
    want = [float(v) for v in signal.sosfilt(sos, np.asarray(xs))]
    b0, b1, b2, _, a1, a2 = [float(v) for v in sos.ravel()]

    def run_sos(fused: bool) -> list[float]:
        z0 = z1 = 0.0
        out = []
        for v in xs:
            y = fma(b0, v, z0) if fused else b0 * v + z0
            if fused:
                z0 = fma(b1, v, -(a1 * y)) + z1
                z1 = fma(b2, v, -(a2 * y))
            else:
                z0 = b1 * v - a1 * y + z1
                z1 = b2 * v - a2 * y
            out.append(y)
        return out

    sf = pick("sosfilt", {"1": run_sos(True) == want, "0": run_sos(False) == want})
    return mul + poly + div + sq + sf
