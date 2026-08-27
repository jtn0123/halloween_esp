//! The WASM face — flat extern "C" exports for the cue desk.
//!
//! No wasm-bindgen: the crate stays zero-dependency and the module tiny
//! enough to inline into the previewer page. Callers write results through
//! a fixed 4-float scratch buffer (`out_ptr`), the classic no-allocator
//! WASM calling convention. The desk swap itself is post-Halloween work;
//! until then tests/test_castle_core.py proves the artifact builds, loads
//! and computes.

use crate::effects::render;
use crate::noise::{fbm, hash3, hashi, vnoise};
use core::cell::UnsafeCell;

struct Scratch(UnsafeCell<[f32; 4]>);
// SAFETY: WASM is single-threaded; there is exactly one caller.
unsafe impl Sync for Scratch {}
static OUT: Scratch = Scratch(UnsafeCell::new([0.0; 4]));

/// Where `wasm_render` leaves its four channels.
#[no_mangle]
pub extern "C" fn out_ptr() -> *const f32 {
    OUT.0.get() as *const f32
}

#[no_mangle]
pub extern "C" fn wasm_render(eff: i32, t: f32, seed: f32, hue: f32, soft: i32, pal: i32) {
    let c = render(eff, t, seed, hue, soft != 0, pal);
    // SAFETY: single-threaded module, single scratch owner (see Scratch).
    unsafe { *OUT.0.get() = [c.r, c.g, c.b, c.w] };
}

#[no_mangle]
pub extern "C" fn wasm_hashi(i: i32) -> f32 {
    hashi(i)
}

#[no_mangle]
pub extern "C" fn wasm_hash3(a: i32, b: i32, c: i32) -> f32 {
    hash3(a, b, c)
}

#[no_mangle]
pub extern "C" fn wasm_vnoise(x: f32) -> f32 {
    vnoise(x)
}

#[no_mangle]
pub extern "C" fn wasm_fbm(x: f32) -> f32 {
    fbm(x)
}
