"""Argument-value parsers for the importer's command line.

Each one refuses, at argparse time, a value that would otherwise reach
ffmpeg or the manifest as something other than what it claims to be: a
flag-shaped time, a note that looks like an option, a band name that
does not exist. import_track.py wires them in; tests and codec_compare
reach them through that module.
"""

from __future__ import annotations

import argparse
import re


def secs(v: str) -> float:
    """Accept 12, 1:05 or 1:02:03."""
    parts = [float(p) for p in str(v).split(":")]
    out = 0.0
    for p in parts:
        out = out * 60 + p
    return out


_NUM = re.compile(r"^\d+(?:\.\d+)?$")


def time_arg(raw: str) -> str:
    """`12`, `1:05` or `1:02:03` — what `secs()` reads. Anything else (a
    flag-shaped "-x", a word, 1:99) is refused before it reaches ffmpeg."""
    parts = raw.strip().split(":")
    ok = (
        1 <= len(parts) <= 3
        and all(_NUM.match(p) for p in parts)
        and all(float(p) < 60 for p in parts[1:])
    )
    if not ok:
        raise argparse.ArgumentTypeError(
            f"not a time: {raw!r} — use seconds (24) or m:ss (0:12)"
        )
    return raw.strip()


def text_arg(raw: str) -> str:
    """Free text that must not look like an option (the studio passes it as
    `--notes=<v>`; a value starting with '-' is refused even so)."""
    if raw.startswith("-"):
        raise argparse.ArgumentTypeError(f"{raw!r} looks like an option, not text")
    return raw


def sensitivity_arg(raw: str) -> float | dict[str, float]:
    """`1.1`, or `low=0.8,mid=1.1,high=1.6`.

    One number for all three bands is usually the wrong answer — a crisp kick
    and a wash of cymbals want different thresholds — but it is the right
    default, so both spellings are accepted and a bare number still means
    "the same everywhere".
    """
    if "=" not in raw:
        try:
            return float(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    out: dict[str, float] = {}
    for part in raw.split(","):
        k, _, v = part.partition("=")
        k = k.strip().replace("onset_", "")
        if k not in ("low", "mid", "high"):
            raise argparse.ArgumentTypeError(
                f"unknown band {k!r} — expected low, mid or high"
            )
        try:
            out[f"onset_{k}"] = float(v)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number for {k}: {v!r}") from None
    return out
