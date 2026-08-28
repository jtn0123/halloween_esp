//! analyze.py's banded onset detection, hit for hit — B3.
//!
//! The Python side left scipy's kernels first (same commit family as the
//! limiter and the hall): the STFT runs on the defined-order FFT, the
//! smoothing is an explicit FIR, the median filter an explicit middle
//! element. What remains numpy-specific is REDUCTIONS, all pinned by the
//! B3 spikes (2026-08-27): np.sum is pairwise (8 accumulators per 128,
//! recursive halving above), np.std rides that, .sum(axis=0) adds rows
//! sequentially, and |complex| is the scaled hypot ax*sqrt(fma(r,r,1)).
//! tests/test_onsets_rust.py holds whole band dictionaries to the Python.

use crate::fft::fft;
use crate::jsonio::Json;
use crate::scene::round3;

pub const WIN: usize = 2048;
pub const HOP: usize = 512;
const SR_F: f64 = 44100.0;

/// (name, low_hz, high_hz, min_gap_s) — analyze.py's BANDS.
pub const BANDS: [(&str, f64, f64, f64); 3] = [
    ("onset_low", 20.0, 200.0, 0.16),
    ("onset_mid", 200.0, 2000.0, 0.11),
    ("onset_high", 2000.0, 16000.0, 0.09),
];

/// band_sensitivity's JSON coercion, in BANDS order: a scalar means all
/// three; a map answers by full band name, then short, then 1.1. Shared
/// by the scene_render and analyze_track bins.
pub fn sens3(v: Option<&Json>) -> [f64; 3] {
    match v {
        Some(m @ Json::Obj(_)) => {
            let mut out = [1.1; 3];
            for (i, short) in ["low", "mid", "high"].iter().enumerate() {
                let hit = m
                    .get(&format!("onset_{short}"))
                    .or_else(|| m.get(short))
                    .and_then(|j| j.as_f64());
                if let Some(f) = hit {
                    out[i] = f;
                }
            }
            out
        }
        Some(j) => [j.as_f64().unwrap_or(1.1); 3],
        None => [1.1; 3],
    }
}

/// numpy's pairwise summation: sequential under 8, eight accumulators up
/// to 128, recursive halving (on a multiple-of-8 boundary) above.
pub(crate) fn pairwise_sum(v: &[f64]) -> f64 {
    let n = v.len();
    if n < 8 {
        let mut s = 0.0;
        for x in v {
            s += x;
        }
        return s;
    }
    if n <= 128 {
        let mut r = [0.0f64; 8];
        let mut i = 0;
        while i + 8 <= n {
            for (j, slot) in r.iter_mut().enumerate() {
                *slot += v[i + j];
            }
            i += 8;
        }
        let mut s = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            s += v[i];
            i += 1;
        }
        return s;
    }
    let mut half = n / 2;
    half -= half % 8;
    pairwise_sum(&v[..half]) + pairwise_sum(&v[half..])
}

/// np.std: pairwise mean, squared deviations, pairwise again, sqrt.
fn np_std(v: &[f64]) -> f64 {
    let n = v.len() as f64;
    let mean = pairwise_sum(v) / n;
    let sq: Vec<f64> = v
        .iter()
        .map(|x| {
            let d = x - mean;
            d * d
        })
        .collect();
    (pairwise_sum(&sq) / n).sqrt()
}

/// np.abs of a complex: numpy's own scaled hypot, fused inner term.
fn cabs(re: f64, im: f64) -> f64 {
    let (mut ax, mut ay) = (re.abs(), im.abs());
    if ax < ay {
        std::mem::swap(&mut ax, &mut ay);
    }
    if ax == 0.0 {
        return 0.0;
    }
    let r = ay / ax;
    ax * r.mul_add(r, 1.0).sqrt()
}

/// np.hanning(k) normalised to unit sum — the smoothing kernels.
fn hann_kernel(k: usize) -> Vec<f64> {
    let w: Vec<f64> = (0..k)
        .map(|i| 0.5 - 0.5 * ((2.0 * std::f64::consts::PI * i as f64) / (k - 1) as f64).cos())
        .collect();
    let s = pairwise_sum(&w);
    w.iter().map(|v| v / s).collect()
}

