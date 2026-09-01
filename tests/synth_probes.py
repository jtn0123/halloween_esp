"""Which compiled form did this wheel give each numpy/scipy kernel?

Split from test_synth_rust.py at the 500-line cap along its real seam:
these functions are not tests but instruments. numpy's C is compiled per
platform, and whether `low + range*d`, the complex kernels, or sosfilt's
inner loop carry fused multiply-adds depends on the wheel (arm64 clang
fuses; baseline x86-64 does not). Each probe compares the installed
library against every known form and returns the matching mode for the
Rust dump — raising loudly on an unknown platform rather than letting a
parity test go tolerant.

The manylinux wheels answered differently on every one of these (2026-08-31),
and brought two forms the macOS wheel had never shown: gcc fuses a complex
multiply on the SECOND product where clang fuses the first, and Linux's
np.sqrt is glibc's csqrt rather than the FreeBSD one numpy carries as a
fallback. So the multiply probes have three answers, csqrt has four, and
np.interp — hard-coded fused in the Rust until Linux said otherwise — is
probed here too.
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


def numpy_dispatch_names() -> str:
    """The optimisation targets THIS wheel was built to dispatch — the
    only names NPY_DISABLE_CPU_FEATURES accepts, and the reason the
    remedy below is computed rather than written down.

    numpy renames them between releases: the 2.5 x86-64 wheel dispatches
    by microarchitecture LEVEL ("X86_V3 X86_V4 AVX512_ICL AVX512_SPR"),
    so the per-feature names an older numpy took ("AVX512F AVX512_SKX"…)
    are now rejected with nothing but an ImportWarning — the vector
    kernels stay, and CI stays red while looking like it was told.
    """
    try:
        import importlib

        mu = importlib.import_module("numpy._core._multiarray_umath")
        names = [str(n) for n in mu.__cpu_dispatch__]
    except Exception:  # a wheel that does not expose it — say so plainly
        return "<numpy._core._multiarray_umath.__cpu_dispatch__>"
    return " ".join(names)


def assert_libm_transcendentals() -> None:
    """The one class of divergence no mode character can describe.

    sin/exp/log/pow are libm calls on both sides — CPython's math module
    and Rust's f64 methods both go straight to the C library, and every
    wheel seen so far leaves numpy's element loops doing the same. A host
    where numpy dispatches a VECTOR math library instead (an AVX-512
    machine is the known case) computes them to a different last ulp, and
    no amount of probing can reproduce that in Rust. The same dispatch
    moves pocketfft, so np.fft — the reverb's transform — drifts with it.

    So say it in words rather than letting it surface three suites later
    as an unexplained last digit. The remedy is numpy's own switch:
    NPY_DISABLE_CPU_FEATURES, naming this wheel's dispatch targets, in
    the environment BEFORE numpy is imported puts the baseline element
    loops back — the arithmetic the canonical render is defined in.
    """
    t = np.arange(1, 4001) / 4000.0
    checks = (
        ("sin", np.sin(t * 40.0), lambda v: math.sin(v * 40.0)),
        ("cos", np.cos(t * 40.0), lambda v: math.cos(v * 40.0)),
        ("exp", np.exp(-t * 3.0), lambda v: math.exp(-v * 3.0)),
        ("log", np.log(t), math.log),
        ("pow", np.power(2.0, t), lambda v: 2.0**v),
    )
    for name, got, want in checks:
        for i, v in enumerate(t):
            if float(got[i]) != want(float(v)):
                raise AssertionError(
                    f"np.{name} does not agree with libm on this host "
                    f"(first at {float(v)!r}: {float(got[i])!r} vs "
                    f"{want(float(v))!r}). numpy is dispatching a vector "
                    "math kernel — the parity suites cannot reproduce it. "
                    "Set NPY_DISABLE_CPU_FEATURES="
                    f'"{numpy_dispatch_names()}" before numpy is imported '
                    "to get the baseline loops back. Those names are this "
                    "wheel's own dispatch targets; a name outside the list "
                    "is a warning at import and disables nothing."
                )


def kernel_modes() -> str:
    """Probe the installed numpy/scipy for each complex kernel's compiled
    form (fused vs naive) and return the six mode characters the Rust
    dump needs: ufunc-mult, poly-mult, division, csqrt, sosfilt, interp.
    Raises if any kernel matches no known form — better a loud new
    platform than a silent tolerance."""
    import math

    assert_libm_transcendentals()

    from scipy import signal

    rng = np.random.default_rng(9)
    fma = math.fma
    quads = rng.uniform(-50, 50, (300, 4))

    def pick(name: str, forms: dict[str, object]) -> str:
        matches = [k for k, ok in forms.items() if ok]
        if not matches:
            raise AssertionError(f"{name}: no known form matches this wheel")
        return matches[0]

    def cmul(ar: float, ai: float, br: float, bi: float, form: str) -> complex:
        """numpy's complex product, in each form a compiler leaves it:
        naive, fused on the first product (clang), fused on the second
        (gcc's fnmsub)."""
        if form == "1":
            return complex(fma(ar, br, -(ai * bi)), fma(ar, bi, ai * br))
        if form == "2":
            return complex(fma(-ai, bi, ar * br), fma(ai, br, ar * bi))
        return complex(ar * br - ai * bi, ar * bi + ai * br)

    mul = pick(
        "mult",
        {
            f: all(
                complex(np.complex128(complex(ar, ai)) * np.complex128(complex(br, bi)))
                == cmul(ar, ai, br, bi, f)
                for ar, ai, br, bi in quads
            )
            for f in ("1", "0", "2")
        },
    )

    # np.poly convolves [1, -r] pairs, so its last coefficient is the
    # product of the negated roots — through the convolve loop's OWN
    # multiply, which need not be the ufunc's.
    pr = rng.uniform(-2, 2, (300, 2))
    poly = pick(
        "poly",
        {
            f: all(
                float(np.poly(np.asarray([complex(x, y), complex(x, -y)]))[2])
                == cmul(-x, -y, -x, y, f).real
                for x, y in pr
            )
            for f in ("0", "1", "2")
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
        if form == 3:
            # glibc's __csqrt: the same identity, halved in a different
            # order, on the C library's hypot (np.hypot IS that hypot;
            # math.hypot is CPython's own and does not agree).
            d = float(np.hypot(np.float64(x), np.float64(y)))
            if x > 0.0:
                r = math.sqrt(0.5 * (d + x))
                return complex(r, 0.5 * (y / r))
            t = math.sqrt(0.5 * (d - x))
            return complex(abs((0.5 * y) / t), math.copysign(t, y))
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
            for form in (2, 1, 0, 3)
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

    # np.interp's inner step, the one kernel here that is not complex:
    # slope*(x-xp)+fp, fused on the macOS wheel and plain on manylinux.
    ix = [0.0, 1.5, 5.0, 7.5]
    iy = [0.0, 1.0, 1.0, 0.0]
    at = [float(v) for v in rng.uniform(0.0, 7.5, 400)]
    iwant = [float(v) for v in np.interp(np.asarray(at), ix, iy)]

    def run_interp(fused: bool) -> list[float]:
        out = []
        for x in at:
            for j in range(len(ix) - 1):
                if x < ix[j + 1]:
                    sl = (iy[j + 1] - iy[j]) / (ix[j + 1] - ix[j])
                    d = x - ix[j]
                    out.append(fma(sl, d, iy[j]) if fused else sl * d + iy[j])
                    break
            else:
                out.append(iy[-1])
        return out

    ip = pick(
        "interp", {"1": run_interp(True) == iwant, "0": run_interp(False) == iwant}
    )
    return mul + poly + div + sq + sf + ip
