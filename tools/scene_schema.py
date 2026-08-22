"""What a scene block may say — checked before it reaches scenes.yaml.

One function, `validate(scene, zones=None) -> list[str]`: an empty list
means the scene is well-formed; otherwise every problem found, each as
one sentence the desk can show next to the field. The studio calls it
before splicing a block into the show (a 400 with the list, instead of a
clean write followed by a failed render) and gen_esphome calls it before
emitting, so the generator and the editor reject the same things.

The vocabularies come from gen_esphome — effect ids, overlays, palettes,
flash modes — rather than a second copy here: that module is the contract
with firmware/castle_effects.h, and a list kept in sync by comment is the
exact drift this file exists to prevent. Imported lazily because
gen_esphome imports this module.

Deliberately NOT checked: that `audio_file` exists (render_audio does,
against the configured library), and anything a synth or a pulse stream
interprets for itself — this is the shape of the block, not the show.
"""

from __future__ import annotations

import re
from typing import Any

import effect_vocab as ev

REQUIRED = ("id", "name", "kind", "duration_ms", "base")
CUE_OPS = ("set", "strike")
ID_RE = re.compile(r"^\w+$", re.ASCII)   # \w, but not the Unicode half


def _vocab() -> dict[str, set[str]]:
    return {"effect": set(ev.EFFECT_IDS), "overlay": set(ev.OVERLAY_IDS),
            "palette": set(ev.PALETTE_IDS), "pixels": set(ev.FLASH_MODE_IDS)}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and v == v and v not in (float("inf"), float("-inf"))


def _unit(where: str, v: Any, errs: list[str], lo: float = 0.0,
          hi: float = 1.0) -> None:
    if not _num(v) or not lo <= v <= hi:
        errs.append(f"{where}: must be a number from {lo:g} to {hi:g}, got {v!r}")


def _color(where: str, v: Any, errs: list[str]) -> None:
    if (not isinstance(v, list) or len(v) not in (3, 4)
            or not all(_num(c) and 0 <= c <= 1 for c in v)):
        errs.append(f"{where}: must be [r, g, b(, w)] with each from 0 to 1")


def _zone(where: str, z: Any, zones: set[str] | None, errs: list[str]) -> None:
    if not isinstance(z, str) or not z:
        errs.append(f"{where}: zone must be a name")
    elif zones is not None and z not in zones:
        errs.append(f"{where}: no zone {z!r} (have {', '.join(sorted(zones))})")


def _effect(where: str, e: Any, vocab: dict[str, set[str]],
            errs: list[str]) -> None:
    if e not in vocab["effect"]:
        errs.append(f"{where}: unknown effect {e!r} "
                    f"(one of {', '.join(sorted(vocab['effect']))})")


def _in(where: str, v: Any, kind: str, vocab: dict[str, set[str]],
        errs: list[str]) -> None:
    if v not in vocab[kind]:
        errs.append(f"{where}: unknown {kind} {v!r} "
                    f"(one of {', '.join(sorted(vocab[kind]))})")


