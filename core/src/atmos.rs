//! The rng-driven atmosphere voices of tools/synth.py — B3, sample exact.
//!
//! wind, heartbeat, creak, shriek, whispers and thunder: every one draws
//! from numpy's dice (rng.rs) and filters through scipy's butter
//! (filters.rs), so the platform-probed Modes ride through everything
//! here. Op orders were pinned by pure-Python replicas in the B3 spike;
//! the notable finds: np.geomspace is 10**linspace with forced endpoints,
//! %1.0 is fmod, and an array**2 is x*x (np.square) where **1.6 is libm
//! pow — tests/test_synth_rust.py holds whole buffers and markers exact.

use crate::filters::{butter_bp, butter_lp, sosfilt, Modes};
use crate::pieces::place;
use crate::rng::Pcg64;
use crate::synth::interp;
use std::f64::consts::PI;

const SR_F: f64 = 44100.0;

/// numpy dice with the platform's uniform form baked in.
pub struct Dice {
    g: Pcg64,
    fused: bool,
}

impl Dice {
    pub fn new(seed: u128, fused: bool) -> Self {
        Self {
            g: Pcg64::new(seed),
            fused,
        }
    }
    /// A public uniform draw, for harnesses that build test signals.
    pub fn uni2(&mut self, lo: f64, hi: f64) -> f64 {
        self.uni(lo, hi)
    }
    fn uni(&mut self, lo: f64, hi: f64) -> f64 {
        if self.fused {
            self.g.uniform_fma(lo, hi)
        } else {
            self.g.uniform_plain(lo, hi)
        }
    }
    fn noise(&mut self, n: usize) -> Vec<f64> {
        (0..n).map(|_| self.uni(-1.0, 1.0)).collect()
    }
}

/// synth._lp: butter lowpass at a clipped cutoff, then sosfilt.
fn lp(x: &[f64], hz: f64, order: usize, m: &Modes) -> Vec<f64> {
    let sos = butter_lp(order, hz.min(SR_F / 2.0 - 100.0), m);
    sosfilt(&[sos], x, m.sos_fused)
}

/// synth._bp: centre + Q to band edges, both clipped like the Python.
fn bp(x: &[f64], hz: f64, q: f64, m: &Modes) -> Vec<f64> {
    let hz = hz.clamp(30.0, SR_F / 2.0 - 200.0);
    let bw = (hz / q).max(20.0);
    let lo = (hz - bw / 2.0).max(20.0);
    let hi = (hz + bw / 2.0).min(SR_F / 2.0 - 100.0);
    let c = butter_bp(lo, hi, m);
    let rows = [
        [c[0], c[1], c[2], c[3], c[4], c[5]],
        [c[6], c[7], c[8], c[9], c[10], c[11]],
    ];
    sosfilt(&rows, x, m.sos_fused)
}

/// np.linspace(0, n, blocks+1).astype(int): i*delta, endpoint forced,
/// truncated toward zero.
pub(crate) fn edges(n: usize, blocks: usize) -> Vec<usize> {
    let d = n as f64 / blocks as f64;
    (0..=blocks)
        .map(|i| {
            if i == blocks {
                n
            } else {
                (i as f64 * d) as usize
            }
        })
        .collect()
}

/// np.geomspace: 10**linspace(log10 a, log10 b) with forced endpoints.
fn geomspace(a: f64, b: f64, n: usize) -> Vec<f64> {
    let (la, lb) = (a.log10(), b.log10());
    let d = (lb - la) / (n - 1) as f64;
    let mut out: Vec<f64> = (0..n).map(|i| 10f64.powf(i as f64 * d + la)).collect();
    out[0] = a;
    out[n - 1] = b;
    out
}

/// synth._sweep_lp: blockwise time-varying lowpass with a 64-sample
/// warm-up, seams softened by an order-1 pass.
fn sweep_lp(x: &[f64], f0: f64, f1: f64, m: &Modes) -> Vec<f64> {
    let n = x.len();
    let mut out = vec![0.0; n];
    let e = edges(n, 48);
    let freqs = geomspace(f0, f1, 48);
    for i in 0..48 {
        let (a, b) = (e[i], e[i + 1]);
        if b <= a {
            continue;
        }
        let seg = lp(&x[a.saturating_sub(64)..b], freqs[i], 2, m);
        out[a..b].copy_from_slice(&seg[seg.len() - (b - a)..]);
    }
    lp(&out, (f0 * 1.5).min(SR_F / 2.0 - 200.0), 1, m)
}

