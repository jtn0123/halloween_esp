//! The note-level synth voices of tools/synth.py, sample for sample — B3.
//!
//! Each function follows the numpy expression's own association order and
//! rounding, pinned first by a pure-Python float-loop replica in the B3
//! spike (2026-08-27) and held by tests/test_synth_rust.py: every sample
//! bit-equal, no tolerances. Two findings that shape the code:
//!   - np.interp's inner step is a FUSED slope*(x-xp)+fp on the wheels we
//!     run (mul_add here, matching);
//!   - np.linspace(0,1,n) is i*delta with the endpoint forced, not
//!     accumulated.

use std::f64::consts::PI;

pub const SR: usize = 44100;
const SR_F: f64 = 44100.0;

/// Organ voicing — the RANKS table from synth.py / the previewer.
pub const RANKS: [(f64, f64, bool); 8] = [
    (0.25, 0.30, false),
    (0.5, 0.86, false),
    (1.0, 1.00, false),
    (1.004, 0.40, false),
    (2.0, 0.40, true),
    (3.0, 0.22, true),
    (4.0, 0.13, true),
    (6.0, 0.06, true),
];

pub const STOPS: f64 = 0.42;
pub const TREM_HZ: f64 = 5.1;
pub const TREM_DEPTH: f64 = 0.046;

/// Semitones relative to A3 (220 Hz).
pub fn nt(semitones: f64) -> f64 {
    220.0 * 2f64.powf(semitones / 12.0)
}

/// np.interp for sorted xp — with the fused inner step numpy compiles to.
pub fn interp(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    let last = xp.len() - 1;
    if x <= xp[0] {
        return fp[0];
    }
    if x >= xp[last] {
        return fp[last];
    }
    for j in 0..last {
        if x < xp[j + 1] {
            let slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
            return slope.mul_add(x - xp[j], fp[j]);
        }
    }
    fp[last]
}

/// np.linspace(0, 1, n)[i]: i*delta, endpoint forced to exactly 1.
fn ramp01(i: usize, n: usize) -> f64 {
    if n < 2 || i == n - 1 {
        return if i == 0 && n > 1 { 0.0 } else { 1.0 };
    }
    i as f64 * (1.0 / (n - 1) as f64)
}

/// Additive drawbar organ rank with tremulant and a duration-scaled swell.
pub fn pipe(f: f64, dur: f64, vel: f64, stops: f64) -> Vec<f64> {
    let n = (dur * SR_F) as usize;
    let mut out = vec![0.0; n];
    for &(m, amp, upper) in &RANKS {
        let fr = f * m * if m == 1.0 { 1.0 } else { 1.0012 };
        if !(24.0..=11000.0).contains(&fr) {
            continue;
        }
        let a = amp * if upper { stops } else { 1.0 };
        if a < 0.012 {
            continue;
        }
        let w = 2.0 * PI * fr;
        for (i, o) in out.iter_mut().enumerate() {
            *o += a * (w * (i as f64 / SR_F)).sin();
        }
    }
    let atk = 1.7f64.min(0.07f64.max(dur * 0.20));
    let rel = 2.6f64.min(0.35f64.max(dur * 0.34));
    let hold = (atk + 0.05).max(dur - rel);
    let xp = [0.0, atk, hold, dur];
    let fp = [0.0, 1.0, 1.0, 0.0];
    for (i, o) in out.iter_mut().enumerate() {
        let t = i as f64 / SR_F;
        let env = interp(t, &xp, &fp);
        let trem = 1.0 + TREM_DEPTH * (2.0 * PI * TREM_HZ * t).sin();
        *o = *o * env * trem * vel;
    }
    out
}

/// Additive piano: higher partials decay faster, slight inharmonicity.
pub fn piano(f: f64, dur: f64, vel: f64) -> Vec<f64> {
    let n = (dur * SR_F) as usize;
    let mut out = vec![0.0; n];
    for h in 1..9 {
        let hf = f64::from(h);
        let fr = f * hf * (1.0 + 0.0004 * (hf * hf));
        if fr > 12000.0 {
            break;
        }
        let a = 1.0 / hf.powf(1.6);
        let k = 2.2 + 0.9 * hf;
        let w = 2.0 * PI * fr;
        for (i, o) in out.iter_mut().enumerate() {
            let t = i as f64 / SR_F;
            *o += a * (w * t).sin() * (-t * k).exp();
        }
    }
    let atk = 1.max((0.006 * SR_F) as usize);
    for (i, o) in out.iter_mut().enumerate().take(atk.min(n)) {
        *o *= ramp01(i, atk);
    }
    out.iter().map(|x| x * vel).collect()
}

/// Music box / celesta: inharmonic partials, long ring.
pub fn music_box(f: f64, dur: f64, vel: f64) -> Vec<f64> {
    let n = (dur * SR_F) as usize;
    let mut out = vec![0.0; n];
    for (idx, m) in [1.0, 3.01, 5.42].into_iter().enumerate() {
        if f * m > 14000.0 {
            continue;
        }
        let i2 = idx as f64;
        let decay = 3.2 / (dur / (i2 * 0.8 + 1.0));
        let a = 1.0 / (i2 * 2.2 + 1.0);
        let w = 2.0 * PI * f * m;
        for (i, o) in out.iter_mut().enumerate() {
            let t = i as f64 / SR_F;
            *o += a * (w * t).sin() * (-t * decay).exp();
        }
    }
    let atk = 1.max((0.004 * SR_F) as usize);
    for (i, o) in out.iter_mut().enumerate().take(atk.min(n)) {
        *o *= ramp01(i, atk);
    }
    out.iter().map(|x| x * vel).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nt_is_equal_temperament_from_a3() {
        assert_eq!(nt(0.0), 220.0);
        assert_eq!(nt(12.0), 440.0);
    }

    #[test]
    fn interp_holds_ends_and_hits_knees() {
        let xp = [0.0, 1.0, 3.0, 4.0];
        let fp = [0.0, 1.0, 1.0, 0.0];
        assert_eq!(interp(-0.5, &xp, &fp), 0.0);
        assert_eq!(interp(0.5, &xp, &fp), 0.5);
        assert_eq!(interp(2.0, &xp, &fp), 1.0);
        assert_eq!(interp(9.0, &xp, &fp), 0.0);
    }

    #[test]
    fn voices_have_the_expected_shape() {
        let p = pipe(nt(-19.0), 1.5, 0.078, STOPS);
        assert_eq!(p.len(), (1.5 * SR_F) as usize);
        assert_eq!(p[0], 0.0); // the swell starts from silence
        assert!(p.iter().any(|&x| x.abs() > 1e-3));
        assert_eq!(ramp01(0, 264), 0.0);
        assert_eq!(ramp01(263, 264), 1.0);
    }
}
