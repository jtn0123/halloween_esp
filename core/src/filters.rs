//! scipy's butter + sosfilt, bit for bit — the filters under synth.py's
//! _lp/_bp (B3).
//!
//! The design chain (buttap -> prewarp -> lp2lp/lp2bp -> bilinear ->
//! zpk2sos) was pinned in the B3 spike (2026-08-27) against scipy 18/18
//! lowpass and 405/405 bandpass cases. The surprise: numpy's complex
//! kernels carry COMPILER-PLACED fusions that differ per wheel — the
//! ufunc multiply is fused on arm64 while np.poly's convolve loop is
//! naive, division is Smith-with-reciprocal fused at all three products,
//! csqrt's hypot is sqrt(fma(x,x,y*y)) — so every kernel here takes a
//! mode flag and tests/test_synth_rust.py PROBES the installed numpy to
//! pick the matching form on whatever platform runs the suite.
//!
//! The manylinux wheels (2026-08-31, the first Linux run of the parity
//! suites) turned every one of those answers around, and added two forms
//! nobody had seen: gcc fuses a complex multiply on the SECOND product
//! (`fma(-ai, bi, ar*br)`, the fnmsub) where clang fuses the first, and
//! Linux's np.sqrt is glibc's csqrt — a different identity from the
//! FreeBSD one numpy carries as a fallback. Hence mul/poly/sqrt are
//! small integers rather than flags. See docs/PARITY.md.

const SR_F: f64 = 44100.0;
use std::f64::consts::PI;

/// Which compiled form each numpy/scipy kernel has on this host.
#[derive(Clone, Copy)]
pub struct Modes {
    /// The complex-multiply forms, shared by the ufunc and np.poly's
    /// convolve loop: 0 = naive, 1 = fused on the first product
    /// (`fma(ar, br, -(ai*bi))`, clang), 2 = fused on the second
    /// (`fma(-ai, bi, ar*br)`, gcc).
    pub mul_form: u8,
    pub poly_form: u8,
    pub div_fused: bool,
    /// 0 = FreeBSD csqrt on libm hypot, 1 = the same on naive
    /// sqrt(x*x+y*y), 2 = the same on sqrt(fma(x,x,y*y)), 3 = glibc's
    /// csqrt (hypot, then the halve-first identity).
    pub sqrt_form: u8,
    pub sos_fused: bool,
    /// np.interp's inner step: fused slope*(x-xp)+fp, or not.
    pub interp_fused: bool,
}

impl Modes {
    /// The render's defined arithmetic: the kernel-fma profile of the
    /// reference numpy wheel the pipeline was held bit-exact against
    /// (arm64 macOS, "101211" + fused uniforms). Production renders use
    /// this everywhere, so a scene is the same bytes on every machine;
    /// the parity tests probe the HOST's wheel and pass an override so
    /// the Python comparison stays exact off the reference platform too.
    pub const CANONICAL: Modes = Modes {
        mul_form: 1,
        poly_form: 0,
        div_fused: true,
        sqrt_form: 2,
        sos_fused: true,
        interp_fused: true,
    };

    /// The uniform draw's half of the same profile.
    pub const CANONICAL_UNI_FUSED: bool = true;