pub fn thunder(d: &mut Dice, m: &Modes) -> Vec<f64> {
    let n = (3.2 * SR_F) as usize;
    let src = d.noise(n);
    let crack = sweep_lp(&src, 900.0, 70.0, m);
    let sub_f = geomspace(50.0, 27.0, n);
    let mut acc = 0.0;
    let mut out = Vec::with_capacity(n);
    for (i, (c, f)) in crack.iter().zip(&sub_f).enumerate() {
        let t = i as f64 / SR_F;
        let cr = c * interp(t, &[0.0, 0.05, 3.0], &[0.0, 1.0, 0.0]).powf(1.6);
        acc += f;
        let sub = (2.0 * PI * acc / SR_F).sin()
            * interp(t, &[0.0, 0.14, 2.9], &[0.0, 1.0, 0.0]).powf(1.5);
        out.push(0.85 * cr + 0.55 * sub);
    }
    out
}

pub fn creak(d: &mut Dice, m: &Modes) -> Vec<f64> {
    let dur = 1.05;
    let n = (dur * SR_F) as usize;
    let steps = 26;
    let fs: Vec<f64> = (0..steps).map(|_| d.uni(70.0, 160.0)).collect();
    let rep = (n as f64 / steps as f64).ceil() as usize;
    let mut acc = 0.0;
    let mut saw = Vec::with_capacity(n);
    'outer: for f in &fs {
        for _ in 0..rep {
            if saw.len() >= n {
                break 'outer;
            }
            acc += f;
            saw.push(2.0 * ((acc / SR_F) % 1.0) - 1.0);
        }
    }
    let out = bp(&saw, 760.0, 7.0, m);
    out.iter()
        .enumerate()
        .map(|(i, o)| {
            let t = i as f64 / SR_F;
            o * interp(t, &[0.0, 0.06, dur], &[0.0, 1.0, 0.0]).powf(1.4) * 0.30
        })
        .collect()
}

pub fn shriek(d: &mut Dice, m: &Modes) -> Vec<f64> {
    let dur = 0.9;
    let n = (dur * SR_F) as usize;
    let src = d.noise(n);
    let mut out = vec![0.0; n];
    let e = edges(n, 40);
    let mut centres = geomspace(700.0, 2900.0, 20);
    centres.extend(geomspace(2900.0, 900.0, 20));
    for i in 0..40 {
        let (a, b) = (e[i], e[i + 1]);
        if b <= a {
            continue;
        }
        let seg = bp(&src[a.saturating_sub(256)..b], centres[i], 9.0, m);
        out[a..b].copy_from_slice(&seg[seg.len() - (b - a)..]);
    }
    out.iter()
        .enumerate()
        .map(|(i, o)| {
            let t = i as f64 / SR_F;
            o * interp(t, &[0.0, 0.04, dur], &[0.0, 1.0, 0.0]).powf(1.3) * 0.34
        })
        .collect()
}

pub fn wind(dur: f64, d: &mut Dice, m: &Modes) -> Vec<f64> {
    let n = (dur * SR_F) as usize;
    let src = d.noise(n);
    let mut out = vec![0.0; n];
    let blocks = 1.max((dur * 4.0) as usize);
    let e = edges(n, blocks);
    for i in 0..blocks {
        let (a, b) = (e[i], e[i + 1]);
        if b <= a {
            continue;
        }
        let centre = 420.0 + 240.0 * (2.0 * PI * 0.09 * (a as f64 / SR_F)).sin();
        let seg = bp(&src[a.saturating_sub(512)..b], centre, 0.7, m);
        out[a..b].copy_from_slice(&seg[seg.len() - (b - a)..]);
    }
    let up = 2.5f64.min(dur / 2.0);
    let down = (dur - 1.5).max(dur / 2.0);
    out.iter()
        .enumerate()
        .map(|(i, o)| {
            let t = i as f64 / SR_F;
            let swell = 1.0 + 0.38 * (2.0 * PI * 0.06 * t).sin();
            let fade = interp(t, &[0.0, up, down, dur], &[0.0, 1.0, 1.0, 0.0]);
            o * swell * fade * 0.10
        })
        .collect()
}