def _check_cue(i: int, c: Any, length: int | None, zones: set[str] | None,
               vocab: dict[str, set[str]], errs: list[str]) -> None:
    w = f"cues[{i}]"
    if not isinstance(c, dict):
        errs.append(f"{w}: must be a mapping")
        return
    t: Any = c.get("t")
    if not _num(t) or t < 0:
        errs.append(f"{w}: t must be a time in ms >= 0, got {t!r}")
    elif length is not None and t > length:
        errs.append(f"{w}: t={t:g} is past the scene's duration_ms={length}")
    op = c.get("op")
    if op not in CUE_OPS:
        errs.append(f"{w}: op must be one of {', '.join(CUE_OPS)}, got {op!r}")
        return
    if op == "set":
        if "zone" not in c:
            errs.append(f"{w}: a set cue needs a zone")
        else:
            _zone(f"{w}.zone", c["zone"], zones, errs)
        if "effect" not in c:
            errs.append(f"{w}: a set cue needs an effect")
        else:
            _effect(f"{w}.effect", c["effect"], vocab, errs)
        if "level" in c:
            _unit(f"{w}.level", c["level"], errs)
        return
    # strike
    if "zone" in c:
        _zone(f"{w}.zone", c["zone"], zones, errs)
    if "targets" in c:
        if not isinstance(c["targets"], list):
            errs.append(f"{w}.targets: must be a list of zones")
        else:
            for z in c["targets"]:
                _zone(f"{w}.targets", z, zones, errs)
    if "pixels" in c:
        _in(f"{w}.pixels", c["pixels"], "pixels", vocab, errs)
    if "intensity" in c:
        _unit(f"{w}.intensity", c["intensity"], errs, 0, 4)
    if "decay" in c:
        _unit(f"{w}.decay", c["decay"], errs)
    errs.extend(f"{w}.{k}: must be a number of ms >= 0, got {c[k]!r}"
                for k in ("ms", "attack")
                if k in c and (not _num(c[k]) or c[k] < 0))
    if "color" in c:
        _color(f"{w}.color", c["color"], errs)


def _pulse_zones(w: str, p: dict, zones: set[str] | None,
                 errs: list[str]) -> None:
    """Where a pulse lands: one zone, or lists of them."""
    if "zone" in p:
        _zone(f"{w}.zone", p["zone"], zones, errs)
    for k in ("zones", "boost_targets"):
        if k not in p:
            continue
        if not isinstance(p[k], list):
            errs.append(f"{w}.{k}: must be a list of zones")
            continue
        for z in p[k]:
            _zone(f"{w}.{k}", z, zones, errs)


def _pulse_colors(w: str, p: dict, errs: list[str]) -> None:
    """A pulse's colours: two named ones, plus an optional palette to pick
       from. A `colors:` that is present but empty is a mistake worth a
       message — the render would silently fall back to white."""
    for k in ("color", "color_hot"):
        if k in p:
            _color(f"{w}.{k}", p[k], errs)
    if "colors" not in p:
        return
    if not isinstance(p["colors"], list) or not p["colors"]:
        errs.append(f"{w}.colors: must be a non-empty list of colours")
        return
    for c in p["colors"]:
        _color(f"{w}.colors", c, errs)


def _pulse_shape(w: str, p: dict, vocab: dict[str, set[str]],
                 errs: list[str]) -> None:
    """How hard it hits and how it falls."""
    if "pixels" in p:
        _in(f"{w}.pixels", p["pixels"], "pixels", vocab, errs)
    if "intensity" in p:
        _unit(f"{w}.intensity", p["intensity"], errs, 0, 4)
    if "decay" in p:
        _unit(f"{w}.decay", p["decay"], errs)
    errs.extend(f"{w}.{k}: must be a number of ms >= 0, got {p[k]!r}"
                for k in ("ms", "attack_ms")
                if k in p and (not _num(p[k]) or p[k] < 0))


def _check_pulse(i: int, p: Any, zones: set[str] | None,
                 vocab: dict[str, set[str]], errs: list[str]) -> None:
    w = f"pulse[{i}]"
    if not isinstance(p, dict):
        errs.append(f"{w}: must be a mapping")
        return
    if not isinstance(p.get("synth"), str) or not p["synth"]:
        errs.append(f"{w}: needs a synth (the marker stream it follows)")
    _pulse_zones(w, p, zones, errs)
    _pulse_shape(w, p, vocab, errs)
    _pulse_colors(w, p, errs)