/// analyze._fir_same: "same"-mode FIR in the Python's own accumulation
/// order (last tap first, then downward).
fn fir_same(env: &[f64], kernel: &[f64]) -> Vec<f64> {
    let k = kernel.len();
    let pad = (k - 1) / 2;
    let n = env.len();
    let mut padded = vec![0.0; pad];
    padded.extend_from_slice(env);
    padded.extend(vec![0.0; k - 1 - pad]);
    let mut out: Vec<f64> = (0..n).map(|i| kernel[k - 1] * padded[i]).collect();
    for j in (0..k - 1).rev() {
        for (i, o) in out.iter_mut().enumerate() {
            *o += kernel[j] * padded[k - 1 - j + i];
        }
    }
    out
}

/// analyze._medfilt: the middle element of each zero-padded window.
fn medfilt(e: &[f64], k: usize) -> Vec<f64> {
    let pad = k / 2;
    let mut padded = vec![0.0; pad];
    padded.extend_from_slice(e);
    padded.extend(vec![0.0; pad]);
    (0..e.len())
        .map(|i| {
            let mut w = padded[i..i + k].to_vec();
            w.sort_by(f64::total_cmp);
            w[pad]
        })
        .collect()
}

/// analyze._stft: RAW magnitudes per frame, the frequency grid, and the
/// shifted time grid. analyze log-compresses; envelope wants them plain.
fn stft_mag(x: &[f64]) -> (Vec<Vec<f64>>, Vec<f64>, Vec<f64>) {
    let win: Vec<f64> = (0..WIN)
        .map(|i| 0.5 - 0.5 * ((2.0 * std::f64::consts::PI * i as f64) / WIN as f64).cos())
        .collect();
    let scale = 1.0 / pairwise_sum(&win);
    let pad = WIN / 2;
    let mut xx = vec![0.0; pad];
    xx.extend_from_slice(x);
    xx.extend(vec![0.0; pad]);
    let nseg = (xx.len() - WIN).div_ceil(HOP) + 1;
    xx.resize((nseg - 1) * HOP + WIN, 0.0);
    let mut mag = Vec::with_capacity(nseg);
    for s in 0..nseg {
        let mut re: Vec<f64> = (0..WIN).map(|i| xx[s * HOP + i] * win[i]).collect();
        let mut im = vec![0.0; WIN];
        fft(&mut re, &mut im, false);
        mag.push(
            (0..=WIN / 2)
                .map(|b| cabs(re[b] * scale, im[b] * scale))
                .collect(),
        );
    }
    let freqs: Vec<f64> = (0..=WIN / 2)
        .map(|b| b as f64 * (SR_F / WIN as f64))
        .collect();
    let shift = (WIN / 2) as f64 / SR_F;
    let times: Vec<f64> = (0..nseg)
        .map(|i| ((i * HOP + WIN / 2) as f64) / SR_F - shift)
        .collect();
    (mag, freqs, times)
}

/// analyze._pick_peaks: adaptive-threshold local maxima with min_gap.
fn pick_peaks(env: &[f64], times: &[f64], min_gap: f64, sensitivity: f64) -> Vec<(f64, f64)> {
    let mx = env.iter().fold(0.0f64, |a, b| a.max(*b));
    if mx <= 0.0 {
        return Vec::new();
    }
    let e: Vec<f64> = env.iter().map(|v| v / mx).collect();
    let k = 3usize.max((((0.5 / (HOP as f64 / SR_F)) as i64) | 1) as usize);
    let local = medfilt(&e, k);
    let margin = sensitivity * (np_std(&e) + 1e-9);
    let mut hits: Vec<(f64, f64)> = Vec::new();
    let mut last = -1e9;
    for i in 1..e.len().saturating_sub(1) {
        if e[i] < local[i] + margin {
            continue;
        }
        if !(e[i] >= e[i - 1] && e[i] >= e[i + 1]) {
            continue;
        }
        let t = times[i];
        if t - last < min_gap {
            if let Some(top) = hits.last_mut() {
                if e[i] > top.1 {
                    *top = (t, e[i]);
                    last = t;
                }
            }
            continue;
        }
        hits.push((t.max(0.0), e[i]));
        last = t;
    }
    if hits.is_empty() {
        return hits;
    }
    let peak = hits.iter().fold(f64::NEG_INFINITY, |a, h| a.max(h.1));
    hits.iter()
        .map(|(t, v)| (*t, round3((v / peak).min(1.0))))
        .collect()
}

