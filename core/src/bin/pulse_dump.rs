//! Line protocol over stdin for the pulse-dynamics parity gate.
//!
//! tests/test_pulse_rust.py writes one op per line and compares every
//! answer digit-for-digit with tools/pulse_dynamics.py. Formats:
//!
//!     tf t1;t2;…             -> tempo factor
//!     td decay factor        -> stretched decay
//!     r3 x                   -> round3
//!     acc v1;v2;… i          -> true/false
//!     gm synth t g1:n1,…|-   -> gate multiplier or None
//!     gn t gates|-           -> section note or None
//!     db c;c;c;c|… i t       -> drifted colour, comma-joined
//!     thin cap i1:t1|…       -> kept original indices, comma-joined

use castle_core::pulse::{
    drift_base, gate_mul, gate_note, is_accent, round3, tempo_decay, tempo_factor, thin_pulses_idx,
};
use std::io::BufRead;

fn floats(s: &str) -> Vec<f64> {
    if s.is_empty() {
        Vec::new()
    } else {
        s.split(';')
            .map(|v| v.parse().expect("bad float"))
            .collect()
    }
}

fn gates(s: &str) -> Vec<(i64, String)> {
    if s == "-" {
        return Vec::new();
    }
    s.split(',')
        .map(|g| {
            let (t, n) = g.split_once(':').expect("bad gate");
            (t.parse().expect("bad gate t"), n.to_string())
        })
        .collect()
}

fn main() {
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let line = line.expect("stdin");
        let mut it = line.split_whitespace();
        let op = it.next().expect("op");
        let arg = |it: &mut dyn Iterator<Item = &str>| it.next().expect("arg").to_string();
        let out = match op {
            // An empty hit list is a real case (`tf ` with nothing after it).
            "tf" => format!("{:?}", tempo_factor(&floats(it.next().unwrap_or("")))),
            "td" => {
                let d: f64 = arg(&mut it).parse().unwrap();
                let f: f64 = arg(&mut it).parse().unwrap();
                format!("{:?}", tempo_decay(d, f))
            }
            "r3" => format!("{:?}", round3(arg(&mut it).parse().unwrap())),
            "acc" => {
                let v = floats(&arg(&mut it));
                let i: usize = arg(&mut it).parse().unwrap();
                format!("{}", is_accent(&v, i))
            }
            "gm" => {
                let synth = arg(&mut it);
                let t: i64 = arg(&mut it).parse().unwrap();
                let g = gates(&arg(&mut it));
                match gate_mul(&synth, &g, t) {
                    None => "None".to_string(),
                    Some(v) => format!("{v:?}"),
                }
            }
            "gn" => {
                let t: i64 = arg(&mut it).parse().unwrap();
                let g = gates(&arg(&mut it));
                gate_note(&g, t).unwrap_or("None").to_string()
            }
            "db" => {
                let colors: Vec<Vec<f64>> = arg(&mut it).split('|').map(floats).collect();
                let i: usize = arg(&mut it).parse().unwrap();
                let t: i64 = arg(&mut it).parse().unwrap();
                let c = drift_base(&colors, i, t);
                c.iter()
                    .map(|v| format!("{v:?}"))
                    .collect::<Vec<_>>()
                    .join(",")
            }
            "thin" => {
                let cap: usize = arg(&mut it).parse().unwrap();
                let cues: Vec<(f64, f64)> = arg(&mut it)
                    .split('|')
                    .map(|c| {
                        let (i, t) = c.split_once(':').expect("bad cue");
                        (i.parse().unwrap(), t.parse().unwrap())
                    })
                    .collect();
                thin_pulses_idx(&cues, cap)
                    .iter()
                    .map(usize::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            }
            other => panic!("unknown op {other:?}"),
        };
        println!("{out}");
    }
}
