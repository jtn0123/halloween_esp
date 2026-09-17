//! Expand pulse streams into strike cues — B2 pass 2.
//!
//! Port of `pulse_cues` in `tools/pulse_expand.py` (whose twin is
//! bandStrikes in `web/src/track_lights.ts`), minus the plumbing: the
//! Python walks the scene YAML and the markers file; this takes the
//! streams and section gates it found and does the arithmetic. Every
//! default that lives in a `.get(key, default)` there is a field default
//! here, so the parity corpus pins those numbers as well.

use crate::pulse::{
    PAN_DECISIVE, drift_base, gate_mul, gate_note, is_accent, round3, tempo_decay, tempo_factor,
};

pub const WHITE: [f64; 4] = [1.0, 1.0, 1.0, 1.0];
pub const DEFAULT_DECAY: f64 = 0.90;

/// #2 Chorus takeover family — same table as pulse_dynamics.py.
pub const TAKEOVER_COLORS: [[f64; 4]; 3] = [
    [1.0, 0.55, 0.0, 0.0],  // gold
    [1.0, 0.25, 0.0, 0.05], // flame orange
    [1.0, 0.0, 0.35, 0.0],  // hot rose
];
pub const TAKEOVER_HOT: [f64; 4] = [1.0, 0.75, 0.1, 0.12];

/// One marker hit: time (ms), velocity, optional pan.
#[derive(Clone, Debug)]
pub struct Hit {
    pub t: i64,
    pub vel: f64,
    pub pan: Option<f64>,
}

/// One `pulse:` stream config, defaults matching the Python `.get`s.
#[derive(Clone, Debug, Default)]
pub struct PulseCfg {
    pub synth: String,
    pub zones: Vec<String>, // empty -> all zones (targets None)
    pub alternate: bool,
    pub boost_targets: Vec<String>,
    pub boost_at: Option<f64>, // .get("boost_at", 2)
    pub color: Option<Vec<f64>>,
    pub color_hot: Option<Vec<f64>>,
    pub colors: Vec<Vec<f64>>, // empty -> no cycle
    pub takeover: bool,
    pub drift: bool,
    pub pixels: Option<String>, // .get("pixels", "all")
    pub pixels_by_vel: bool,
    pub intensity: Option<f64>, // .get("intensity", 0.3)
    pub decay: Option<f64>,     // .get("decay", DEFAULT_DECAY)
    pub ms: Option<i64>,        // .get("ms", 120)
    pub attack_ms: i64,
}

/// One expanded strike cue. `targets` None means "all zones".
#[derive(Clone, Debug)]
pub struct Cue {
    pub t: i64,
    pub targets: Option<Vec<String>>,
    pub ms: i64,
    pub intensity: f64,
    pub color: Vec<f64>,
    pub decay: f64,
    pub attack: i64,
    pub pixels: String,
    pub note: String,
}

/// color -> color_hot by velocity. Identical in track_lights.ts.
pub fn blend_color(base: &[f64], hot: Option<&[f64]>, vel: f64) -> Vec<f64> {
    match hot {
        None => base.to_vec(),
        Some(h) => base
            .iter()
            .zip(h.iter())
            .map(|(b, h)| round3(b + (h - b) * vel))
            .collect(),
    }
}

/// Velocity picks the strike mask: soft centre, medium scatter, hard all.
pub fn pixels_for(cfg: &PulseCfg, vel: f64) -> String {
    if !cfg.pixels_by_vel {
        return cfg.pixels.clone().unwrap_or_else(|| "all".to_string());
    }
    if vel < 0.40 {
        "center"
    } else if vel < 0.72 {
        "scatter"
    } else {
        "all"
    }
    .to_string()
}

/// The body of pulse_cues for streams the caller already resolved:
/// (cfg, that synth's beats), plus the scene's section gates.
pub fn pulse_cues(streams: &[(PulseCfg, Vec<Hit>)], gates: &[(i64, String)]) -> Vec<Cue> {
    let mut out = Vec::new();
    for (cfg, beats) in streams {
        let zones = &cfg.zones;
        let factor = tempo_factor(
            &beats
                .iter()
                .map(|b| b.t as f64 / 1000.0)
                .collect::<Vec<_>>(),
        );
        let decay = tempo_decay(cfg.decay.unwrap_or(DEFAULT_DECAY), factor);
        let ms = (cfg.ms.unwrap_or(120) as f64 * factor + 0.5).floor() as i64;
        let vels: Vec<f64> = beats.iter().map(|b| b.vel).collect();
        for (i, beat) in beats.iter().enumerate() {
            let (t, vel, pan) = (beat.t, beat.vel, beat.pan);
            let Some(mul) = gate_mul(&cfg.synth, gates, t) else {
                continue; // gated out by its section (#9)
            };
            let mut targets: Option<Vec<String>> = if !zones.is_empty() && cfg.alternate {
                // A decisively panned hit goes to ITS tower (#7).
                let both =
                    zones.iter().any(|z| z == "towerL") && zones.iter().any(|z| z == "towerR");
                match pan {
                    Some(p) if p.abs() >= PAN_DECISIVE && both => {
                        Some(vec![if p < 0.0 { "towerL" } else { "towerR" }.to_string()])
                    }
                    _ => Some(vec![zones[i % zones.len()].clone()]),
                }
            } else if zones.is_empty() {
                None // all zones
            } else {
                Some(zones.clone())
            };
            if let Some(tg) = &mut targets {
                if !cfg.boost_targets.is_empty()
                    && (vel >= cfg.boost_at.unwrap_or(2.0) || is_accent(&vels, i))
                {
                    for z in &cfg.boost_targets {
                        if !tg.contains(z) {
                            tg.push(z.clone());
                        }
                    }
                }
            }
            let mut hot = cfg.color_hot.as_deref();
            let base: Vec<f64> = if cfg.takeover && gate_note(gates, t) == Some("chorus") {
                hot = Some(&TAKEOVER_HOT);
                TAKEOVER_COLORS[i % TAKEOVER_COLORS.len()].to_vec()
            } else if !cfg.colors.is_empty() && cfg.drift {
                drift_base(&cfg.colors, i, t)
            } else if !cfg.colors.is_empty() {
                cfg.colors[i % cfg.colors.len()].clone()
            } else {
                cfg.color.clone().unwrap_or_else(|| WHITE.to_vec())
            };
            out.push(Cue {
                t,
                targets,
                ms,
                intensity: round3(cfg.intensity.unwrap_or(0.3) * vel * mul),
                color: blend_color(&base, hot, vel),
                decay,
                attack: cfg.attack_ms,
                pixels: pixels_for(cfg, vel),
                note: cfg.synth.clone(),
            });
        }
    }
    out
}