/// Lub-dub at ~48 bpm, with the jitter and the past-the-end dub rule.
pub fn heartbeat(dur: f64, d: &mut Dice, m: &Modes) -> (Vec<f64>, Vec<(f64, f64)>) {
    let n = (dur * SR_F) as usize;
    let mut buf = vec![0.0; n];
    let thump = |f: f64, amp: f64, m: &Modes| -> Vec<f64> {
        let tn = (0.22 * SR_F) as usize;
        let body: Vec<f64> = (0..tn)
            .map(|i| {
                let t = i as f64 / SR_F;
                (2.0 * PI * f * t).sin() * (-t * 26.0).exp()
            })
            .collect();
        lp(&body, 150.0, 2, m).iter().map(|v| v * amp).collect()
    };
    let period = 1.25;
    let mut t0 = 0.0;
    let mut beats = Vec::new();
    while t0 < dur {
        let jitter = d.uni(-0.03, 0.03);
        place(&mut buf, &thump(52.0, 1.00, m), t0 + jitter);
        place(&mut buf, &thump(64.0, 0.55, m), t0 + 0.18 + jitter);
        beats.push(((t0 + jitter).max(0.0), 1.00));
        let dub = t0 + 0.18 + jitter;
        if dub < dur {
            beats.push((dub, 0.55));
        }
        t0 += period;
    }
    (buf, beats)
}

/// Sibilant bursts through wandering formants. env's sin² is x*x — the
/// numpy array**2 path — where the fade powers above are libm pow.
pub fn whispers(dur: f64, d: &mut Dice, m: &Modes) -> (Vec<f64>, Vec<(f64, f64)>) {
    let n = (dur * SR_F) as usize;
    let mut buf = vec![0.0; n];
    let mut words = Vec::new();
    let mut t0 = 0.6;
    while t0 < dur - 1.0 {
        let wlen = d.uni(0.25, 0.9);
        let wn = (wlen * SR_F) as usize;
        let seg0 = d.noise(wn);
        let f1 = d.uni(1200.0, 2600.0);
        let f2 = f1 * d.uni(1.3, 1.9);
        let pa = bp(&seg0, f1, 8.0, m);
        let pb = bp(&seg0, f2, 10.0, m);
        let wob = d.uni(6.0, 11.0);
        let bi = (t0 * SR_F) as usize;
        if bi < n {
            let k = wn.min(n - bi);
            for j in 0..k {
                let st = j as f64 / SR_F;
                let s = (PI * st / wlen).sin();
                let env = (s * s) * (1.0 + 0.5 * (2.0 * PI * wob * st).sin());
                buf[bi + j] += (pa[j] + 0.6 * pb[j]) * env;
            }
        }
        words.push((t0, 0.5 + 0.5 * (wlen / 0.9).min(1.0)));
        t0 += wlen + d.uni(0.15, 1.4);
    }
    (buf.iter().map(|v| v * 0.5).collect(), words)
}

/// synth_master.reverb_ir: decaying noise, drawn from the shared dice.
pub fn reverb_ir(secs: f64, decay: f64, d: &mut Dice) -> Vec<f64> {
    let n = (secs * SR_F) as usize;
    let noise = d.noise(n);
    noise
        .iter()
        .enumerate()
        .map(|(i, v)| v * (1.0 - i as f64 / n as f64).powf(decay))
        .collect()
}

/// synth_master.apply_reverb: the stone hall, through the defined-order
/// FFT (crate::fft) so the tail matches the Python bit for bit.
pub fn apply_reverb(x: &[f64], wet: f64, d: &mut Dice) -> Vec<f64> {
    if wet <= 0.0 {
        return x.to_vec();
    }
    let ir = reverb_ir(3.4, 2.4, d);
    let mut tail = crate::fft::fft_convolve(x, &ir);
    tail.truncate(x.len());
    let peak = tail.iter().fold(0.0f64, |m, v| m.max(v.abs()));
    if peak > 0.0 {
        for v in tail.iter_mut() {
            *v /= peak; // the Python divides; a reciprocal would round off
        }
    }
    let m = x
        .iter()
        .map(|v| v.abs() + 1e-9)
        .fold(f64::NEG_INFINITY, f64::max);
    x.iter()
        .zip(&tail)
        .map(|(xv, tv)| xv + wet * tv * m)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const M: Modes = Modes {
        mul_fused: true,
        poly_fused: false,
        div_fused: true,
        sqrt_form: 2,
        sos_fused: true,
    };

    #[test]
    fn voices_have_their_advertised_shapes() {
        let mut d = Dice::new(11, true);
        assert_eq!(creak(&mut d, &M).len(), (1.05 * SR_F) as usize);
        let mut d = Dice::new(15, true);
        let (buf, beats) = heartbeat(3.0, &mut d, &M);
        assert_eq!(buf.len(), 3 * 44100);
        assert_eq!(beats.len(), 6); // 3 lub-dub pairs
        assert!(beats[0].0 >= 0.0);
    }

    #[test]
    fn edges_and_geomspace_hold_their_endpoints() {
        let e = edges(46305, 48);
        assert_eq!((e[0], e[48]), (0, 46305));
        let g = geomspace(900.0, 70.0, 48);
        assert_eq!((g[0], g[47]), (900.0, 70.0));
    }
}
