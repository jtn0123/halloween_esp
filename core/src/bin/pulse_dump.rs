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
use castle_core::pulse_expand::{pulse_cues, Hit, PulseCfg};
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
            "pc" => pc_line(&mut it),
            other => panic!("unknown op {other:?}"),
        };
        println!("{out}");
    }
}

// ── The pc op (appended with B2 pass 2) ─────────────────────────────────
// Kept in this file's dispatch via the shim below: pulse_dump's main match
// calls `pc_line` for op "pc". Encoding, one stream per line:
//     pc <gates|-> synth=NAME[;key=value…] <beats>
// keys: zones=a+b, boost_targets=a+b, alternate/takeover/drift/pbv=1,
// boost_at/intensity/decay=F, ms/attack=I, pixels=S,
// color/hot=f,f,f,f, colors=f,f,f,f|f,f,f,f
// beats: t:vel[:pan],…   Output: all cues on ONE line, cue fields joined
// by unit separator (0x1f), cues by record separator (0x1e); empty = none.

fn pc_line(it: &mut dyn Iterator<Item = &str>) -> String {
    let g = gates(it.next().expect("gates"));
    let mut cfg = PulseCfg::default();
    for kv in it.next().expect("cfg").split(';') {
        let (k, v) = kv.split_once('=').expect("bad cfg kv");
        let strs = |v: &str| v.split('+').map(str::to_string).collect::<Vec<_>>();
        let color = |v: &str| {
            v.split(',')
                .map(|x| x.parse().unwrap())
                .collect::<Vec<f64>>()
        };
        match k {
            "synth" => cfg.synth = v.to_string(),
            "zones" => cfg.zones = strs(v),
            "boost_targets" => cfg.boost_targets = strs(v),
            "alternate" => cfg.alternate = v == "1",
            "takeover" => cfg.takeover = v == "1",
            "drift" => cfg.drift = v == "1",
            "pbv" => cfg.pixels_by_vel = v == "1",
            "boost_at" => cfg.boost_at = Some(v.parse().unwrap()),
            "intensity" => cfg.intensity = Some(v.parse().unwrap()),
            "decay" => cfg.decay = Some(v.parse().unwrap()),
            "ms" => cfg.ms = Some(v.parse().unwrap()),
            "attack" => cfg.attack_ms = v.parse().unwrap(),
            "pixels" => cfg.pixels = Some(v.to_string()),
            "color" => cfg.color = Some(color(v)),
            "hot" => cfg.color_hot = Some(color(v)),
            "colors" => cfg.colors = v.split('|').map(color).collect(),
            other => panic!("unknown cfg key {other:?}"),
        }
    }
    let beats: Vec<Hit> = match it.next() {
        None | Some("-") => Vec::new(),
        Some(b) => b
            .split(',')
            .map(|h| {
                let parts: Vec<&str> = h.split(':').collect();
                Hit {
                    t: parts[0].parse().expect("bad t"),
                    vel: parts[1].parse().expect("bad vel"),
                    pan: parts.get(2).map(|p| p.parse().expect("bad pan")),
                }
            })
            .collect(),
    };
    let fj = |c: &[f64]| {
        c.iter()
            .map(|v| format!("{v:?}"))
            .collect::<Vec<_>>()
            .join(",")
    };
    pulse_cues(&[(cfg, beats)], &g)
        .iter()
        .map(|c| {
            format!(
                "{}\x1f{}\x1f{}\x1f{:?}\x1f{}\x1f{:?}\x1f{}\x1f{}\x1f{}",
                c.t,
                c.targets.as_ref().map_or("-".to_string(), |t| t.join("+")),
                c.ms,
                c.intensity,
                fj(&c.color),
                c.decay,
                c.attack,
                c.pixels,
                c.note,
            )
        })
        .collect::<Vec<_>>()
        .join("\x1e")
}
