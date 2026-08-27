//! Per-stream pulse dynamics — B2 of the typesafe plan.
//!
//! Port of `tools/pulse_dynamics.py`, which is itself twinned with
//! `web/src/track_lights.ts`. Everything here is f64 with the SAME
//! operation order as the Python, so the parity gate
//! (`tests/test_pulse_rust.py`) can demand digit-for-digit equality on a
//! seeded corpus rather than tolerances. The YAML-walking glue
//! (`section_gates` scene extraction) stays in Python — this is the
//! arithmetic, not the plumbing.

/// #3: the stream's own pace, from the median gap between its hits.
/// Below 8 hits there is no tempo evidence and the factor stays neutral.
pub fn tempo_factor(times_s: &[f64]) -> f64 {
    if times_s.len() < 8 {
        return 1.0;
    }
    let mut gaps: Vec<f64> = times_s.windows(2).map(|w| w[1] - w[0]).collect();
    gaps.sort_by(f64::total_cmp);
    let m = gaps.len() / 2;
    let g = if gaps.len() % 2 == 1 {
        gaps[m]
    } else {
        (gaps[m - 1] + gaps[m]) / 2.0
    };
    (g / 0.45).clamp(0.7, 1.6)
}

/// Stretch or shrink the tail: factor scales time-to-dark, not the decay
/// number itself. floor(+0.5), not round-half-even — the parity contract
/// compares digits with JS's Math.round.
pub fn tempo_decay(decay: f64, factor: f64) -> f64 {
    ((1.0 - (1.0 - decay) / factor) * 10000.0 + 0.5).floor() / 10000.0
}

/// floor(x*1000+0.5)/1000 — the exact arithmetic of JS's
/// Math.round(x*1000)/1000.
pub fn round3(x: f64) -> f64 {
    (x * 1000.0 + 0.5).floor() / 1000.0
}

/// #8: louder than its own recent neighbourhood, not just loud.
pub fn is_accent(vels: &[f64], i: usize) -> bool {
    let w = &vels[i.saturating_sub(8)..i];
    w.len() >= 3 && vels[i] >= w.iter().sum::<f64>() / w.len() as f64 + 0.25 && vels[i] >= 0.55
}

/// A pan this far off-centre overrides round-robin movement (#7).
pub const PAN_DECISIVE: f64 = 0.10;

/// #9: what a hit's section does to it — None drops it, else a multiplier.
/// Twin of gateMul in track_lights.ts / gate_mul in pulse_dynamics.py.
pub fn gate_mul(synth: &str, gates: &[(i64, String)], t_ms: i64) -> Option<f64> {
    match gate_note(gates, t_ms) {
        Some("silence") => None,
        Some("hush") if synth == "onset_high" => None,
        Some("hush") if synth == "onset_mid" => Some(0.5),
        _ => Some(1.0),
    }
}

/// The section a moment lives in, or None before any boundary.
pub fn gate_note(gates: &[(i64, String)], t_ms: i64) -> Option<&str> {
    let mut note = None;
    for (gt, n) in gates {
        if *gt <= t_ms {
            note = Some(n.as_str());
        } else {
            break;
        }
    }
    note
}

/// #1 Palette drift: one full lap of the band's triad per period.
pub const DRIFT_PERIOD_S: f64 = 60.0;

/// Twin of drift_base / driftBase: plain lerp between neighbouring cycle
/// colours, every channel through round3.
pub fn drift_base(colors: &[Vec<f64>], i: usize, t_ms: i64) -> Vec<f64> {
    let n = colors.len();
    if n < 2 {
        return colors[0].clone();
    }
    let p = (t_ms as f64 / 1000.0 % DRIFT_PERIOD_S) / DRIFT_PERIOD_S;
    let pos = (i as f64 + p * n as f64) % n as f64;
    let k = pos as usize;
    let f = pos - k as f64;
    let a = &colors[k];
    let b = &colors[(k + 1) % n];
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| round3(x + (y - x) * f))
        .collect()
}

/// The dram0 diet: each scene keeps its strongest `cap` pulse cues, in time
/// order. Cues arrive as (intensity, t); the return is the kept ORIGINAL
/// indices, so the caller (and the parity test) can check identity, not
/// just values. Ranking and tie-breaks mirror thin_pulses exactly:
/// strongest first, then earlier, then original order — then re-sorted by
/// (t, original order).
pub fn thin_pulses_idx(cues: &[(f64, f64)], cap: usize) -> Vec<usize> {
    if cues.len() <= cap {
        return (0..cues.len()).collect();
    }
    let mut ranked: Vec<usize> = (0..cues.len()).collect();
    ranked.sort_by(|&x, &y| {
        (-cues[x].0)
            .total_cmp(&-cues[y].0)
            .then(cues[x].1.total_cmp(&cues[y].1))
            .then(x.cmp(&y))
    });
    let mut keep = ranked[..cap].to_vec();
    keep.sort_by(|&x, &y| cues[x].1.total_cmp(&cues[y].1).then(x.cmp(&y)));
    keep
}
