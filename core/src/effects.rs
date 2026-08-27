//! The effect vocabulary — a line-for-line port of the `render` switch in
//! `firmware/castle_effects.h`. Same formulas, same f32 evaluation order;
//! `tests/test_castle_core.py` holds every channel to the same bits.

use crate::noise::{fbm, vnoise};
use crate::palette::{mix_pal, Rgbw};

pub const EFF_OFF: i32 = 0;
pub const EFF_CANDLE: i32 = 1;
pub const EFF_EMBER: i32 = 2;
pub const EFF_FURNACE: i32 = 3;
pub const EFF_SPIRIT: i32 = 4;
pub const EFF_EYES: i32 = 5;
pub const EFF_SEANCE: i32 = 6;
pub const EFF_WISP: i32 = 7;
pub const EFF_MANSION: i32 = 8;
pub const EFF_CHILL: i32 = 9;
pub const EFF_THROB: i32 = 10;
pub const EFF_STROBE: i32 = 11;
pub const EFF_BLOOD: i32 = 12;

const BLACK: Rgbw = Rgbw {
    r: 0.0,
    g: 0.0,
    b: 0.0,
    w: 0.0,
};

/// One pixel of one frame. `seed` varies per pixel so a flame moves ACROSS
/// the fixture; `hue` biases the crossfades; `soft` damps hard strobing
/// (~7 Hz white strobe is a photosensitivity risk).
pub fn render(eff: i32, t: f32, seed: f32, hue: f32, soft: bool, pal: i32) -> Rgbw {
    match eff {
        EFF_CANDLE => {
            let n = fbm(t * 1.4 + seed * 3.7);
            let l = 0.0_f32.max(1.0 - 0.55 * (1.0 - n));
            Rgbw {
                r: 0.34 * l,
                g: 0.05 * l,
                b: 0.0,
                w: 1.00 * l,
            }
        }
        EFF_EMBER => {
            let n = fbm(t * 0.63 + seed * 2.2);
            let l = 0.22 + 0.16 * n;
            Rgbw {
                r: 0.40 * l,
                g: 0.06 * l,
                b: 0.0,
                w: 0.85 * l,
            }
        }
        EFF_FURNACE => {
            let n = fbm(t * 2.5 + seed * 0.9);
            let l = 0.80 + 0.20 * n;
            Rgbw {
                r: 1.00 * l,
                g: 0.22 * l,
                b: 0.02 * l,
                w: 0.55 * l,
            }
        }
        EFF_SPIRIT => {
            let b = 0.5 + 0.5 * (t * 1.15 + seed * 0.8).sin();
            let l = 0.22 + 0.42 * b;
            Rgbw {
                r: 0.10 * l,
                g: 1.00 * l,
                b: 0.66 * l,
                w: 0.0,
            }
        }
        EFF_EYES => {
            let blink = if vnoise(t * 1.9 + seed * 0.55) > 0.82 {
                0.10
            } else {
                1.0
            };
            let l = (0.55 + 0.28 * (t * 3.1).sin()) * blink;
            Rgbw {
                r: 1.00 * l,
                g: 0.05 * l,
                b: 0.03 * l,
                w: 0.0,
            }
        }
        EFF_SEANCE => {
            let b = 0.5 + 0.5 * (t * 0.80 + seed * 0.6).sin();
            mix_pal(0.0, 0.24 + 0.52 * b, pal)
        }
        EFF_WISP => {
            let n = fbm(t * 2.1 + seed * 5.3);
            let l = 0.0_f32.max(0.18 + 0.82 * n - 0.14);
            mix_pal(1.0, l, pal)
        }
        EFF_MANSION => {
            let sweep = 0.5 + 0.5 * (t * 0.38 + seed * 0.7).sin();
            let shimmer = 0.84 + 0.16 * fbm(t * 1.05 + seed * 2.7);
            mix_pal(sweep * 0.8 + (hue - 0.5) * 0.9, 0.62 * shimmer, pal)
        }
        EFF_CHILL => {
            let b = 0.5 + 0.5 * (t * 0.50 + seed * 1.1).sin();
            mix_pal(hue * 0.35, 0.14 + 0.16 * b, pal)
        }
        EFF_THROB => {
            let mut p = 0.5 + 0.5 * (t * 7.4 + seed * 0.4).sin();
            p *= p;
            mix_pal(hue * 0.5, 0.20 + 0.80 * p, pal)
        }
        EFF_STROBE => {
            if soft {
                let l = 0.34 + 0.44 * (0.5 + 0.5 * (t * 3.1 + seed).sin());
                return Rgbw {
                    r: 0.10 * l,
                    g: 0.10 * l,
                    b: 0.14 * l,
                    w: 1.00 * l,
                };
            }
            let on = if (t * 44.0 + seed).sin() > 0.0 {
                1.0
            } else {
                0.06
            };
            Rgbw {
                r: 0.12 * on,
                g: 0.12 * on,
                b: 0.18 * on,
                w: 1.00 * on,
            }
        }
        EFF_BLOOD => {
            let n = fbm(t * 0.35 + seed * 1.7);
            let l = 0.045 + 0.05 * n;
            Rgbw {
                r: 1.00 * l,
                g: 0.02 * l,
                b: 0.01 * l,
                w: 0.0,
            }
        }
        _ => BLACK,
    }
}
