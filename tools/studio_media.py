#!/usr/bin/env python3
"""Media queries the Tracks panel needs: probing links, and drawing waveforms.

Split out of studio.py to keep both files inside the 500-line cap, and because
these are pure functions over media — no HTTP, no server state — which makes
them testable without standing anything up.
"""

from __future__ import annotations

import collections
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana

# Waveform resolution. ~1000 buckets is more than any reasonable canvas width,
# so the browser downsamples rather than interpolating, and it keeps the JSON
# small enough to not think about.
PEAKS = 1000


def probe(url: str, timeout: int = 60) -> dict:
    """What is at this link, without downloading it.

    The whole reason this exists: choosing `start` and `take` without knowing
    the duration is guesswork. yt-dlp can answer in about a second with
    --dump-json, which turns a blind guess into a decision.

    --no-playlist matters more than it looks. A YouTube link copied while a
    radio queue is playing carries `&list=RD...&start_radio=1`, and without it
    yt-dlp would happily fetch the entire generated queue.
    """
    if not shutil.which("yt-dlp"):
        return {"ok": False, "error": "yt-dlp is not installed (brew install yt-dlp)"}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "that does not look like a link"}

    try:
        r = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-playlist", "--no-warnings", url],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout}s asking about that link"}

    if r.returncode != 0:
        # yt-dlp's own message is usually the useful one ("Video unavailable",
        # "Private video"), so surface its last line rather than a generic.
        tail = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
        return {"ok": False, "error": tail[-1] if tail else "could not read that link"}

    try:
        d = json.loads(r.stdout.splitlines()[0])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": "could not parse what came back"}

    dur = d.get("duration") or 0
    return {
        "ok": True,
        "title": d.get("title") or "",
        "uploader": d.get("uploader") or d.get("channel") or "",
        "duration": dur,
        "duration_text": f"{int(dur) // 60}:{int(dur) % 60:02d}" if dur else "?",
        "thumbnail": d.get("thumbnail") or "",
        "is_live": bool(d.get("is_live")),
        "extractor": d.get("extractor_key") or "",
        # A live stream has no end, so trimming is meaningless and the download
        # would never finish. Worth saying before someone starts one.
        "warning": "this is a live stream — it has no end to trim from"
                   if d.get("is_live") else "",
    }


"""Codec comparisons, kept until a few newer ones push them out.

Each comparison is a directory of encodes the browser then streams. They are
not worth persisting — the inputs are in the panel and re-encoding takes a
second — so they live in a temp directory and the oldest are dropped rather
than accumulating for the life of the process.
"""
_COMPARES: dict[str, Path] = {}
KEEP_COMPARES = 3


def compare(path: Path, opts: dict, token: str) -> dict:
    """Encode one clip every way, and score each against the lossless one.

    See tools/codec_compare.py for what the score is and — more importantly —
    what it is not. The point of this endpoint is the audio it produces; the
    number is there so two encodes that sound close can still be ranked.
    """
    import codec_compare as cc

    root = Path(tempfile.mkdtemp(prefix="castle-cmp-"))
    try:
        rows = cc.encode_set(path, root, opts)
    except SystemExit as e:                      # ffmpeg said no
        shutil.rmtree(root, ignore_errors=True)
        return {"ok": False, "error": str(e)}

    _COMPARES[token] = root
    for old in list(_COMPARES)[:-KEEP_COMPARES]:
        shutil.rmtree(_COMPARES.pop(old), ignore_errors=True)

    for r in rows:
        r["url"] = f"/api/compare/{token}/{r['codec']}"
    return {"ok": True, "token": token, "reference": cc.REFERENCE, "codecs": rows}


def compare_file(token: str, codec: str) -> Path | None:
    """The encode behind one row of a comparison, or None."""
    root = _COMPARES.get(token)
    if root is None or codec not in cc_codecs():
        return None
    p = root / f"{codec}.{codec}"
    return p if p.exists() else None


def cc_codecs() -> tuple[str, ...]:
    import codec_compare as cc
    return cc.CODECS


# Finished waveforms, keyed by (path, mtime_ns, sensitivity, buckets). The
# clip editor asks for the same track again every time it opens, and the
# audition re-asks at each sensitivity nudge; the answer is ~0.8 s of decode
# + STFT each time and changes only when the file or the knob does. The mtime
# in the key is the staleness rule — a re-import is a new file. Bounded so a
# long session with a big library cannot grow it without limit.
_WAVES: collections.OrderedDict[tuple, dict] = collections.OrderedDict()
KEEP_WAVES = 32


def waveform(path: Path, sensitivity: float | dict = 1.1,
             buckets: int = PEAKS) -> dict:
    """Peak envelope plus detected onsets, for the clip editor — cached.

    Peaks are absolute maxima per bucket rather than RMS: the point of the
    picture is to see where the transients are so you can line a selection up
    with them, and RMS smooths exactly that away.
    """
    key = (str(path), path.stat().st_mtime_ns,
           json.dumps(sensitivity, sort_keys=True), buckets)
    hit = _WAVES.get(key)
    if hit is not None:
        _WAVES.move_to_end(key)
        return hit
    out = _waveform(path, sensitivity, buckets)
    _WAVES[key] = out
    while len(_WAVES) > KEEP_WAVES:
        _WAVES.popitem(last=False)
    return out


def _waveform(path: Path, sensitivity: float | dict, buckets: int) -> dict:
    x = ana.load_audio(path)
    dur = len(x) / ana.SR
    if len(x) == 0:
        return {"id": path.stem, "duration": 0.0, "peaks": [], "onsets": {}}

    n = min(buckets, len(x))
    edges = np.linspace(0, len(x), n + 1).astype(int)
    peaks = [float(np.abs(x[a:b]).max()) if b > a else 0.0
             for a, b in itertools.pairwise(edges)]
    top = max(peaks) or 1.0
    peaks = [round(p / top, 4) for p in peaks]

    # Stereo alongside the mono: onsets carry their pan as a third element,
    # which the audition uses to route hits between the towers.
    marks = ana.analyze_full(x, sensitivity=sensitivity,
                             stereo=ana.load_stereo(path))
    # Full-range loudness over time, alongside the onsets. Onsets say WHEN
    # something hit; this says how big the music is right now, which is what
    # lets a generated scene dim for the spoken verse and bloom for the
    # chorus instead of holding one level for three minutes.
    env = ana.envelope(x, bands=[("onset_full", 20, 16000, 0.0)])
    return {
        "id": path.stem,
        "duration": round(dur, 3),
        "peaks": peaks,
        "onsets": {k: [[round(h[0], 3), *h[1:]] for h in v_]
                   for k, v_ in marks.items()},
        "env": [[round(t, 3), v] for t, v in env.get("level_full", [])],
    }
