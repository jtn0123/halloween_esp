//! The rng-free musical pieces of tools/synth.py — B3, sample for sample.
//!
//! These are the compositions: chord tables and note lists driving the
//! voices in synth.rs, mixed into one buffer by `place` exactly as
//! synth._place does it (truncating index, clamped both ends, additive).
//! The marker lists ride along because the lights are cued from them.
//! tests/test_synth_rust.py compares whole buffers AND markers bit-exact.
//! The three that reach an envelope (drone's fade, pipe's swell) take the
//! host's kernel profile for np.interp's inner step; the rest are pure
//! sines and exponentials with nothing left to vary.

use crate::filters::Modes;
use crate::synth::{STOPS, interp, music_box, nt, piano, pipe};
use std::f64::consts::PI;

const SR_F: f64 = 44100.0;

/// synth._place: drop `sig` into `buf` at t0 seconds, clamped both ends.
pub fn place(buf: &mut [f64], sig: &[f64], t0: f64) {
    let mut i = (t0 * SR_F) as i64; // int() truncates toward zero, like `as`
    let mut sig = sig;
    if i < 0 {
        let skip = (-i) as usize;
        if skip >= sig.len() {
            return;
        }
        sig = &sig[skip..];
        i = 0;
    }
    let i = i as usize;
    if i >= buf.len() || sig.is_empty() {
        return;
    }
    let k = sig.len().min(buf.len() - i);
    for (o, s) in buf[i..i + k].iter_mut().zip(&sig[..k]) {
        *o += s;
    }
}

/// D2 against A-flat: the tritone, sustained.
pub fn drone(dur: f64, m: &Modes) -> Vec<f64> {
    let n = (dur * SR_F) as usize;
    let mut out = vec![0.0; n];
    let pairs = [
        (73.42, 0.5),
        (73.60, 0.35),
        (103.83, 0.30),
        (104.05, 0.22),
        (36.71, 0.30),
    ];
    for &(f, amp) in &pairs {
        let w = 2.0 * PI * f;
        for (i, o) in out.iter_mut().enumerate() {
            *o += amp * (w * (i as f64 / SR_F)).sin();
        }
    }
    let up = 3.0f64.min(dur / 2.0);
    let down = (dur - 3.0).max(dur / 2.0);
    let xp = [0.0, up, down, dur];
    let fp = [0.0, 1.0, 1.0, 0.0];
    let ww = 2.0 * PI * 0.045;
    for (i, o) in out.iter_mut().enumerate() {
        let t = i as f64 / SR_F;
        let wander = 0.75 + 0.25 * (ww * t + 1.0).sin();
        let fade = interp(t, &xp, &fp, m.interp_fused);
        *o = *o * wander * fade * 0.16;
    }
    out
}

/// The bell. Marker at the strike.
pub fn toll() -> (Vec<f64>, Vec<(f64, f64)>) {
    let dur = 5.0;
    let n = (dur * SR_F) as usize;
    let mut out = vec![0.0; n];
    for (idx, m) in [1.0, 2.76, 5.4, 8.9].into_iter().enumerate() {
        let i2 = idx as f64;
        let a = 0.24 / (i2 + 1.0);
        let k = 0.9 + i2 * 0.55;
        let w = 2.0 * PI * 138.0 * m;
        for (i, o) in out.iter_mut().enumerate() {
            let t = i as f64 / SR_F;
            *o += a * (w * t).sin() * (-t * k).exp();
        }
    }
    (out, vec![(0.0, 1.0)])
}

/// Procession in D minor: i - bII - i - V7b9.
pub fn organ(m: &Modes) -> (Vec<f64>, Vec<(f64, f64)>) {
    let chords: [(i32, &[i32]); 4] = [
        (-19, &[5, 8, 12]),
        (-18, &[6, 10, 13]),
        (-19, &[5, 8, 12]),
        (-12, &[12, 13, 16, 19]),
    ];
    let mut buf = vec![0.0; (28.0 * SR_F) as usize];
    for (i, &(ped, notes)) in chords.iter().enumerate() {
        let bt = i as f64 * 6.6;
        place(
            &mut buf,
            &pipe(nt(f64::from(ped)), 7.5, 0.078, STOPS, m),
            bt,
        );
        place(
            &mut buf,
            &pipe(nt(f64::from(ped + 12)), 7.5, 0.038, STOPS, m),
            bt,
        );
        for &s in notes {
            place(
                &mut buf,
                &pipe(nt(f64::from(s - 12)), 7.5, 0.030, STOPS, m),
                bt,
            );
        }
    }
    let marks = (0..chords.len()).map(|i| (i as f64 * 6.6, 1.0)).collect();
    (buf, marks)
}

