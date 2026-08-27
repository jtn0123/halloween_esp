//! Dump what castle-core computes for the SAME seeded corpus as
//! tests/cxx/parity_dump.cpp — the C++ harness that prints what the
//! firmware would. tests/test_castle_core.py runs both and compares the
//! numbers bit for bit: same LCG, same draw order, same arithmetic.
//!
//!     parity_dump [seed] [cases] [zone-pixel-counts, e.g. 7,7,12]
//!
//! Without the zone list only the noise-primitive lines are emitted. With
//! it, the px lines follow — the zone counts come from the C++ dump's own
//! zone lines, so both sides walk the identical corpus. The px lines carry
//! the base effect colour; overlay and gate arrive with the fixture port.

use castle_core::{fbm, hash3, hashi, render, vnoise};

struct Lcg(u32);

impl Lcg {
    fn next_u32(&mut self) -> u32 {
        self.0 = self.0.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
        self.0
    }
    fn frand(&mut self) -> f32 {
        (self.next_u32() >> 8) as f32 / 16_777_216.0
    }
}

fn main() {
    let mut args = std::env::args().skip(1);
    let seed: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(7);
    let cases: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(3000);
    let zones: Vec<u32> = args
        .next()
        .map(|z| z.split(',').filter_map(|n| n.parse().ok()).collect())
        .unwrap_or_default();
    let mut rng = Lcg(seed);

    for i in 0..400u32 {
        let k: i32 = match i % 4 {
            0 => ((rng.next_u32() >> 16) % 64) as i32,
            1 => ((rng.next_u32() >> 8) % 2_000_000) as i32,
            2 => -(((rng.next_u32() >> 16) % 1000) as i32),
            _ => (rng.next_u32() ^ 0x8000_0000) as i32,
        };
        let a = ((rng.next_u32() >> 8) % 300_000) as i32;
        let b = ((rng.next_u32() >> 16) % 64) as i32;
        let c = ((rng.next_u32() >> 16) % 1000) as i32;
        let x = if i & 1 == 1 {
            rng.frand() * 40.0
        } else {
            rng.frand() * 60_000.0
        };
        // {:?} on the f32-promoted f64 prints the shortest round-trip, the
        // same VALUE the C side's %.17g names; the reader parses both.
        println!(
            "{{\"kind\":\"noise\",\"k\":{},\"hashi\":{:?},\"a\":{},\"b\":{},\"c\":{},\"hash3\":{:?},\"x\":{:?},\"vnoise\":{:?},\"fbm\":{:?}}}",
            k,
            hashi(k) as f64,
            a,
            b,
            c,
            hash3(a, b, c) as f64,
            x as f64,
            vnoise(x) as f64,
            fbm(x) as f64,
        );
    }

    if zones.is_empty() {
        return;
    }
    let nz = zones.len() as u32;
    for i in 0..cases {
        let eff = ((rng.next_u32() >> 16) % 13) as i32;
        let pal = ((rng.next_u32() >> 16) % 4) as i32;
        let hue = match i % 9 {
            0 => 0.0,
            1 => 1.0,
            _ => rng.frand(),
        };
        let soft = ((rng.next_u32() >> 16) & 1) != 0;
        let t = match i % 4 {
            0 => rng.frand() * 10.0,
            1 => rng.frand() * 600.0,
            2 => rng.frand() * 36_000.0,
            _ => ((rng.next_u32() >> 16) % 4096) as f32 / 64.0,
        };
        let zi = (rng.next_u32() >> 16) % nz;
        let n = zones[zi as usize];
        if n == 0 {
            continue;
        }
        let p = (rng.next_u32() >> 16) % n;
        let ov = (rng.next_u32() >> 16) % 4;
        let mode = (rng.next_u32() >> 16) % 4;
        let epoch = (rng.next_u32() >> 16) % 1000;
        let seed_f = zi as f32 * 4.7 + p as f32 * 1.31;
        let base = render(eff, t, seed_f, hue, soft, pal);
        println!(
            "{{\"kind\":\"px\",\"eff\":{},\"pal\":{},\"hue\":{:?},\"soft\":{},\"t\":{:?},\"zi\":{},\"p\":{},\"ov\":{},\"mode\":{},\"epoch\":{},\"seed\":{:?},\"base\":[{:?},{:?},{:?},{:?}]}}",
            eff, pal, hue as f64, if soft { 1 } else { 0 }, t as f64, zi, p, ov, mode,
            epoch, seed_f as f64, base.r as f64, base.g as f64, base.b as f64,
            base.w as f64,
        );
    }
}