def _scene_head(scene: dict, errs: list[str]) -> int | None:
    """id, name, kind, duration, volume, loop, audio_file — the scalars.
       Returns the scene's length in ms when it has a usable one."""
    errs.extend(f"missing required key {k!r}" for k in REQUIRED if k not in scene)
    sid = scene.get("id")
    if "id" in scene and (not isinstance(sid, str) or not ID_RE.match(sid)):
        errs.append(f"id: letters, digits and _ only, got {sid!r}")
    errs.extend(f"{k}: must be a non-empty string" for k in ("name", "kind")
                if k in scene and (not isinstance(scene[k], str)
                                   or not scene[k].strip()))
    if "volume" in scene:
        _unit("volume", scene["volume"], errs)
    if "loop" in scene and not isinstance(scene["loop"], bool):
        errs.append(f"loop: must be true or false, got {scene['loop']!r}")
    if "audio_file" in scene:
        af = scene["audio_file"]
        if (not isinstance(af, str) or not af or af.startswith("/")
                or ".." in af.split("/")):
            errs.append(f"audio_file: must be a relative path, got {af!r}")
    if "duration_ms" not in scene:
        return None
    d = scene["duration_ms"]
    if not _num(d) or d <= 0 or int(d) != d:
        errs.append(f"duration_ms: must be a whole number of ms > 0, got {d!r}")
        return None
    return int(d)


def _mapping(name: str, value: Any, complaint: str, errs: list[str]) -> bool:
    """`value` is a mapping, or one error saying what it should have been."""
    if isinstance(value, dict):
        return True
    errs.append(f"{name}: {complaint}")
    return False


def _scene_zones(scene: dict, zs: set[str] | None,
                 vocab: dict[str, set[str]], errs: list[str]) -> None:
    """base, levels and zones: three maps keyed by zone, each with its own
       idea of what a value is."""
    if "base" in scene and _mapping(
            "base", scene["base"], "must map each zone to an effect", errs):
        for z, e in scene["base"].items():
            _zone("base", z, zs, errs)
            _effect(f"base.{z}", e, vocab, errs)
    levels = scene.get("levels")
    if levels is not None and _mapping(
            "levels", levels, "must map zones to a level", errs):
        for z, v in levels.items():
            _zone("levels", z, zs, errs)
            _unit(f"levels.{z}", v, errs)
    zd = scene.get("zones")
    if zd is not None and _mapping(
            "zones", zd, "must map zones to their texture", errs):
        for z, d in zd.items():
            _zone("zones", z, zs, errs)
            _zone_texture(z, d, vocab, errs)


def _zone_texture(z: str, d: Any, vocab: dict[str, set[str]],
                  errs: list[str]) -> None:
    """One zone's entry under `zones:` — centre effect, overlay, palette, phase."""
    if not _mapping(f"zones.{z}", d, "must be a mapping", errs):
        return
    if "center" in d:
        _effect(f"zones.{z}.center", d["center"], vocab, errs)
    for k in ("overlay", "palette"):
        if k in d:
            _in(f"zones.{z}.{k}", d[k], k, vocab, errs)
    if "phase" in d and not _num(d["phase"]):
        errs.append(f"zones.{z}.phase: must be a number")


def validate(scene: Any, zones: list[str] | None = None) -> list[str]:
    """Every way `scene` is not a scene — [] when it is one.

    `zones` is the show's zone list when the caller knows it; without it,
    zone names are only checked for shape.
    """
    if not isinstance(scene, dict):
        return ["scene must be a mapping"]
    errs: list[str] = []
    vocab = _vocab()
    zs = set(zones) if zones is not None else None
    length = _scene_head(scene, errs)
    _scene_zones(scene, zs, vocab, errs)
    cues = scene.get("cues")
    if cues is not None:
        if isinstance(cues, list):
            for i, c in enumerate(cues):
                _check_cue(i, c, length, zs, vocab, errs)
        else:
            errs.append("cues: must be a list")
    pulse = scene.get("pulse")
    if pulse is not None:
        if isinstance(pulse, list):
            for i, pl in enumerate(pulse):
                _check_pulse(i, pl, zs, vocab, errs)
        else:
            errs.append("pulse: must be a list")
    return errs
