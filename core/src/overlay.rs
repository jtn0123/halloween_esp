//! Overlays and strike masks — per-pixel roles on top of any base effect.
//!
//! Port of `apply_overlay`, `flash_gate` and `loop_dist` in
//! `firmware/castle_effects.h`, plus the `Fixture` geometry those read.
//! The geometry values arrive from outside (generated/rig.h on the device,
//! the parity harness here) — this crate does no layout arithmetic, same
//! as the firmware.

use crate::noise::hash3;
use crate::palette::Rgbw;

/// One zone's fixture geometry. `center` is -1 where the fixture has no
/// middle (a bare ring), mirroring the C++ struct.
#[derive(Clone, Debug)]
pub struct Fixture {
    pub n: i32,
    pub center: i32,
    pub fall_steps: i32,
    pub walk: Vec<f32>,
    pub fall: Vec<f32>,
    pub core: Vec<bool>,
}

pub const OV_NONE: i32 = 0;
pub const OV_SPARKLE: i32 = 1;
pub const OV_CHASE: i32 = 2;
pub const OV_METEOR: i32 = 3;

/// Shortest way between two points on a loop, in turns (0..0.5).
fn loop_dist(a: f32, b: f32) -> f32 {
    let d = (a - b).abs() % 1.0;
    d.min(1.0 - d)
}

pub fn apply_overlay(ov: i32, c: Rgbw, t: f32, p: i32, zi: i32, fx: &Fixture) -> Rgbw {
    if ov == OV_SPARKLE {
        let cell = (t * 7.0).floor() as i32;
        let g = hash3(cell, p, zi);
        if g > 0.93 {
            let k = (g - 0.93) / 0.07;
            return Rgbw {
                r: 1.0_f32.min(c.r + 0.30 * k),
                g: 1.0_f32.min(c.g + 0.30 * k),
                b: 1.0_f32.min(c.b + 0.30 * k),
                w: 1.0_f32.min(c.w + 0.90 * k),
            };
        }
        return c;
    }
    if ov == OV_CHASE {
        if p == fx.center {
            return Rgbw {
                r: c.r * 0.55,
                g: c.g * 0.55,
                b: c.b * 0.55,
                w: c.w * 0.55,
            };
        }
        let head = (t * 0.45 + zi as f32 * 0.37) % 1.0;
        // Width is set in PIXELS, not turns: one lit pixel on any fixture.
        let span = (if fx.center < 0 { fx.n } else { fx.n - 1 }) as f32;
        let boost = 0.0_f32.max(1.0 - loop_dist(fx.walk[p as usize], head) * span * 0.9);
        let k = 0.45 + 0.55 * boost;
        return Rgbw {
            r: c.r * k,
            g: c.g * k,
            b: c.b * k,
            w: 1.0_f32.min(c.w * k + 0.50 * boost * boost),
        };
    }
    if ov == OV_METEOR {
        let ph = (t / 2.6 + zi as f32 * 0.41) % 1.0;
        let rung = 1.0 / 1.0_f32.max((fx.fall_steps - 1) as f32);
        if ph < 0.12 {
            // With a middle the drip forms there; without one, at the top edge.
            let forms = if fx.center >= 0 {
                p == fx.center
            } else {
                fx.fall[p as usize] < rung
            };
            if !forms {
                return c;
            }
            let k = (0.12 - ph) / 0.12;
            return Rgbw {
                r: c.r,
                g: c.g,
                b: c.b,
                w: 1.0_f32.min(c.w + 0.80 * k),
            };
        }
        if p == fx.center {
            return c;
        }
        let front = (ph - 0.12) / 0.88;
        let fade = 1.0 - front * 0.5;
        let d = (fx.fall[p as usize] - front).abs();
        let boost = 0.0_f32.max(1.0 - d / (rung * 1.5)) * fade;
        return Rgbw {
            r: 1.0_f32.min(c.r + 0.20 * boost),
            g: c.g,
            b: 1.0_f32.min(c.b + 0.25 * boost),
            w: 1.0_f32.min(c.w + 0.60 * boost),
        };
    }
    c
}

/// Which pixels a flash actually hits. 0 = whole fixture, 1 = a fresh
/// random scatter per strike, 2 = core only, 3 = everything but the core.
pub fn flash_gate(mode: i32, p: i32, zi: i32, epoch: i32, fx: &Fixture) -> f32 {
    if mode == 1 {
        return if hash3(p, zi, epoch) > 0.45 {
            1.0
        } else {
            0.15
        };
    }
    if mode == 2 {
        return if fx.core[p as usize] { 1.0 } else { 0.1 };
    }
    if mode == 3 {
        return if fx.core[p as usize] { 0.1 } else { 1.0 };
    }
    1.0
}
