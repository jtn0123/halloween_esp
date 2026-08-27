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

use castle_core::atmos;
use castle_core::bridge::crc32;
use castle_core::filters;
use castle_core::master;
use castle_core::pieces;
use castle_core::rng::Pcg64;
use castle_core::scene;
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
            "piece" => {
                // piece <name> [dur] — buffer digest, then " | t:v" markers.
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let name = rest.next().unwrap_or("");
                let dur: f64 = rest.next().and_then(|v| v.parse().ok()).unwrap_or(20.0);
                let (buf, marks): (Vec<f64>, Vec<(f64, f64)>) = match name {
                    "drone" => (pieces::drone(dur), Vec::new()),
                    "toll" => pieces::toll(),
                    "organ" => pieces::organ(),
                    "descent" => (pieces::descent(), Vec::new()),
                    "waltz" => pieces::waltz(),
                    "musicbox" => (pieces::musicbox(), Vec::new()),
                    _ => {
                        println!("ERR unknown piece {name}");
                        continue;
                    }
                };
                let ms: Vec<String> = marks.iter().map(|(t, v)| format!("{t:?}:{v:?}")).collect();
                println!("{} | {}", digest(&buf), ms.join(","));
            }
            "blp" | "bbp" => {
                // blp <N> <hz> <modes> · bbp <lo> <hi> <modes>
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let mut num =
                    |d: f64| -> f64 { rest.next().and_then(|v| v.parse().ok()).unwrap_or(d) };
                let (x1, x2) = (num(2.0), num(150.0));
                let m = filters::Modes::parse(rest.next().unwrap_or(""));
                let vals: Vec<String> = if op == "blp" {
                    filters::butter_lp(x1 as usize, x2, &m)
                        .iter()
                        .map(|v| format!("{v:?}"))
                        .collect()
                } else {
                    filters::butter_bp(x1, x2, &m)
                        .iter()
                        .map(|v| format!("{v:?}"))
                        .collect()
                };
                println!("{}", vals.join(" "));
            }
            "sflp" | "sfbp" => {
                // sflp <N> <hz> <seed> <n> <umode> <modes>
                // sfbp <lo> <hi> <seed> <n> <umode> <modes>
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let mut num =
                    |d: f64| -> f64 { rest.next().and_then(|v| v.parse().ok()).unwrap_or(d) };
                let (x1, x2) = (num(2.0), num(150.0));
                let sd: u128 = rest.next().and_then(|v| v.parse().ok()).unwrap_or(0);
                let n: usize = rest.next().and_then(|v| v.parse().ok()).unwrap_or(0);
                let fma_uni = rest.next() == Some("fma");
                let m = filters::Modes::parse(rest.next().unwrap_or(""));
                let mut g = Pcg64::new(sd);
                let x: Vec<f64> = (0..n)
                    .map(|_| {
                        if fma_uni {
                            g.uniform_fma(-1.0, 1.0)
                        } else {
                            g.uniform_plain(-1.0, 1.0)
                        }
                    })
                    .collect();
                let y = if op == "sflp" {
                    filters::sosfilt(&[filters::butter_lp(x1 as usize, x2, &m)], &x, m.sos_fused)
                } else {
                    let c = filters::butter_bp(x1, x2, &m);
                    let rows = [
                        [c[0], c[1], c[2], c[3], c[4], c[5]],
                        [c[6], c[7], c[8], c[9], c[10], c[11]],
                    ];
                    filters::sosfilt(&rows, &x, m.sos_fused)
                };
                println!("{}", digest(&y));
            }
            "voice" => {
                // voice <name> <dur> <seed> <umode> <modes> — atmosphere
                // voices; dur is ignored by the fixed-length ones.
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let name = rest.next().unwrap_or("");
                let dur: f64 = rest.next().and_then(|v| v.parse().ok()).unwrap_or(20.0);
                let sd: u128 = rest.next().and_then(|v| v.parse().ok()).unwrap_or(0);
                let fused = rest.next() == Some("fma");
                let m = filters::Modes::parse(rest.next().unwrap_or(""));
                let mut d = atmos::Dice::new(sd, fused);
                let (buf, marks): (Vec<f64>, Vec<(f64, f64)>) = match name {
                    "wind" => (atmos::wind(dur, &mut d, &m), Vec::new()),
                    "thunder" => (atmos::thunder(&mut d, &m), Vec::new()),
                    "creak" => (atmos::creak(&mut d, &m), Vec::new()),
                    "shriek" => (atmos::shriek(&mut d, &m), Vec::new()),
                    "heartbeat" => atmos::heartbeat(dur, &mut d, &m),
                    "whispers" => atmos::whispers(dur, &mut d, &m),
                    _ => {
                        println!("ERR unknown voice {name}");
                        continue;
                    }
                };
                let ms: Vec<String> = marks.iter().map(|(t, v)| format!("{t:?}:{v:?}")).collect();
                println!("{} | {}", digest(&buf), ms.join(","));
            }
            "master" => {
                // master <kind> <seed> <n> <umode> — limit|loop|fade|norm|wav
                // applied to a seeded noise buffer (hot for the limiter).
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let kind = rest.next().unwrap_or("");
                let sd: u128 = rest.next().and_then(|v| v.parse().ok()).unwrap_or(0);
                let n: usize = rest.next().and_then(|v| v.parse().ok()).unwrap_or(0);
                let fused = rest.next() == Some("fma");
                let mut g = Pcg64::new(sd);
                let amp = if kind == "limit" { 1.6 } else { 0.8 };
                let mut buf: Vec<f64> = (0..n)
                    .map(|_| {
                        if fused {
                            g.uniform_fma(-amp, amp)
                        } else {
                            g.uniform_plain(-amp, amp)
                        }
                    })
                    .collect();
                match kind {
                    "limit" => buf = master::limit(&buf, 0.89),
                    "loop" => master::loop_crossfade(&mut buf),
                    "fade" => master::end_fade(&mut buf),
                    "norm" => master::normalize(&mut buf, 0.89),
                    "wav" => {
                        let pcm = master::quantize16(&buf);
                        let mut bytes = Vec::with_capacity(pcm.len() * 2);
                        for v in &pcm {
                            bytes.extend_from_slice(&v.to_le_bytes());
                        }
                        println!("{:08x} {}", castle_core::bridge::crc32(&bytes), pcm.len());
                        continue;
                    }
                    _ => {
                        println!("ERR unknown master kind {kind}");
                        continue;
                    }
                }
                println!("{}", digest(&buf));
            }
            "scene" => {
                // scene <id> <duration_ms> <loop01> <umode> <modes> <ev;ev…>
                // ev = name:t:gain:dur|-:take|-  (reverb-0 scenes only)
                let _ = seed;
                let mut rest = line.split_whitespace().skip(1);
                let id = rest.next().unwrap_or("");
                let dur_ms: f64 = rest.next().and_then(|v| v.parse().ok()).unwrap_or(0.0);
                let looped = rest.next() == Some("1");
                let fused = rest.next() == Some("fma");
                let m = filters::Modes::parse(rest.next().unwrap_or(""));
                let evs: Vec<scene::Ev> = rest
                    .next()
                    .unwrap_or("")
                    .split(';')
                    .filter(|e| !e.is_empty())
                    .filter_map(|e| {
                        let f: Vec<&str> = e.split(':').collect();
                        let opt = |s: &&str| -> Option<f64> {
                            if **s == *"-" {
                                None
                            } else {
                                s.parse().ok()
                            }
                        };
                        Some(scene::Ev {
                            synth: (*f.first()?).to_string(),
                            t: f.get(1)?.parse().ok()?,
                            gain: f.get(2)?.parse().ok()?,
                            dur: f.get(3).and_then(opt),
                            take: f.get(4).and_then(opt),
                        })
                    })
                    .collect();
                match scene::render_scene(id, dur_ms, &evs, looped, fused, &m) {
                    None => println!("ERR unknown synth in scene"),
                    Some((buf, marks)) => {
                        let ms: Vec<String> = marks
                            .iter()
                            .map(|(n, v)| {
                                let pts: Vec<String> =
                                    v.iter().map(|(t, vel)| format!("{t}:{vel:?}")).collect();
                                format!("{n}>{}", pts.join(","))
                            })
                            .collect();
                        println!("{} | {}", digest(&buf), ms.join(";"));
                    }
                }
            }
            other => println!("ERR unknown op {other}"),
        }
    }
}
