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

use castle_core::bridge::crc32;
use castle_core::rng::Pcg64;
use castle_core::synth;
use std::io::BufRead;

/// A buffer's compact fingerprint: crc32 of the f64 LE bytes, the length,
/// and 16 strided probe samples printed {:?} — enough to prove bit
/// equality and to say WHERE it broke when it does.
fn digest(buf: &[f64]) -> String {
    let mut bytes = Vec::with_capacity(buf.len() * 8);
    for v in buf {
        bytes.extend_from_slice(&v.to_le_bytes());
    }
    let stride = 1.max(buf.len() / 16);
    let probes: Vec<String> = (0..buf.len())
        .step_by(stride)
        .map(|i| format!("{:?}", buf[i]))
        .collect();
    format!("{:08x} {} {}", crc32(&bytes), buf.len(), probes.join(" "))
}

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
            "note" => {
                // note <voice> <f> <dur> <vel> [stops] — seed slot held the
                // voice name here, so re-read it as text.
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let voice = rest.next().unwrap_or("");
                let mut num =
                    |d: f64| -> f64 { rest.next().and_then(|v| v.parse().ok()).unwrap_or(d) };
                let (f, dur, vel) = (num(220.0), num(1.0), num(1.0));
                let buf = match voice {
                    "pipe" => synth::pipe(f, dur, vel, num(synth::STOPS)),
                    "piano" => synth::piano(f, dur, vel),
                    "box" => synth::music_box(f, dur, vel),
                    _ => {
                        println!("ERR unknown voice {voice}");
                        continue;
                    }
                };
                println!("{}", digest(&buf));
            }
            other => println!("ERR unknown op {other}"),
        }
    }
}
