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
//!   wasm32-unknown-unknown `--no-default-features` and proven to load. The
//!   desk still runs the TypeScript copy.
//! - `parity_dump` / `synth_dump` / `pulse_dump` (bins) — **parity only.**
//!   They exist to be diffed: `parity_dump` against the host-compiled C++
//!   in `tests/cxx/`, the other two against numpy, digit for digit.
//!
//! Everything with a Python or C++ twin is held to it by a test that
//! compares values, not behaviour-in-spirit — see docs/PARITY.md. Change
//! one side of a pair and the gate fails; that is the whole design.
//!
//! **Two halves, one crate (`native`, default on).** The modules below the
//! `native` line need a machine: files, sockets, child processes, ffmpeg.
//! The ones above it are arithmetic and nothing else, which is exactly what
//! the desk's cdylib wants — so the wasm face is built
//! `--no-default-features` and the whole HTTP server stops compiling into a
//! module that will never listen on anything. That is not only weight: the
//! stubs manifest.rs and studio_jobs.rs used to carry (a flock that does not
//! lock, a kill that does not kill, both live only on wasm32) existed to get
//! a server past a target that cannot run one. Gate the modules and the lies
//! go away. Bins are native by definition and say so in Cargo.toml.
//! The mods stay flat: a dsp::/net:: rename would touch every import in the
//! crate and every test that names a path, for no gate a feature does not
//! already give.

pub mod atmos;
pub mod effects;
pub mod fft;
pub mod filters;
pub mod jsonio;
pub mod master;
pub mod noise;
pub mod overlay;
pub mod palette;
pub mod pieces;
pub mod pulse;
pub mod pulse_expand;
pub mod rng;
pub mod synth;
pub mod wasm;

#[cfg(feature = "native")]
pub mod bridge;
#[cfg(feature = "native")]
pub mod hosts;
#[cfg(feature = "native")]
pub mod http_parse;
#[cfg(feature = "native")]
pub mod http_resp;
#[cfg(feature = "native")]
pub mod httpd;
#[cfg(feature = "native")]
pub mod manifest;
#[cfg(feature = "native")]
pub mod media;
#[cfg(feature = "native")]
pub mod netguard;
#[cfg(feature = "native")]
pub mod onsets;
#[cfg(feature = "native")]
pub mod scene;
#[cfg(feature = "native")]
pub mod studio;
#[cfg(feature = "native")]
pub mod studio_import;
#[cfg(feature = "native")]
pub mod studio_jobs;
#[cfg(feature = "native")]
pub mod studio_media;
#[cfg(feature = "native")]
pub mod studio_probe;
#[cfg(feature = "native")]
pub mod studio_proc;
#[cfg(feature = "native")]
pub mod studio_reason;
#[cfg(feature = "native")]
pub mod studio_relay;
#[cfg(feature = "native")]
pub mod studio_routes;
#[cfg(feature = "native")]
pub mod studio_scenes;
#[cfg(feature = "native")]
pub mod studio_tracks;

pub use effects::render;
pub use noise::{fbm, hash3, hashi, mix32, vnoise};
pub use overlay::{Fixture, apply_overlay, flash_gate};
pub use palette::{PALETTES, Rgbw, mix_pal};
