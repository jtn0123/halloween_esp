//! The defined-order radix-2 FFT behind synth_master's reverb — B3.
//!
//! synth_master.py left pocketfft for exactly this file's benefit: an FFT
//! whose every rounding is spelled out can be matched bit for bit across
//! languages. Same shape as the Python: bit-reversal gather, one twiddle
//! table for the largest stage strided into the smaller ones (exact —
//! halving the angle and doubling the index round identically), butterflies
//! in plain mul/sub/add with separate re/im arrays so nothing ever fuses.

use std::f64::consts::PI;

/// In-place iterative radix-2 FFT; `invert` includes the exact 1/n scale.
pub fn fft(re: &mut [f64], im: &mut [f64], invert: bool) {
    let n = re.len();
    let bits = n.trailing_zeros();
    let idx: Vec<usize> = (0..n)
        .map(|i| i.reverse_bits() >> (usize::BITS - bits))
        .collect();
    let tmp: Vec<f64> = idx.iter().map(|&j| re[j]).collect();
    re.copy_from_slice(&tmp);
    let tmp: Vec<f64> = idx.iter().map(|&j| im[j]).collect();
    im.copy_from_slice(&tmp);
    let ang = (2.0 * PI / n as f64) * if invert { 1.0 } else { -1.0 };
    let wr_all: Vec<f64> = (0..n / 2).map(|j| (ang * j as f64).cos()).collect();
    let wi_all: Vec<f64> = (0..n / 2).map(|j| (ang * j as f64).sin()).collect();
    let mut m = 2;
    while m <= n {
        let half = m / 2;
        let stride = n / m;
        for blk in (0..n).step_by(m) {
            for j in 0..half {
                let (wr, wi) = (wr_all[j * stride], wi_all[j * stride]);
                let (ar, ai) = (re[blk + j], im[blk + j]);
                let (br, bi) = (re[blk + half + j], im[blk + half + j]);
                let tr = wr * br - wi * bi;
                let ti = wr * bi + wi * br;
                re[blk + j] = ar + tr;
                im[blk + j] = ai + ti;
                re[blk + half + j] = ar - tr;
                im[blk + half + j] = ai - ti;
            }
        }
        m *= 2;
    }
    if invert {
        let c = 1.0 / n as f64; // n is a power of two: exact reciprocal
        for v in re.iter_mut() {
            *v *= c;
        }
        for v in im.iter_mut() {
            *v *= c;
        }
    }
}

/// Full linear convolution — synth_master._fft_convolve, value for value.
pub fn fft_convolve(x: &[f64], h: &[f64]) -> Vec<f64> {
    let total = x.len() + h.len() - 1;
    let n = total.next_power_of_two();
    let mut xr = vec![0.0; n];
    xr[..x.len()].copy_from_slice(x);
    let mut xi = vec![0.0; n];
    let mut hr = vec![0.0; n];
    hr[..h.len()].copy_from_slice(h);
    let mut hi = vec![0.0; n];
    fft(&mut xr, &mut xi, false);
    fft(&mut hr, &mut hi, false);
    let mut yr: Vec<f64> = xr
        .iter()
        .zip(&hr)
        .zip(xi.iter().zip(&hi))
        .map(|((a, b), (c, d))| a * b - c * d)
        .collect();
    let mut yi: Vec<f64> = xr
        .iter()
        .zip(&hi)
        .zip(xi.iter().zip(&hr))
        .map(|((a, b), (c, d))| a * b + c * d)
        .collect();
    fft(&mut yr, &mut yi, true);
    yr.truncate(total);
    yr
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_recovers_the_signal_and_convolution_is_polynomial_mult() {
        let x: Vec<f64> = (0..64).map(|i| (i as f64 * 0.37).sin()).collect();
        let mut re = x.clone();
        let mut im = vec![0.0; 64];
        fft(&mut re, &mut im, false);
        fft(&mut re, &mut im, true);
        for (a, b) in re.iter().zip(&x) {
            assert!((a - b).abs() < 1e-12);
        }
        let y = fft_convolve(&[1.0, 2.0], &[3.0, 4.0, 5.0]);
        let want = [3.0, 10.0, 13.0, 10.0];
        assert_eq!(y.len(), 4);
        for (a, b) in y.iter().zip(&want) {
            assert!((a - b).abs() < 1e-12);
        }
    }
}
