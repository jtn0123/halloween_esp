"""Offline synthesis for the Halloween castle.

Python port of the Web Audio graph in previewer/castle-cue-desk.html. The
previewer is for tuning; this is what actually produces the files that land in
flash. Keep the two in step — the parameters here are the authority.

All musical material is original, written in the haunted-parlour idiom
(A/D minor, raised 7th on the dominant). Nothing here is a transcription.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

SR = 44100

# Organ voicing. Matches the RANKS table in the previewer:
# 32' 16' 8' celeste, then the upper chorus which rides the `stops` control.
RANKS = [
    (0.25, 0.30, False), (0.5, 0.86, False), (1.0, 1.00, False), (1.004, 0.40, False),
    (2.0, 0.40, True), (3.0, 0.22, True), (4.0, 0.13, True), (6.0, 0.06, True),
]

STOPS = 0.42        # how much upper chorus is drawn
TREM_HZ = 5.1
TREM_DEPTH = 0.046  # multiplicative, not additive — see previewer notes


def nt(semitones: float) -> float:
    """Semitones relative to A3 (220 Hz)."""
    return 220.0 * (2.0 ** (semitones / 12.0))


def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def _noise(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.uniform(-1.0, 1.0, n)


def _lp(x: np.ndarray, hz: float, order: int = 2) -> np.ndarray:
    sos = signal.butter(order, min(hz, SR / 2 - 100), "lowpass", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def _bp(x: np.ndarray, hz: float, q: float = 2.0) -> np.ndarray:
    hz = float(np.clip(hz, 30, SR / 2 - 200))
    bw = max(hz / q, 20.0)
    lo, hi = max(20.0, hz - bw / 2), min(SR / 2 - 100, hz + bw / 2)
    sos = signal.butter(2, [lo, hi], "bandpass", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def _sweep_lp(x: np.ndarray, f0: float, f1: float, blocks: int = 48) -> np.ndarray:
    """Time-varying lowpass, done blockwise. Exact enough for thunder."""
    n = len(x)
    out = np.zeros(n)
    edges = np.linspace(0, n, blocks + 1).astype(int)
    freqs = np.geomspace(f0, f1, blocks)
    win = 64
    for i in range(blocks):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        seg = _lp(x[max(0, a - win):b], freqs[i])[-(b - a):]
        out[a:b] = seg
    # Soften block seams.
    return _lp(out, min(f0 * 1.5, SR / 2 - 200), order=1)


# ── voices ────────────────────────────────────────────────────────────────

def pipe(f: float, dur: float, vel: float, stops: float = STOPS) -> np.ndarray:
    """Additive drawbar organ rank with tremulant and a duration-scaled swell."""
    n = int(dur * SR)
    t = _t(n)
    out = np.zeros(n)
    for m, amp, upper in RANKS:
        fr = f * m * (1.0 if m == 1.0 else 1.0012)
        if fr < 24 or fr > 11000:          # below hearing / above useful
            continue
        a = amp * (stops if upper else 1.0)
        if a < 0.012:
            continue
        out += a * np.sin(2 * np.pi * fr * t)

    atk = min(1.7, max(0.07, dur * 0.20))
    rel = min(2.6, max(0.35, dur * 0.34))
    hold = max(atk + 0.05, dur - rel)
    env = np.interp(t, [0, atk, hold, dur], [0.0, 1.0, 1.0, 0.0])
    trem = 1.0 + TREM_DEPTH * np.sin(2 * np.pi * TREM_HZ * t)
    return out * env * trem * vel


def piano(f: float, dur: float, vel: float) -> np.ndarray:
    """Additive: higher partials decay faster, slight inharmonicity."""
    n = int(dur * SR)
    t = _t(n)
    out = np.zeros(n)
    for h in range(1, 9):
        fr = f * h * (1 + 0.0004 * h * h)
        if fr > 12000:
            break
        out += (1.0 / h ** 1.6) * np.sin(2 * np.pi * fr * t) * np.exp(-t * (2.2 + 0.9 * h))
    atk = max(1, int(0.006 * SR))
    out[:atk] *= np.linspace(0, 1, atk)
    return out * vel


def box(f: float, dur: float, vel: float) -> np.ndarray:
    """Music box / celesta: inharmonic partials, long ring."""
    n = int(dur * SR)
    t = _t(n)
    out = np.zeros(n)
    for i, m in enumerate((1.0, 3.01, 5.42)):
        if f * m > 14000:
            continue
        decay = 3.2 / (dur / (i * 0.8 + 1))
        out += (1.0 / (i * 2.2 + 1)) * np.sin(2 * np.pi * f * m * t) * np.exp(-t * decay)
    atk = max(1, int(0.004 * SR))
    out[:atk] *= np.linspace(0, 1, atk)
    return out * vel


# ── atmosphere ────────────────────────────────────────────────────────────

def wind(dur: float, rng: np.random.Generator) -> np.ndarray:
    n = int(dur * SR)
    t = _t(n)
    src = _noise(n, rng)
    # Bandpass centre wanders; blockwise again since the LFO is slow.
    out = np.zeros(n)
    blocks = max(1, int(dur * 4))
    edges = np.linspace(0, n, blocks + 1).astype(int)
    for i in range(blocks):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        centre = 420 + 240 * np.sin(2 * np.pi * 0.09 * (a / SR))
        out[a:b] = _bp(src[max(0, a - 512):b], centre, q=0.7)[-(b - a):]
    swell = 1.0 + 0.38 * np.sin(2 * np.pi * 0.06 * t)
    # Knees clamped into order: below ~4 s the two fades would overlap and
    # np.interp's xp goes non-monotonic, which silently returns nonsense —
    # the tail never fades and a looping bed clicks on every pass.
    up, down = min(2.5, dur / 2), max(dur - 1.5, dur / 2)
    fade = np.interp(t, [0, up, down, dur], [0.0, 1.0, 1.0, 0.0])
    return out * swell * fade * 0.10


def thunder(rng: np.random.Generator) -> np.ndarray:
    dur = 3.2
    n = int(dur * SR)
    t = _t(n)
    crack = _sweep_lp(_noise(n, rng), 900, 70)
    crack *= np.interp(t, [0, 0.05, 3.0], [0.0, 1.0, 0.0]) ** 1.6
    sub_f = np.geomspace(50, 27, n)
    sub = np.sin(2 * np.pi * np.cumsum(sub_f) / SR)
    sub *= np.interp(t, [0, 0.14, 2.9], [0.0, 1.0, 0.0]) ** 1.5
    return 0.85 * crack + 0.55 * sub


def creak(rng: np.random.Generator) -> np.ndarray:
    dur = 1.05
    n = int(dur * SR)
    t = _t(n)
    # Sawtooth whose pitch lurches — a hinge does not glide.
    steps = 26
    f = np.repeat(rng.uniform(70, 160, steps), int(np.ceil(n / steps)))[:n]
    saw = 2.0 * (np.cumsum(f) / SR % 1.0) - 1.0
    out = _bp(saw, 760, q=7)
    return out * np.interp(t, [0, 0.06, dur], [0.0, 1.0, 0.0]) ** 1.4 * 0.30


def shriek(rng: np.random.Generator) -> np.ndarray:
    dur = 0.9
    n = int(dur * SR)
    t = _t(n)
    src = _noise(n, rng)
    out = np.zeros(n)
    blocks = 40
    edges = np.linspace(0, n, blocks + 1).astype(int)
    centres = np.concatenate([np.geomspace(700, 2900, blocks // 2),
                              np.geomspace(2900, 900, blocks - blocks // 2)])
    for i in range(blocks):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        out[a:b] = _bp(src[max(0, a - 256):b], centres[i], q=9)[-(b - a):]
    return out * np.interp(t, [0, 0.04, dur], [0.0, 1.0, 0.0]) ** 1.3 * 0.34


def heartbeat(dur: float, rng: np.random.Generator) -> np.ndarray:
    """Lub-dub at ~48 bpm. The second thump is softer and higher, like the
    real thing; the pause after each pair is what makes it read as a heart
    and not a drum machine."""
    n = int(dur * SR)
    buf = np.zeros(n)

    def thump(f: float, amp: float) -> np.ndarray:
        tn = int(0.22 * SR)
        tt = _t(tn)
        body = np.sin(2 * np.pi * f * tt) * np.exp(-tt * 26.0)
        return _lp(body, 150) * amp

    period = 1.25
    t0 = 0.0
    beats = []                                     # actual thump times, for the lights
    while t0 < dur:
        jitter = rng.uniform(-0.03, 0.03)          # a metronome heart is dead
        _place(buf, thump(52, 1.00), t0 + jitter)
        _place(buf, thump(64, 0.55), t0 + 0.18 + jitter)
        # BOTH thumps, with their real loudness — the light does lub-dub too.
        #
        # Each is clamped to the buffer. The loop condition only checks the
        # lub, so when `dur` is not a comfortable multiple of the period the
        # dub lands past the end: _place drops the sound but the marker used
        # to survive, leaving a light flash with nothing underneath it.
        beats.append((max(0.0, t0 + jitter), 1.00))
        dub = t0 + 0.18 + jitter
        if dub < dur:
            beats.append((dub, 0.55))
        t0 += period
    return buf, beats


def drone(dur: float) -> np.ndarray:
    """D2 against A-flat: the tritone, sustained. Slow beating between
    detuned pairs keeps it alive without anything ever moving."""
    n = int(dur * SR)
    t = _t(n)
    out = np.zeros(n)
    for f, amp in ((73.42, 0.5), (73.60, 0.35),      # D2, detuned pair
                   (103.83, 0.30), (104.05, 0.22),   # Ab2, detuned pair
                   (36.71, 0.30)):                   # D1 under everything
        out += amp * np.sin(2 * np.pi * f * t)
    wander = 0.75 + 0.25 * np.sin(2 * np.pi * 0.045 * t + 1.0)
    # Clamp the knees so they stay ordered on short durations; otherwise
    # np.interp gets a non-monotonic xp and the tail never fades.
    up, down = min(3.0, dur / 2), max(dur - 3.0, dur / 2)
    fade = np.interp(t, [0, up, down, dur], [0.0, 1.0, 1.0, 0.0])
    return out * wander * fade * 0.16


def whispers(dur: float, rng: np.random.Generator) -> np.ndarray:
    """Sibilant bursts through wandering narrow formants — voices at the edge
    of intelligibility. Kept quiet on purpose: heard clearly, it's a noise
    effect; half-heard, the listener's brain writes the words."""
    n = int(dur * SR)
    buf = np.zeros(n)
    words = []                                      # word onsets, for the lights
    t0 = 0.6
    while t0 < dur - 1.0:
        wlen = rng.uniform(0.25, 0.9)               # one "word"
        wn = int(wlen * SR)
        seg = _noise(wn, rng)
        f1 = rng.uniform(1200, 2600)
        f2 = f1 * rng.uniform(1.3, 1.9)
        seg = _bp(seg, f1, q=8) + 0.6 * _bp(seg, f2, q=10)
        st = _t(wn)
        env = np.sin(np.pi * st / wlen) ** 2        # syllable swell
        env *= 1.0 + 0.5 * np.sin(2 * np.pi * rng.uniform(6, 11) * st)
        buf_i = int(t0 * SR)
        k = min(wn, n - buf_i)
        if k > 0:
            buf[buf_i:buf_i + k] += seg[:k] * env[:k]
        # Velocity from word length (no extra rng draws — the audio must not
        # change because we started reporting markers).
        words.append((t0, 0.5 + 0.5 * min(1.0, wlen / 0.9)))
        t0 += wlen + rng.uniform(0.15, 1.4)         # ragged pauses
    return buf * 0.5, words


