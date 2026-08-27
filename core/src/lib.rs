//! castle-core: the effect arithmetic behind the Halloween castle, in Rust.
//!
//! B1 of the typesafe migration plan (.claude/typesafe-migration-plan.md):
//! this crate re-implements the maths that today exists twice — in
//! `firmware/castle_effects.h` (C++, float32) and `web/src/effects.ts`
//! (TypeScript, double) — held frame-exact by docs/PARITY.md. Everything
//! here is f32, matching the device; `tests/test_castle_core.py` compares
//! this crate's `parity_dump` against the host-compiled C++ one bit for bit.

pub mod atmos;
pub mod bridge;
pub mod effects;
pub mod filters;
pub mod hosts;
pub mod master;
pub mod noise;
pub mod overlay;
pub mod palette;
pub mod pieces;
pub mod pulse;
pub mod pulse_expand;
pub mod rng;
pub mod scene;
pub mod synth;
pub mod wasm;

pub use effects::render;
pub use noise::{fbm, hash3, hashi, mix32, vnoise};
pub use overlay::{apply_overlay, flash_gate, Fixture};
pub use palette::{mix_pal, Rgbw, PALETTES};
