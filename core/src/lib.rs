//! castle-core: the Halloween castle's arithmetic — and, now, its show.
//!
//! The crate began as B1 of the typesafe migration plan
//! (.claude/typesafe-migration-plan.md): one home for the effect maths
//! that existed twice, in `firmware/castle_effects.h` (C++, float32) and
//! `web/src/effects.ts` (TypeScript, double), held frame-exact by
//! docs/PARITY.md. That part is still here and still f32, matching the
//! device. But B3 and B5 landed on top of it, and the crate now renders
//! the show, hears imported music, and serves the cue desk — so the old
//! one-line "this is the maths" description had stopped being true.
//!
//! **The faces, and which of them the show actually runs through.** The
//! library is one crate; what it is *used as* is these:
//!
//! - `scene_render` (bin) — **production.** `tools/render_audio.py` spawns
//!   it for every scene; the Python `render_scene_py` survives only as the
//!   parity reference. This is why a render is the same bytes everywhere.
//! - `analyze_track` (bin) — **production.** The importer's ears;
//!   `tools/import_track.py` spawns it instead of running `analyze.py`.
//! - `castle` (bin) — the bridge CLI, a hand tool for the operator. No
//!   Python path goes through it; `tools/castle_link.py` still talks to
//!   the castle for the desk.
//! - `studio` (bin) — a complete twin of `tools/studio.py`, HTTP surface
//!   and all (`httpd`, `studio*`). Finished and parity-gated, but the
//!   Python one is what `make studio` still starts; the flip is off-season
//!   work. Nothing here is the show's path *yet*.
//! - `wasm` (module) — the desk's future effects engine, built to
//!   wasm32-unknown-unknown and proven to load. The desk still runs the
//!   TypeScript copy.
//! - `parity_dump` / `synth_dump` / `pulse_dump` (bins) — **parity only.**
//!   They exist to be diffed: `parity_dump` against the host-compiled C++
//!   in `tests/cxx/`, the other two against numpy, digit for digit.
//!
//! Everything with a Python or C++ twin is held to it by a test that
//! compares values, not behaviour-in-spirit — see docs/PARITY.md. Change
//! one side of a pair and the gate fails; that is the whole design.

pub mod atmos;
pub mod bridge;
pub mod effects;
pub mod fft;
pub mod filters;
pub mod hosts;
pub mod http_parse;
pub mod http_resp;
pub mod httpd;
pub mod jsonio;
pub mod manifest;
pub mod master;
pub mod media;
pub mod netguard;
pub mod noise;
pub mod onsets;
pub mod overlay;
pub mod palette;
pub mod pieces;
pub mod pulse;
pub mod pulse_expand;
pub mod rng;
pub mod scene;
pub mod studio;
pub mod studio_import;
pub mod studio_jobs;
pub mod studio_media;
pub mod studio_probe;
pub mod studio_reason;
pub mod studio_relay;
pub mod studio_routes;
pub mod studio_scenes;
pub mod studio_tracks;
pub mod synth;
pub mod wasm;

pub use effects::render;
pub use noise::{fbm, hash3, hashi, mix32, vnoise};
pub use overlay::{apply_overlay, flash_gate, Fixture};
pub use palette::{mix_pal, Rgbw, PALETTES};