def toll() -> np.ndarray:
    dur = 5.0
    n = int(dur * SR)
    t = _t(n)
    out = np.zeros(n)
    for i, m in enumerate((1.0, 2.76, 5.4, 8.9)):
        out += (0.24 / (i + 1)) * np.sin(2 * np.pi * 138 * m * t) * np.exp(-t * (0.9 + i * 0.55))
    return out, [(0.0, 1.0)]


# ── pieces ────────────────────────────────────────────────────────────────

def _place(buf: np.ndarray, sig: np.ndarray, t0: float) -> None:
    i = int(t0 * SR)
    if i < 0:                     # e.g. heartbeat jitter on the first beat
        sig = sig[-i:]
        i = 0
    if i >= len(buf) or len(sig) == 0:
        return
    k = min(len(sig), len(buf) - i)
    if k > 0:
        buf[i:i + k] += sig[:k]


def organ() -> np.ndarray:
    """Procession in D minor: i - bII - i - V7b9. 7.5 s chords, 0.9 s overlap."""
    chords = [
        (-19, (5, 8, 12)),      # i     D minor
        (-18, (6, 10, 13)),     # bII   E flat major, the Neapolitan
        (-19, (5, 8, 12)),      # i
        (-12, (12, 13, 16, 19)) # V7b9  A, Bb, C#, E
    ]
    buf = np.zeros(int(28.0 * SR))
    for i, (ped, notes) in enumerate(chords):
        bt = i * 6.6
        _place(buf, pipe(nt(ped), 7.5, 0.078), bt)
        _place(buf, pipe(nt(ped + 12), 7.5, 0.038), bt)
        for s in notes:
            _place(buf, pipe(nt(s - 12), 7.5, 0.030), bt)
    return buf, [(i * 6.6, 1.0) for i in range(len(chords))]