/// analyze.analyze with a scalar sensitivity: {band: [(s, vel)]}.
pub fn analyze(x: &[f64], sensitivity: f64) -> Vec<(String, Vec<(f64, f64)>)> {
    analyze3(x, [sensitivity; 3])
}

/// The per-band form — band_sensitivity's dict, in BANDS order.
pub fn analyze3(x: &[f64], sens: [f64; 3]) -> Vec<(String, Vec<(f64, f64)>)> {
    if x.len() < WIN * 2 {
        return Vec::new();
    }
    let mut xx = vec![0.0; WIN]; // the front pad that saves a t=0 downbeat
    xx.extend_from_slice(x);
    let (raw, freqs, t0) = stft_mag(&xx);
    let mag: Vec<Vec<f64>> = raw
        .iter()
        .map(|row| row.iter().map(|m| (m * 100.0).ln_1p()).collect())
        .collect();
    let shift = WIN as f64 / SR_F;
    let times: Vec<f64> = t0.iter().map(|t| t - shift).collect();
    let mut out = Vec::new();
    for (bi, (name, lo, hi, gap)) in BANDS.into_iter().enumerate() {
        let sel: Vec<usize> = (0..freqs.len())
            .filter(|&b| freqs[b] >= lo && freqs[b] < hi)
            .collect();
        if sel.is_empty() {
            continue;
        }
        // positive spectral flux, rows (bins) accumulated sequentially —
        // numpy's own axis-0 reduce order
        let nseg = mag.len();
        let mut flux = vec![0.0; nseg];
        for &b in &sel {
            for fr in 0..nseg {
                let d = if fr == 0 {
                    0.0
                } else {
                    mag[fr][b] - mag[fr - 1][b]
                };
                flux[fr] += d.max(0.0);
            }
        }
        let sm = fir_same(&flux, &hann_kernel(5));
        let hits = pick_peaks(&sm, &times, gap, sens[bi]);
        if !hits.is_empty() {
            out.push((name.to_string(), hits));
        }
    }
    out
}

/// How often the level envelope is sampled, and the beatless floor —
/// analyze.py's ENV_HZ and BEATLESS.
pub const ENV_HZ: f64 = 6.0;
pub const BEATLESS: usize = 8;
const PAN_WIN_S: f64 = 0.08;
const PAN_DEAD: f64 = 0.05;

/// CPython round(v, 2), the pan's spelling — like round3, {:.2} IS it.
pub fn round2(v: f64) -> f64 {
    format!("{v:.2}").parse().unwrap_or(v)
}

/// np.sqrt((seg ** 2).mean()): squares, pairwise mean, root.
fn rms(seg: &[f64]) -> f64 {
    let sq: Vec<f64> = seg.iter().map(|v| v * v).collect();
    (pairwise_sum(&sq) / seg.len() as f64).sqrt()
}

/// analyze.annotate_pan's per-hit measurement: L/R RMS balance in a short
/// window at the hit, dead-zoned and rounded.
fn pan_of(t: f64, left: &[f64], right: &[f64]) -> f64 {
    let n = (PAN_WIN_S * SR_F) as usize;
    let a = ((t * SR_F) as i64).max(0) as usize;
    let b = (a + n).min(left.len());
    if b <= a {
        return 0.0;
    }
    let lo = rms(&left[a..b]);
    let hi = rms(&right[a..b]);
    let mut pan = if lo + hi < 1e-9 {
        0.0
    } else {
        (hi - lo) / (hi + lo)
    };
    if pan.abs() < PAN_DEAD {
        pan = 0.0;
    }
    round2(pan)
}

