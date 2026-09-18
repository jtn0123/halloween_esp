//! The effect vocabulary — `tools/effect_vocab.py`'s names, in id order.
//!
//! The Python table is the one four consumers already share (the two
//! generators, `scene_schema`, and the parity test that reads the names
//! out of `web/src/effects.ts` and `firmware/castle_effects.h`). This is
//! the fifth copy, needed because the studio's scene validator lives here
//! now and the crate has no dependencies to read YAML or Python with.
//!
//! Order IS the id order — index equals the integer the firmware's switch
//! wants, and `effects.rs`'s `EFF_*` constants number the same way.
//! `tests/test_pulse_dynamics_parity.py` holds this file to
//! `effect_vocab.py` name for name and position for position, so a new
//! effect that lands in only some of the five is a red test rather than a
//! dark window on the night.

/// Base effects: `EFFECTS[i]` is the name of effect id `i`.
pub const EFFECTS: [&str; 13] = [
    "off", "candle", "ember", "furnace", "spirit", "eyes", "seance", "wisp", "mansion", "chill",
    "throb", "strobe", "blood",
];

/// Per-pixel overlays.
pub const OVERLAYS: [&str; 4] = ["none", "sparkle", "chase", "meteor"];

/// Palette poles the mansion effects crossfade between.
pub const PALETTES: [&str; 4] = ["haunt", "ember", "moonlight", "toxic"];

/// Strike masks — which pixels a strike lights.
pub const FLASH_MODES: [&str; 4] = ["all", "scatter", "center", "ring"];

/// The card's scene manifest holds this many scenes — `show.man` is a fixed
/// record count so the firmware never allocates to read it
/// (`tools/scene_manifest.py` `MAX_SCENES`, `castle_scenes::kMaxScenes`). It
/// was a dram0 measurement until v5.67, when the timelines moved to the card
/// and the per-scene RAM cost went away; the number stayed. The other copy is
/// `tools/check_loc.py`'s `SCENE_LIMIT`, which fails `make check` above it;
/// `tests/test_loc_scope.py` holds the two equal. Raising it means raising all
/// four together — see scenes/scenes.yaml's header.
pub const SCENE_LIMIT: usize = 12;
