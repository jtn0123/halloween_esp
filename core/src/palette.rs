//! Palettes — the pole pairs the crossfade effects sweep between.
//!
//! Same table, same order, as `web/src/effects.ts` and
//! `firmware/castle_effects.h`. Index 0 is the classic haunt look.

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rgbw {
    pub r: f32,
    pub g: f32,
    pub b: f32,
    pub w: f32,
}

pub const PALETTES: [[[f32; 3]; 2]; 4] = [
    [[0.66, 0.08, 1.00], [0.14, 1.00, 0.42]], // haunt: violet <-> green
    [[0.72, 0.08, 0.00], [1.00, 0.55, 0.05]], // ember: deep red <-> amber
    [[0.10, 0.22, 0.85], [0.72, 0.85, 1.00]], // moonlight: indigo <-> pale blue
    [[0.05, 0.90, 0.10], [0.85, 1.00, 0.05]], // toxic: green <-> acid yellow
];

/// The crossfade the seance/wisp/mansion family is built on. Clamp order and
/// arithmetic order match `mix_pal` in the firmware header exactly.
pub fn mix_pal(mut k: f32, level: f32, pal: i32) -> Rgbw {
    let pal = if !(0..=3).contains(&pal) {
        0
    } else {
        pal as usize
    };
    let a = PALETTES[pal][0];
    let b = PALETTES[pal][1];
    k = 1.0_f32.min(0.0_f32.max(k));
    Rgbw {
        r: (a[0] + (b[0] - a[0]) * k) * level,
        g: (a[1] + (b[1] - a[1]) * k) * level,
        b: (a[2] + (b[2] - a[2]) * k) * level,
        w: 0.0,
    }
}