def descent() -> np.ndarray:
    """32' pedal held throughout; upper voices walk down chromatically."""
    buf = np.zeros(int(27.5 * SR))
    _place(buf, pipe(nt(-19), 25.5, 0.090), 0.0)
    _place(buf, pipe(nt(-31), 25.5, 0.060), 0.0)
    for i, cluster in enumerate(((17, 20, 24), (16, 19, 23), (15, 18, 22), (14, 17, 21))):
        bt = 1.2 + i * 5.8
        for s in cluster:
            _place(buf, pipe(nt(s - 12), 6.6, 0.030), bt)
    for s in (5, 8, 11, 14):           # diminished pile-up: D F Ab B
        _place(buf, pipe(nt(s - 12), 6.4, 0.034), 20.4)
    return buf


def waltz() -> np.ndarray:
    """8 bars, 3/4. Bass on the downbeat, triad on 2 and 3, music box on top."""
    B = 0.52
    prog = [(0, (0, 3, 7)), (0, (0, 3, 7)), (5, (0, 3, 7)), (7, (0, 4, 7)),
            (0, (0, 3, 7)), (8, (0, 4, 7)), (7, (0, 4, 7)), (0, (0, 3, 7))]
    mel = [12, 15, 19, 17, 15, 14, 17, 20, 17, 19, 14, 11,
           12, 15, 19, 20, 19, 17, 19, 14, 11, 12, None, None]
    buf = np.zeros(int((8 * 3 * B + 3.0) * SR))
    for bar, (root, iv) in enumerate(prog):
        bt = bar * 3 * B
        _place(buf, piano(nt(root - 24), 1.35, 0.22), bt)
        for k in (1, 2):
            for s in iv:
                _place(buf, piano(nt(root + s - 12), 0.75, 0.07), bt + k * B)
    for i, s in enumerate(mel):
        if s is not None:
            _place(buf, box(nt(s), 1.7, 0.15), i * B)
    # Downbeats, bar 1 of each 4-bar phrase accented.
    return buf, [(bar * 3 * B, 1.0 if bar % 4 == 0 else 0.7) for bar in range(len(prog))]


