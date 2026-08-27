//! Line-protocol dump for the synth parity harness (B3) — the same shape
//! as pulse_dump: tests/test_synth_rust.py feeds ops on stdin and compares
//! every printed value digit-for-digit with numpy's.
//!
//!     raw <seed> <n>                    n uint64 draws
//!     uni <seed> <lo> <hi> <n> <mode>   n uniforms; mode fma|plain picks
//!                                       the host numpy's compiled form
//!
//! The seed parses as a full u128 (SeedSequence entropy can exceed f64's
//! integers). Values print with {:?} (shortest round-trip); the Python
//! side parses them back with float()/int() so spelling cannot false-fail.

use castle_core::rng::Pcg64;
use std::io::BufRead;

fn main() {
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = line.unwrap_or_default();
        let mut it = line.split_whitespace();
        let op = it.next().unwrap_or("");
        if op.is_empty() {
            continue;
        }
        let seed: u128 = it.next().and_then(|v| v.parse().ok()).unwrap_or(0);
        match op {
            "raw" => {
                let n: usize = it.next().and_then(|v| v.parse().ok()).unwrap_or(0);
                let mut g = Pcg64::new(seed);
                let vals: Vec<String> = (0..n).map(|_| g.next64().to_string()).collect();
                println!("{}", vals.join(" "));
            }
            "uni" => {
                let mut num =
                    |d: f64| -> f64 { it.next().and_then(|v| v.parse().ok()).unwrap_or(d) };
                let (lo, hi) = (num(0.0), num(1.0));
                let n = num(0.0) as usize;
                let fma = it.next() == Some("fma");
                let mut g = Pcg64::new(seed);
                let vals: Vec<String> = (0..n)
                    .map(|_| {
                        let v = if fma {
                            g.uniform_fma(lo, hi)
                        } else {
                            g.uniform_plain(lo, hi)
                        };
                        format!("{v:?}")
                    })
                    .collect();
                println!("{}", vals.join(" "));
            }
            other => println!("ERR unknown op {other}"),
        }
    }
}