    /// Six characters, one per kernel: mul, poly, div, sqrt, sosfilt,
    /// interp. Short strings keep the CANONICAL answer for the tail, so
    /// an older five-character profile still names the same arithmetic.
    pub fn parse(s: &str) -> Self {
        let b: Vec<u8> = s.bytes().map(|c| c.saturating_sub(b'0')).collect();
        let c = Self::CANONICAL;
        let get = |i: usize, d: u8| -> u8 { b.get(i).copied().unwrap_or(d) };
        Self {
            mul_form: get(0, c.mul_form),
            poly_form: get(1, c.poly_form),
            div_fused: get(2, u8::from(c.div_fused)) != 0,
            sqrt_form: get(3, c.sqrt_form),
            sos_fused: get(4, u8::from(c.sos_fused)) != 0,
            interp_fused: get(5, u8::from(c.interp_fused)) != 0,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub struct Cx {
    pub re: f64,
    pub im: f64,
}

impl Cx {
    pub fn new(re: f64, im: f64) -> Self {
        Self { re, im }
    }
    fn conj(self) -> Self {
        Self::new(self.re, -self.im)
    }
    fn add(self, o: Cx) -> Self {
        Self::new(self.re + o.re, self.im + o.im)
    }
    fn sub(self, o: Cx) -> Self {
        Self::new(self.re - o.re, self.im - o.im)
    }
    fn neg(self) -> Self {
        Self::new(-self.re, -self.im)
    }
}

fn cmul(a: Cx, b: Cx, form: u8) -> Cx {
    match form {
        1 => Cx::new(
            a.re.mul_add(b.re, -(a.im * b.im)),
            a.re.mul_add(b.im, a.im * b.re),
        ),
        2 => Cx::new(
            (-a.im).mul_add(b.im, a.re * b.re),
            a.im.mul_add(b.re, a.re * b.im),
        ),
        _ => Cx::new(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re),
    }
}

/// numpy nc_quot: Smith's method with a reciprocal multiply.
fn cdiv(a: Cx, b: Cx, fused: bool) -> Cx {
    let (ar, ai, br, bi) = (a.re, a.im, b.re, b.im);
    if br.abs() >= bi.abs() {
        let rat = bi / br;
        if fused {
            let scl = 1.0 / bi.mul_add(rat, br);
            Cx::new(ai.mul_add(rat, ar) * scl, (-ar).mul_add(rat, ai) * scl)
        } else {
            let scl = 1.0 / (br + bi * rat);
            Cx::new((ar + ai * rat) * scl, (ai - ar * rat) * scl)
        }
    } else {
        let rat = br / bi;
        if fused {
            let scl = 1.0 / br.mul_add(rat, bi);
            Cx::new(ar.mul_add(rat, ai) * scl, ai.mul_add(rat, -ar) * scl)
        } else {
            let scl = 1.0 / (bi + br * rat);
            Cx::new((ar * rat + ai) * scl, (ai * rat - ar) * scl)
        }
    }
}

/// numpy's csqrt: FreeBSD's, on a hypot that varies per wheel — or
/// glibc's own, which the Linux wheels call instead (sqrt_form).
fn csqrt(z: Cx, form: u8) -> Cx {
    let (x, y) = (z.re, z.im);
    if x == 0.0 && y == 0.0 {
        return Cx::new(0.0, y);
    }
    if form == 3 {
        // glibc's __csqrt: the same identity, halved in a different
        // order — and note x == 0 takes the second branch there.
        let d = x.hypot(y);
        if x > 0.0 {
            let r = (0.5 * (d + x)).sqrt();
            return Cx::new(r, 0.5 * (y / r));
        }
        let s = (0.5 * (d - x)).sqrt();
        return Cx::new(((0.5 * y) / s).abs(), s.copysign(y));
    }
    let h = match form {
        2 => x.mul_add(x, y * y).sqrt(),
        1 => (x * x + y * y).sqrt(),
        _ => x.hypot(y),
    };
    let t = ((x.abs() + h) * 0.5).sqrt();
    if x >= 0.0 {
        Cx::new(t, y / (2.0 * t))
    } else {
        Cx::new(y.abs() / (2.0 * t), t.copysign(y))
    }
}

/// np.poly by convolution, real coefficients when roots pair conjugate —
/// numpy's convolve loop multiplies with its OWN form (poly_form).
fn poly(roots: &[Cx], form: u8) -> Vec<f64> {
    let mut a = vec![Cx::new(1.0, 0.0)];
    for &r in roots {
        let mut b = vec![Cx::new(0.0, 0.0); a.len() + 1];
        for (i, &c) in a.iter().enumerate() {
            b[i] = b[i].add(c);
            b[i + 1] = b[i + 1].add(cmul(c, r.neg(), form));
        }
        a = b;
    }
    // conj-paired roots -> real coefficients (all our shapes qualify)
    a.iter().map(|c| c.re).collect()
}

fn buttap(n: usize) -> Vec<Cx> {
    let mut out = Vec::new();
    let mut k = -(n as i64) + 1;
    while k < n as i64 {
        let theta = PI * k as f64 / (2 * n) as f64;
        out.push(Cx::new(-theta.cos(), -theta.sin()));
        k += 2;
    }
    out
}

/// scipy.signal.butter(N, hz, "lowpass", fs=44100, output="sos"), N = 1|2.
pub fn butter_lp(n: usize, hz: f64, m: &Modes) -> [f64; 6] {
    let wn = hz / (SR_F / 2.0);
    let warped = 4.0 * (PI * wn / 2.0).tan();
    let p: Vec<Cx> = buttap(n)
        .into_iter()
        .map(|x| Cx::new(warped * x.re, warped * x.im))
        .collect();
    let k = warped.powf(n as f64);
    let four = Cx::new(4.0, 0.0);
    let pz: Vec<Cx> = p
        .iter()
        .map(|&x| cdiv(four.add(x), four.sub(x), m.div_fused))
        .collect();
    let mut den = Cx::new(1.0, 0.0);
    for &x in &p {
        den = cmul(den, four.sub(x), m.mul_form);
    }
    let kz = k * cdiv(Cx::new(1.0, 0.0), den, m.div_fused).re;
    let zeros = vec![Cx::new(-1.0, 0.0); n];
    let b: Vec<f64> = poly(&zeros, m.poly_form).iter().map(|c| kz * c).collect();
    let a = poly(&pz, m.poly_form);
    let mut out = [0.0; 6];
    out[..b.len()].copy_from_slice(&b);
    out[3..3 + a.len()].copy_from_slice(&a);
    out
}

/// scipy.signal.butter(2, [lo, hi], "bandpass", fs=44100, output="sos"):
/// two sections. The zpk2sos pairing for this fixed shape: the pole pair
/// closest to the unit circle goes LAST and takes its nearest zero pair
/// (greedy, worst first); the overall gain lands on the first section.
pub fn butter_bp(lo: f64, hi: f64, m: &Modes) -> [f64; 12] {
    let warped: Vec<f64> = [lo, hi]
        .iter()
        .map(|w| 4.0 * (PI * (w / (SR_F / 2.0)) / 2.0).tan())
        .collect();
    let bw = warped[1] - warped[0];
    let wo = (warped[0] * warped[1]).sqrt();
    let p_lp: Vec<Cx> = buttap(2)
        .into_iter()
        .map(|x| Cx::new(x.re * bw / 2.0, x.im * bw / 2.0))
        .collect();
    let sq: Vec<Cx> = p_lp
        .iter()
        .map(|&x| {
            csqrt(
                cmul(x, x, m.mul_form).sub(Cx::new(wo * wo, 0.0)),
                m.sqrt_form,
            )
        })
        .collect();
    let mut p_bp: Vec<Cx> = p_lp.iter().zip(&sq).map(|(&x, &s)| x.add(s)).collect();
    p_bp.extend(p_lp.iter().zip(&sq).map(|(&x, &s)| x.sub(s)));
    let k = bw.powf(2.0);
    let four = Cx::new(4.0, 0.0);
    let pz: Vec<Cx> = p_bp
        .iter()
        .map(|&x| cdiv(four.add(x), four.sub(x), m.div_fused))
        .collect();
    let mut den = Cx::new(1.0, 0.0);
    for &x in &p_bp {
        den = cmul(den, four.sub(x), m.mul_form);
    }
    let kz = k * cdiv(Cx::new(16.0, 0.0), den, m.div_fused).re;
    // conjugate pairs, by exact match
    let mut pairs: Vec<(Cx, Cx)> = Vec::new();
    let mut used = [false; 4];
    for i in 0..4 {
        if used[i] {
            continue;
        }
        for j in i + 1..4 {
            if !used[j] && pz[j] == pz[i].conj() {
                pairs.push((pz[i], pz[j]));
                used[i] = true;
                used[j] = true;
                break;
            }
        }
    }
    let radius = |c: Cx| c.re.hypot(c.im);
    let mut order: Vec<usize> = (0..pairs.len()).collect();
    order.sort_by(|&a, &b| radius(pairs[b].0).total_cmp(&radius(pairs[a].0)));
    let mut zeros_left = vec![1.0, -1.0];
    let mut assign = [0.0; 2];
    for &idx in &order {
        let p1 = pairs[idx].0;
        zeros_left.sort_by(|&a, &b| {
            radius(p1.sub(Cx::new(a, 0.0))).total_cmp(&radius(p1.sub(Cx::new(b, 0.0))))
        });
        assign[idx] = zeros_left.remove(0);
    }
    let mut sections: Vec<usize> = (0..pairs.len()).collect();
    sections.sort_by(|&a, &b| radius(pairs[a].0).total_cmp(&radius(pairs[b].0)));
    let mut out = [0.0; 12];
    for (si, &idx) in sections.iter().enumerate() {
        let (p1, p2) = pairs[idx];
        let z = Cx::new(assign[idx], 0.0);
        let bpoly = poly(&[z, z], m.poly_form);
        let apoly = poly(&[p1, p2], m.poly_form);
        let gain = if si == 0 { kz } else { 1.0 };
        for (t, v) in out[si * 6..si * 6 + 3].iter_mut().zip(&bpoly) {
            *t = gain * v;
        }
        out[si * 6 + 3..si * 6 + 6].copy_from_slice(&apoly);
    }
    out
}

/// scipy.signal.sosfilt: direct-form II transposed, cascaded per sample.
pub fn sosfilt(sos: &[[f64; 6]], x: &[f64], fused: bool) -> Vec<f64> {
    let mut state = vec![[0.0f64; 2]; sos.len()];
    let mut out = Vec::with_capacity(x.len());
    for &sample in x {
        let mut v = sample;
        for (row, z) in sos.iter().zip(state.iter_mut()) {
            let [b0, b1, b2, _, a1, a2] = *row;
            let y = if fused {
                b0.mul_add(v, z[0])
            } else {
                b0 * v + z[0]
            };
            if fused {
                z[0] = b1.mul_add(v, -(a1 * y)) + z[1];
                z[1] = b2.mul_add(v, -(a2 * y));
            } else {
                z[0] = b1 * v - a1 * y + z[1];
                z[1] = b2 * v - a2 * y;
            }
            v = y;
        }
        out.push(v);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const ARM64_MODES: Modes = Modes::CANONICAL;

    #[test]
    fn lowpass_is_normalised_and_plausible() {
        let sos = butter_lp(2, 150.0, &ARM64_MODES);
        assert_eq!(sos[3], 1.0);
        assert!(sos[0] > 0.0 && sos[0] < 1e-3); // tiny gain for a low cut
        let one = butter_lp(1, 150.0, &ARM64_MODES);
        assert_eq!(one[2], 0.0); // order 1: b2/a2 empty
        assert_eq!(one[5], 0.0);
    }

    #[test]
    fn bandpass_pairs_conjugates_and_splits_zeros() {
        let sos = butter_bp(652.0, 868.0, &ARM64_MODES);
        // one section carries the +1 zeros (b: x, -2x, x), the other -1s
        let s1_sign = sos[1] / sos[0];
        let s2_sign = sos[7] / sos[6];
        assert!((s1_sign - 2.0).abs() < 1e-9);
        assert!((s2_sign + 2.0).abs() < 1e-9);
        assert_eq!(sos[3], 1.0);
        assert_eq!(sos[9], 1.0);
    }

    #[test]
    fn sosfilt_settles_dc_to_unity_for_lowpass() {
        let sos = butter_lp(2, 150.0, &ARM64_MODES);
        let x = vec![1.0; 44100];
        let y = sosfilt(&[sos], &x, true);
        assert!((y[44099] - 1.0).abs() < 1e-6);
    }
}
