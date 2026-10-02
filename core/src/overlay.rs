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
    /// Drawn position, 0 = left .. 1 = right (the v5.71 half masks).
    pub x: Vec<f32>,
    /// Drawn position, 0 = top .. 1 = bottom.
    pub y: Vec<f32>,
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

/// `head_at` is a look record's tempo-locked head in turns (the chase head,
/// and the meteor's drip phase); negative is the legacy clock, digit for
/// digit what every v1 show runs.
pub fn apply_overlay(
    ov: i32,
    c: Rgbw,
    t: f32,
    p: i32,
    zi: i32,
    fx: &Fixture,
    head_at: f32,
) -> Rgbw {
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
        let head = if head_at >= 0.0 {
            head_at
        } else {
            (t * 0.45 + zi as f32 * 0.37) % 1.0
        };
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
        let ph = if head_at >= 0.0 {
            head_at
        } else {
            (t / 2.6 + zi as f32 * 0.41) % 1.0
        };
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

/// One drawn coordinate against the midline: 1 on the struck side, 0.5 on
/// the line, 0.1 beyond (`half_gate` in castle_effects.h).
fn half_gate(v: f32, low: bool) -> f32 {
    if v < 0.499 {
        return if low { 1.0 } else { 0.1 };
    }
    if v > 0.501 {
        return if low { 0.1 } else { 1.0 };
    }
    0.5
}

/// Arc modes 8-15 (`arc0`..`arc7`) and the loop they are measured on, in
/// whole steps: 3072 = 8 arcs x 384 = 12 hours x 256.
const ARC_FIRST: i32 = 8;
const ARC_TURN: i32 = 3072;

/// Arc `k`: one patch of the loop centred at walk k/8 — 1 within 1/12 turn,
/// linear to 0.1 at 1/4 turn, 0.1 beyond, 0.3 on a hub's centre
/// (`arc_gate` in castle_effects.h, whose comment says why it is exact).
fn arc_gate(k: i32, p: i32, fx: &Fixture) -> f32 {
    if p == fx.center {
        return 0.3;
    }
    let q = (fx.walk[p as usize] * ARC_TURN as f32 + 0.5).floor() as i32;
    let mut d = (q - k * (ARC_TURN / 8)).rem_euclid(ARC_TURN);
    if d > ARC_TURN / 2 {
        d = ARC_TURN - d;
    }
    let x = (d - 256).clamp(0, 512);
    (1000 - 225 * x / 128) as f32 / 1000.0
}

/// Which pixels a flash actually hits. 0 = whole fixture, 1 = a fresh
/// random scatter per strike, 2 = core only, 3 = everything but the core,
/// 4-7 = the left, right, top and bottom half by drawn position, 8-15 =
/// the arcs round the loop.
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
    match mode {
        4 => half_gate(fx.x[p as usize], true),
        5 => half_gate(fx.x[p as usize], false),
        6 => half_gate(fx.y[p as usize], true),
        7 => half_gate(fx.y[p as usize], false),
        ARC_FIRST..=15 => arc_gate(mode - ARC_FIRST, p, fx),
        _ => 1.0,
    }
}
