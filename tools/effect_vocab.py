"""The effect vocabulary — one table, four consumers.

Every name a scene may use for a zone's base effect, an overlay, a palette
or a strike mask lives here, with the integer the firmware's switch wants.
tools/gen_esphome.py and tools/gen_previewer.py import it; tools/scene_schema.py
validates against it; tests/test_generator_parity.py holds it equal to the
names parsed out of web/src/effects.ts and firmware/castle_effects.h, so a
new effect that lands in only some of the four is a red test, not a dark
window on the night.

Integers rather than strings on the wire so the render hot path is a
switch, not a string compare. Order here IS the id order.
"""

from __future__ import annotations

EFFECT_IDS: dict[str, int] = {
    "off": 0,
    "candle": 1,
    "ember": 2,
    "furnace": 3,
    "spirit": 4,
    "eyes": 5,
    "seance": 6,
    "wisp": 7,
    "mansion": 8,
    "chill": 9,
    "throb": 10,
    "strobe": 11,
    "blood": 12,
}

# Per-pixel texture vocabularies — indices match the enums in
# firmware/castle_effects.h and the *_NAMES arrays in web/src/effects.ts.
OVERLAY_IDS: dict[str, int] = {"none": 0, "sparkle": 1, "chase": 2, "meteor": 3}
PALETTE_IDS: dict[str, int] = {"haunt": 0, "ember": 1, "moonlight": 2, "toxic": 3}
FLASH_MODE_IDS: dict[str, int] = {"all": 0, "scatter": 1, "center": 2, "ring": 3}

#: The base-effect names as a set, for the "is this a known effect" check.
KNOWN_EFFECTS: frozenset[str] = frozenset(EFFECT_IDS)
