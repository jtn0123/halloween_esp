//! The master chain of tools/render_audio.py and synth.limit — B3.
//!
//! Everything after the voices are mixed: the lookahead limiter (whose
//! moving averages are cumsum differences — synth.py was moved off
//! np.convolve in the same change, because BLAS dot order is a vendor
//! choice and the render's low bits differed across machines), the loop
//! crossfade, the end fade, peak normalisation, and the int16 quantise
//! that write_wav performs. tests/test_master_rust.py holds each step to
//! the Python bit for bit.

const SR_F: f64 = 44100.0;

/// synth._avg_same: "same"-placed moving average via cumsum differences.
fn avg_same(x: &[f64], win: usize) -> Vec<f64> {
    let n = x.len();
    let mut cs = vec![0.0; n + 1];
    let mut acc = 0.0;
    for (i, v) in x.iter().enumerate() {
        acc += v;
        cs[i + 1] = acc;
    }
    let off = (win - 1) / 2 + 1;
    let c = 1.0 / win as f64;
    (0..n)
        .map(|i| {
            let hi = (i + off).min(n);
            let lo = (i + off).saturating_sub(win);
            (cs[hi] - cs[lo]) * c
        })
        .collect()
}

/// synth.limit: smoothed peak envelope -> gain -> smoothed gain -> clip.
pub fn limit(x: &[f64], ceiling: f64) -> Vec<f64> {
    let peak = x.iter().fold(0.0f64, |m, v| m.max(v.abs()));
    if peak <= 0.0 {
        return x.to_vec();
    }
    let win = 1.max((0.005 * SR_F) as usize);
    let ax: Vec<f64> = x.iter().map(|v| v.abs()).collect();
    let env = avg_same(&ax, win);
    let gain: Vec<f64> = env
        .iter()
        .map(|e| if *e > ceiling { ceiling / e } else { 1.0 })
        .collect();
    let smooth = 1.max((0.02 * SR_F) as usize);
    let pad = smooth / 2;
    let mut padded = vec![gain[0]; pad];
    padded.extend_from_slice(&gain);
    padded.extend(std::iter::repeat_n(*gain.last().unwrap(), pad));
    let sm = avg_same(&padded, smooth);
    x.iter()
        .zip(&sm[pad..pad + env.len()])
        .map(|(v, g)| (v * g).clamp(-1.0, 1.0))
        .collect()
}

/// np.linspace(a, b, n)[i]: i*delta + a, endpoint forced.
fn lin(a: f64, b: f64, n: usize, i: usize) -> f64 {
    if i == n - 1 {
        return b;
    }
    i as f64 * ((b - a) / (n - 1) as f64) + a
}

/// render_scene's loop path: crossfade the tail into the head so the loop
/// point is inaudible.
pub fn loop_crossfade(buf: &mut [f64]) {
    let n = buf.len();
    let xf = ((0.6 * SR_F) as usize).min(n / 4);
    if xf == 0 {
        return;
    }
    let head: Vec<f64> = buf[..xf].to_vec();
    for i in 0..xf {
        let r = lin(0.0, 1.0, xf, i);
        buf[n - xf + i] = buf[n - xf + i] * (1.0 - r) + head[i] * r;
    }
}

/// A linspace(1,0) fade over the last `secs` seconds (or the whole
/// buffer when shorter) — render_scene's one-shot tail and the 0.4 s
/// `take` trim both use this shape.
pub fn fade_tail(buf: &mut [f64], secs: f64) {
    let n = buf.len();
    let fade = ((secs * SR_F) as usize).min(n);
    if fade < 2 {
        return;
    }
    for i in 0..fade {
        buf[n - fade + i] *= lin(1.0, 0.0, fade, i);
    }
}

/// render_scene's one-shot path: a quarter-second fade to silence.
pub fn end_fade(buf: &mut [f64]) {
    fade_tail(buf, 0.25);
}

/// render_scene's normalisation: every scene at the same peak.
pub fn normalize(buf: &mut [f64], target: f64) {
    let peak = buf.iter().fold(0.0f64, |m, v| m.max(v.abs()));
    if peak > 1e-6 {
        let s = target / peak;
        for v in buf.iter_mut() {
            *v *= s;
        }
    }
}

/// write_wav's quantise: clip, scale by 32767, truncate toward zero.
pub fn quantize16(buf: &[f64]) -> Vec<i16> {
    buf.iter()
        .map(|v| (v.clamp(-1.0, 1.0) * 32767.0) as i16)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limiter_holds_the_ceiling_and_leaves_quiet_material_alone() {
        let hot: Vec<f64> = (0..44100).map(|i| 1.4 * (i as f64 * 0.1).sin()).collect();
        let out = limit(&hot, 0.89);
        assert!(out.iter().all(|v| v.abs() <= 1.0));
        // the ride pulls a 1.4 peak well down, even before the clip backstop
        assert!(out.iter().fold(0.0f64, |m, v| m.max(v.abs())) < 1.3);
        let quiet = vec![0.1; 1000];
        let q = limit(&quiet, 0.89);
        assert_eq!(q, quiet); // gain 1 everywhere
        assert_eq!(limit(&[0.0; 64], 0.89), [0.0; 64]);
    }

    #[test]
    fn tails_and_quantise_behave() {
        let mut b = vec![0.5; 200_000];
        loop_crossfade(&mut b);
        assert_eq!(b[0], 0.5);
        let mut f = vec![0.5; 200_000];
        end_fade(&mut f);
        assert_eq!(*f.last().unwrap(), 0.0);
        assert_eq!(quantize16(&[1.5, -1.5, 0.5]), vec![32767, -32767, 16383]);
        let mut n = vec![0.2, -0.4];
        normalize(&mut n, 0.89);
        assert_eq!(n[1], -0.4 * (0.89 / 0.4)); // the Python's own spelling
    }
}