/// analyze.envelope: band loudness over time for beatless material,
/// under level_* names.
pub fn envelope(x: &[f64], bands: &[(&str, f64, f64, f64)]) -> Vec<(String, Vec<(f64, f64)>)> {
    if x.len() < WIN * 2 {
        return Vec::new();
    }
    let (mag, freqs, times) = stft_mag(x);
    let nseg = mag.len();
    let step = 1.max(((SR_F / HOP as f64) / ENV_HZ).round_ties_even() as usize);
    let mut out = Vec::new();
    for (name, lo, hi, _gap) in bands {
        let sel: Vec<usize> = (0..freqs.len())
            .filter(|&b| freqs[b] >= *lo && freqs[b] < *hi)
            .collect();
        if sel.is_empty() {
            continue;
        }
        let rows = sel.len() as f64;
        let mut acc = vec![0.0; nseg];
        for &b in &sel {
            for (fr, slot) in acc.iter_mut().enumerate() {
                slot_add(slot, mag[fr][b]);
            }
        }
        let env0: Vec<f64> = acc.iter().map(|v| (v / rows).sqrt()).collect();
        if env0.iter().fold(0.0f64, |a, b| a.max(*b)) <= 0.0 {
            continue;
        }
        let sm = fir_same(&env0, &hann_kernel(9));
        let le: Vec<f64> = sm.iter().map(|v| (v * 50.0).ln_1p()).collect();
        let lo_v = le.iter().fold(f64::INFINITY, |a, b| a.min(*b));
        let hi_v = le.iter().fold(f64::NEG_INFINITY, |a, b| a.max(*b));
        if hi_v - lo_v < 1e-9 {
            continue;
        }
        let d = hi_v - lo_v;
        let pts: Vec<(f64, f64)> = (0..le.len())
            .step_by(step)
            .map(|i| (times[i], round3((le[i] - lo_v) / d)))
            .filter(|(_, v)| *v > 0.02)
            .collect();
        if !pts.is_empty() {
            out.push((name.replace("onset_", "level_"), pts));
        }
    }
    out
}

/// The squared-magnitude row accumulation — numpy squares the whole
/// selected block first, then row-adds sequentially.
fn slot_add(slot: &mut f64, m: f64) {
    *slot += m * m;
}

/// analyze.analyze_full: onsets, pans when stereo is known, and a level
/// envelope for any band with no beat worth following. Rows carry 2 or
/// 3 values exactly as the Python's tuples do.
pub fn analyze_full(
    x: &[f64],
    sensitivity: f64,
    stereo: Option<(&[f64], &[f64])>,
) -> Vec<(String, Vec<Vec<f64>>)> {
    analyze_full3(x, [sensitivity; 3], stereo)
}

/// analyze_full with the clip editor's per-band sensitivities.
pub fn analyze_full3(
    x: &[f64],
    sens: [f64; 3],
    stereo: Option<(&[f64], &[f64])>,
) -> Vec<(String, Vec<Vec<f64>>)> {
    let ons = analyze3(x, sens);
    let mut out: Vec<(String, Vec<Vec<f64>>)> = ons
        .iter()
        .map(|(n, hits)| (n.clone(), hits.iter().map(|(t, v)| vec![*t, *v]).collect()))
        .collect();
    if let Some((l, r)) = stereo {
        for (_, hits) in out.iter_mut() {
            for h in hits.iter_mut() {
                let p = pan_of(h[0], l, r);
                h.push(p);
            }
        }
    }
    let thin: Vec<(&str, f64, f64, f64)> = BANDS
        .iter()
        .filter(|(n, ..)| {
            out.iter()
                .find(|(name, _)| name == n)
                .map_or(0, |(_, h)| h.len())
                < BEATLESS
        })
        .copied()
        .collect();
    if thin.is_empty() {
        return out;
    }
    for (name, pts) in envelope(x, &thin) {
        out.push((name, pts.iter().map(|(t, v)| vec![*t, *v]).collect()));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairwise_and_std_have_numpy_shape() {
        let v: Vec<f64> = (0..2048).map(|i| (i as f64 * 0.37).sin()).collect();
        let seq: f64 = v.iter().sum();
        assert!((pairwise_sum(&v) - seq).abs() < 1e-9); // close, not equal
        assert!(np_std(&[1.0, 1.0, 1.0]) == 0.0);
    }

    #[test]
    fn a_clean_click_train_is_found_on_the_beat() {
        let mut x = vec![0.0; 44100 * 4];
        for k in 0..8 {
            let at = k * 22050;
            for i in 0..800 {
                x[at + i] += ((i as f64) * 0.9).sin() * (1.0 - i as f64 / 800.0);
            }
        }
        let out = analyze(&x, 1.1);
        let (_, hits) = out.iter().find(|(n, _)| n == "onset_mid").unwrap();
        assert!(hits.len() >= 6);
        assert!(hits[0].0 < 0.05);
        assert!(hits.iter().all(|(_, v)| *v > 0.0 && *v <= 1.0));
    }
}