/// 32' pedal held throughout; upper voices walk down chromatically.
pub fn descent(m: &Modes) -> Vec<f64> {
    let mut buf = vec![0.0; (27.5 * SR_F) as usize];
    place(&mut buf, &pipe(nt(-19.0), 25.5, 0.090, STOPS, m), 0.0);
    place(&mut buf, &pipe(nt(-31.0), 25.5, 0.060, STOPS, m), 0.0);
    let clusters: [[i32; 3]; 4] = [[17, 20, 24], [16, 19, 23], [15, 18, 22], [14, 17, 21]];
    for (i, cluster) in clusters.iter().enumerate() {
        let bt = 1.2 + i as f64 * 5.8;
        for &s in cluster {
            place(
                &mut buf,
                &pipe(nt(f64::from(s - 12)), 6.6, 0.030, STOPS, m),
                bt,
            );
        }
    }
    for s in [5, 8, 11, 14] {
        place(
            &mut buf,
            &pipe(nt(f64::from(s - 12)), 6.4, 0.034, STOPS, m),
            20.4,
        );
    }
    buf
}

/// 8 bars, 3/4. Bass on the downbeat, triad on 2 and 3, music box on top.
pub fn waltz() -> (Vec<f64>, Vec<(f64, f64)>) {
    const B: f64 = 0.52;
    let prog: [(i32, &[i32]); 8] = [
        (0, &[0, 3, 7]),
        (0, &[0, 3, 7]),
        (5, &[0, 3, 7]),
        (7, &[0, 4, 7]),
        (0, &[0, 3, 7]),
        (8, &[0, 4, 7]),
        (7, &[0, 4, 7]),
        (0, &[0, 3, 7]),
    ];
    let mel: [Option<i32>; 24] = [
        Some(12),
        Some(15),
        Some(19),
        Some(17),
        Some(15),
        Some(14),
        Some(17),
        Some(20),
        Some(17),
        Some(19),
        Some(14),
        Some(11),
        Some(12),
        Some(15),
        Some(19),
        Some(20),
        Some(19),
        Some(17),
        Some(19),
        Some(14),
        Some(11),
        Some(12),
        None,
        None,
    ];
    let mut buf = vec![0.0; (((8 * 3) as f64 * B + 3.0) * SR_F) as usize];
    for (bar, &(root, iv)) in prog.iter().enumerate() {
        let bt = (bar * 3) as f64 * B;
        place(&mut buf, &piano(nt(f64::from(root - 24)), 1.35, 0.22), bt);
        for k in [1.0, 2.0] {
            for &s in iv {
                place(
                    &mut buf,
                    &piano(nt(f64::from(root + s - 12)), 0.75, 0.07),
                    bt + k * B,
                );
            }
        }
    }
    for (i, note) in mel.iter().enumerate() {
        if let Some(s) = note {
            place(
                &mut buf,
                &music_box(nt(f64::from(*s)), 1.7, 0.15),
                i as f64 * B,
            );
        }
    }
    let marks = (0..prog.len())
        .map(|bar| ((bar * 3) as f64 * B, if bar % 4 == 0 { 1.0 } else { 0.7 }))
        .collect();
    (buf, marks)
}

/// The five-note descending call.
pub fn musicbox() -> Vec<f64> {
    let mut buf = vec![0.0; (3.5 * SR_F) as usize];
    for (i, s) in [24, 22, 19, 15, 12].into_iter().enumerate() {
        place(
            &mut buf,
            &music_box(nt(f64::from(s)), 1.9, 0.17),
            i as f64 * 0.30,
        );
    }
    buf
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn place_clamps_both_ends() {
        let mut buf = [0.0; 10];
        place(&mut buf, &[1.0, 2.0, 3.0], -1.0 / SR_F); // first sample dropped
        assert_eq!(&buf[..3], &[2.0, 3.0, 0.0]);
        place(&mut buf, &[5.0; 8], 8.0 / SR_F); // tail clipped
        assert_eq!(buf[9], 5.0);
        place(&mut buf, &[9.0], 99.0); // far past the end: no-op
        assert_eq!(buf.iter().sum::<f64>(), 15.0);
    }

    #[test]
    fn pieces_fill_their_advertised_lengths() {
        assert_eq!(musicbox().len(), (3.5 * SR_F) as usize);
        let (buf, marks) = toll();
        assert_eq!(buf.len(), (5.0 * SR_F) as usize);
        assert_eq!(marks, vec![(0.0, 1.0)]);
        let (_, wm) = waltz();
        assert_eq!(wm.len(), 8);
        assert_eq!(wm[4], (6.24, 1.0));
    }
}