def musicbox() -> np.ndarray:
    buf = np.zeros(int(3.5 * SR))
    for i, s in enumerate((24, 22, 19, 15, 12)):
        _place(buf, box(nt(s), 1.9, 0.17), i * 0.30)
    return buf


# ── room and master ───────────────────────────────────────────────────────

def reverb_ir(secs: float, decay: float, rng: np.random.Generator) -> np.ndarray:
    n = int(secs * SR)
    return _noise(n, rng) * (1.0 - np.arange(n) / n) ** decay


def apply_reverb(x: np.ndarray, wet: float, rng: np.random.Generator) -> np.ndarray:
    """The stone hall. Without it the organ is just a synth patch."""
    if wet <= 0:
        return x
    ir = reverb_ir(3.4, 2.4, rng)
    tail = signal.fftconvolve(x, ir)[:len(x)]
    peak = np.max(np.abs(tail))
    if peak > 0:
        tail /= peak
    return x + wet * tail * np.max(np.abs(x) + 1e-9)


def limit(x: np.ndarray, ceiling: float = 0.89) -> np.ndarray:
    """Lookahead limiter. Two overlapping organ chords clip without it.

    Smoothed peak envelope -> per-sample gain -> smoothed again so the gain
    ride is inaudible. Hard clip at the end is a backstop, not the mechanism.
    """
    if np.max(np.abs(x)) <= 0:
        return x
    win = max(1, int(0.005 * SR))
    env = np.convolve(np.abs(x), np.ones(win) / win, mode="same")
    gain = np.ones_like(env)
    over = env > ceiling
    gain[over] = ceiling / env[over]
    smooth = max(1, int(0.02 * SR))
    # Pad with the edge value rather than letting convolve assume zeros past
    # the ends. With mode="same" alone the first and last ~10 ms came back at
    # roughly half gain — inaudible as a de-click on a one-shot, but wind and
    # drone loop, so the seam dipped on every pass.
    pad = smooth // 2
    padded = np.pad(gain, pad, mode="edge")
    gain = np.convolve(padded, np.ones(smooth) / smooth, mode="same")[pad:pad + len(env)]
    return np.clip(x * gain, -1.0, 1.0)


SYNTHS = {
    "wind": lambda rng, dur=30.0: wind(dur, rng),
    "heartbeat": lambda rng, dur=20.0: heartbeat(dur, rng),
    "drone": lambda rng, dur=20.0: drone(dur),
    "whispers": lambda rng, dur=20.0: whispers(dur, rng),
    "thunder": lambda rng, **_: thunder(rng),
    "creak": lambda rng, **_: creak(rng),
    "shriek": lambda rng, **_: shriek(rng),
    "toll": lambda rng, **_: toll(),
    "organ": lambda rng, **_: organ(),
    "descent": lambda rng, **_: descent(),
    "waltz": lambda rng, **_: waltz(),
    "musicbox": lambda rng, **_: musicbox(),
}
