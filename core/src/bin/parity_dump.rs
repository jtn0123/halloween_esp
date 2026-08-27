//! Dump what castle-core computes for the SAME seeded corpus as
//! tests/cxx/parity_dump.cpp — the C++ harness that prints what the
//! firmware would. tests/test_castle_core.py runs both and compares the
//! numbers bit for bit: same LCG, same draw order, same arithmetic.
//!
//!     parity_dump [seed] [cases]
//!
//! Pass 1 emits the noise-primitive lines only; the px (effect) lines
//! arrive with the effects port.

use castle_core::{fbm, hash3, hashi, vnoise};

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
    let _cases: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(3000);
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
}
