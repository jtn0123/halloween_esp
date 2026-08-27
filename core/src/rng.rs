//! numpy's default_rng, reproduced draw-for-draw — B3's foundation.
//!
//! render_audio.py seeds `np.random.default_rng(zlib.crc32(scene_id))` and
//! every synth voice draws from it, so a Rust render can only byte-match
//! the Python one if the random stream is identical. This is numpy's
//! SeedSequence (4-word entropy pool) feeding PCG64 XSL-RR 128/64,
//! verified state-for-state and draw-for-draw by tests/test_synth_rust.py.
//!
//! `uniform` has two forms because numpy's C is `low + range * next_double`
//! and whether the compiler fuses that into an fma depends on the wheel's
//! build target (clang on arm64 fuses; baseline x86-64 does not). The
//! parity test probes the host's numpy and picks the matching form.

const INIT_A: u32 = 0x43b0_d7e5;
const MULT_A: u32 = 0x931e_8875;
const INIT_B: u32 = 0x8b51_f9dd;
const MULT_B: u32 = 0x58f3_8ded;
const MIX_L: u32 = 0xca01_f9dd;
const MIX_R: u32 = 0x4973_f715;
const XSHIFT: u32 = 16;

/// numpy SeedSequence(entropy).generate_state(n_words, uint32): the 4-word
/// entropy pool, mixed and cycled exactly as numpy does it.
pub fn seed_seq_state(entropy: u128, n_words: usize) -> Vec<u32> {
    let mut ent = Vec::new();
    let mut e = entropy;
    while e != 0 {
        ent.push((e & 0xffff_ffff) as u32);
        e >>= 32;
    }
    if ent.is_empty() {
        ent.push(0);
    }
    let mut pool = [0u32; 4];
    let mut hc = INIT_A;
    let mut hash = |v: u32| -> u32 {
        let mut v = v ^ hc;
        hc = hc.wrapping_mul(MULT_A);
        v = v.wrapping_mul(hc);
        v ^ (v >> XSHIFT)
    };
    let mix = |x: u32, y: u32| -> u32 {
        let r = x.wrapping_mul(MIX_L).wrapping_sub(y.wrapping_mul(MIX_R));
        r ^ (r >> XSHIFT)
    };
    for (i, slot) in pool.iter_mut().enumerate() {
        *slot = hash(*ent.get(i).unwrap_or(&0));
    }
    for src in 0..4 {
        for dst in 0..4 {
            if src != dst {
                pool[dst] = mix(pool[dst], hash(pool[src]));
            }
        }
    }
    for &word in ent.iter().skip(4) {
        for slot in &mut pool {
            *slot = mix(*slot, hash(word));
        }
    }
    let mut out = Vec::with_capacity(n_words);
    let mut hc2 = INIT_B;
    for i in 0..n_words {
        let mut v = pool[i % 4] ^ hc2;
        hc2 = hc2.wrapping_mul(MULT_B);
        v = v.wrapping_mul(hc2);
        out.push(v ^ (v >> XSHIFT));
    }
    out
}

/// PCG64 XSL-RR 128/64 — numpy's np.random.PCG64, bit for bit.
pub struct Pcg64 {
    state: u128,
    inc: u128,
}

/// PCG's 128-bit default multiplier.
const PCG_MULT: u128 = 0x2360_ed05_1fc6_5da4_4385_df64_9fcc_f645;

impl Pcg64 {
    /// What `np.random.default_rng(seed)` builds: SeedSequence expands the
    /// entropy to four u64 (little-endian word pairs), then pcg64_srandom:
    /// state=0, step, += initstate, step.
    pub fn new(seed: u128) -> Self {
        let w = seed_seq_state(seed, 8);
        let u64s: Vec<u64> = (0..4)
            .map(|i| (u64::from(w[2 * i + 1]) << 32) | u64::from(w[2 * i]))
            .collect();
        let initstate = (u128::from(u64s[0]) << 64) | u128::from(u64s[1]);
        let initseq = (u128::from(u64s[2]) << 64) | u128::from(u64s[3]);
        let inc = (initseq << 1) | 1;
        let state = (inc.wrapping_add(initstate))
            .wrapping_mul(PCG_MULT)
            .wrapping_add(inc);
        Self { state, inc }
    }

    /// Step then output (numpy's 128-bit variant): rotr64(hi ^ lo, hi >> 58).
    pub fn next64(&mut self) -> u64 {
        self.state = self.state.wrapping_mul(PCG_MULT).wrapping_add(self.inc);
        let s = self.state;
        let x = ((s >> 64) as u64) ^ (s as u64);
        x.rotate_right((s >> 122) as u32)
    }

    /// The 53-bit double in [0, 1) numpy makes of each draw.
    pub fn next_double(&mut self) -> f64 {
        (self.next64() >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
    }

    /// Generator.uniform(low, high), in the fused form arm64 wheels compile
    /// `low + range * d` into.
    pub fn uniform_fma(&mut self, lo: f64, hi: f64) -> f64 {
        (hi - lo).mul_add(self.next_double(), lo)
    }

    /// The same draw in the unfused form (two roundings) — what a baseline
    /// x86-64 numpy wheel computes.
    pub fn uniform_plain(&mut self, lo: f64, hi: f64) -> f64 {
        lo + (hi - lo) * self.next_double()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seed_12345_matches_numpy_goldens() {
        // Every constant here was read straight off numpy 2.5.2 in the B3
        // spike (2026-08-27): SeedSequence pool words, PCG64.state, and the
        // first raw draws for np.random.PCG64(12345).
        let w = seed_seq_state(12345, 8);
        assert_eq!(w[0], 0xa03d_837c);
        assert_eq!(w[1], 0xb5ae_6482);
        let mut g = Pcg64::new(12345);
        assert_eq!(g.state, 0x1905_e033_5aae_9634_9199_b0d0_9775_add5);
        assert_eq!(g.inc, 0xc9c7_353e_6e2b_1f28_7d76_1f2d_4027_fae7);
        assert_eq!(g.next64(), 0x3a32_b18d_b2ff_c19d);
        assert_eq!(g.next64(), 0x5117_1315_c9e4_c4de);
        assert_eq!(g.next64(), 0xcc20_2482_3444_efd9);
    }

    #[test]
    fn uniform_forms_reproduce_the_spike_values() {
        let mut g = Pcg64::new(12345);
        // default_rng(12345).uniform(-1, 1, 2) on an arm64 (fused) numpy.
        assert_eq!(g.uniform_fma(-1.0, 1.0), -0.545_327_955_065_660_7);
        assert_eq!(g.uniform_fma(-1.0, 1.0), -0.366_483_320_580_494_27);
        // random() is next_double directly, fusion-free on every platform.
        let mut h = Pcg64::new(12345);
        assert_eq!(h.next_double(), 0.227_336_022_467_169_66);
    }
}
