//! Noise primitives — the ONE integer hash everything random comes from.
//!
//! Port of the primitives in `firmware/castle_effects.h` (which mirror
//! `web/src/effects.ts`). mix32 is lowbias32 (Chris Wellons): a
//! full-avalanche 32-bit bijection. The result keeps 24 bits, which a
//! float32 holds exactly — so all three languages compute the SAME number.

pub fn mix32(mut x: u32) -> u32 {
    x ^= x >> 16;
    x = x.wrapping_mul(0x7feb_352d);
    x ^= x >> 15;
    x = x.wrapping_mul(0x846c_a68b);
    x ^= x >> 16;
    x
}

fn unit01(h: u32) -> f32 {
    (h >> 8) as f32 * (1.0 / 16_777_216.0)
}

/// Noise at one lattice point (the vnoise cell index). The `as u32` is the
/// C++ `(uint32_t) int32_t` cast and JS's `| 0` wrap: same bits everywhere.
pub fn hashi(i: i32) -> f32 {
    unit01(mix32(i as u32))
}

/// Noise at a triple of small integer coordinates — a time cell, a pixel
/// and a zone for the sparkle; a pixel, a zone and an epoch for the scatter.
pub fn hash3(a: i32, b: i32, c: i32) -> f32 {
    unit01(mix32(
        mix32(mix32(a as u32).wrapping_add(b as u32)).wrapping_add(c as u32),
    ))
}

/// Smoothed value noise — the flame's whole personality. Interpolated, not
/// sampled: per-frame random reads as a loose connection.
pub fn vnoise(x: f32) -> f32 {
    let i = x.floor() as i32;
    let f = x - i as f32;
    let u = f * f * (3.0 - 2.0 * f);
    hashi(i) * (1.0 - u) + hashi(i + 1) * u
}

pub fn fbm(x: f32) -> f32 {
    0.55 * vnoise(x) + 0.30 * vnoise(x * 2.13 + 11.3) + 0.15 * vnoise(x * 4.31 + 27.7)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Golden values printed by firmware/castle_effects.h with %.17g
    /// (clang++ -O1, 2026-08-27) — the f32s promoted to double, exactly.
    #[test]
    fn matches_the_firmware_goldens() {
        assert_eq!(mix32(7), 2_492_178_918);
        assert_eq!(hashi(0) as f64, 0.0);
        assert_eq!(hashi(1) as f64, 0.408_349_037_170_410_16);
        assert_eq!(hashi(-1) as f64, 0.403_938_412_666_320_8);
        assert_eq!(hashi(2_147_483_647) as f64, 0.551_422_059_535_980_2);
        assert_eq!(hash3(1, 2, 3) as f64, 0.248_284_220_695_495_6);
        assert_eq!(hash3(-5, 60_000, 999) as f64, 0.996_346_473_693_847_7);
        assert_eq!(vnoise(1.5) as f64, 0.612_523_555_755_615_2);
        assert_eq!(vnoise(43_210.75) as f64, 0.320_140_093_564_987_2);
        assert_eq!(fbm(2.25) as f64, 0.506_680_011_749_267_6);
        assert_eq!(fbm(59_999.5) as f64, 0.591_780_245_304_107_7);
    }
}
