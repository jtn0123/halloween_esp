// EXTRACTED VERBATIM from the pre-migration inline script.
// The reference the TypeScript port is checked against. Do not edit —
// with ONE deliberate exception, re-pinned 2026-08-21 for firmware v5.24:
//
//   `hash` was frac(sin(n*127.1)*43758.5453). The firmware could not compute
//   that to the same digits in float32, so the desk never matched the porch
//   frame for frame. Both sides now use the integer mix below (lowbias32 on
//   the lattice cell, 24-bit result). It is written out here INDEPENDENTLY
//   — not imported from the port — so a typo in effects.ts's mix32 still
//   fails effects_equivalence.ts. vnoise, fbm and every effect formula
//   beneath them are still the verbatim original.
  /* ── Smoothed value noise — the flame's whole personality ────────── */
  const hash = n => {
    let x = (n | 0) >>> 0;
    x ^= x >>> 16; x = Math.imul(x, 0x7feb352d) >>> 0;
    x ^= x >>> 15; x = Math.imul(x, 0x846ca68b) >>> 0;
    x ^= x >>> 16;
    return (x >>> 8) / 16777216;
  };
  const vnoise = x => { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return hash(i) * (1 - u) + hash(i + 1) * u; };
  const fbm = x => 0.55 * vnoise(x) + 0.30 * vnoise(x * 2.13 + 11.3) + 0.15 * vnoise(x * 4.31 + 27.7);

  /* ── Effects: (seconds, seed) → [r,g,b] in 0..1 ──────────────────── */
  const P = { depth: 0.55, speed: 1.4, bright: 0.85, hue: 0.5, soft: false, stops: 0.42 };

  /* Mansion palette: the two poles everything crossfades between. */
  const VIOLET = [0.66, 0.08, 1.00];
  const GREEN  = [0.14, 1.00, 0.42];
  const mix = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

  /* ── Effects: line-for-line port of firmware/castle_effects.h ──────
     Same formulas, same per-pixel seeds, so the screen shows what the
     firmware will compute — not a separate approximation. Each returns
     [r, g, b, w]; the RGBW jewels have a real 3000K white emitter, which
     the screen fakes as WARM_W. */
  const WARM_W = [1.00, 0.83, 0.62];   // what the jewel's white die looks like

  const EFFECTS = {
    off: () => [0, 0, 0, 0],
    candle: (t, s) => {
      const n = fbm(t * P.speed + s * 3.7);
      const l = Math.max(0, 1 - P.depth * (1 - n));
      return [0.34 * l, 0.05 * l, 0, 1.00 * l];
    },
    ember: (t, s) => {
      const n = fbm(t * 0.63 + s * 2.2);
      const l = 0.22 + 0.16 * n;
      return [0.40 * l, 0.06 * l, 0, 0.85 * l];
    },
    furnace: (t, s) => {
      const n = fbm(t * 2.5 + s * 0.9);
      const l = 0.80 + 0.20 * n;
      return [1.00 * l, 0.22 * l, 0.02 * l, 0.55 * l];
    },
    spirit: (t, s) => {
      const b = 0.5 + 0.5 * Math.sin(t * 1.15 + s * 0.8);
      const l = 0.22 + 0.42 * b;
      return [0.10 * l, 1.00 * l, 0.66 * l, 0];
    },
    eyes: (t, s) => {
      const blink = vnoise(t * 1.9 + s * 0.55) > 0.82 ? 0.1 : 1;
      const l = (0.55 + 0.28 * Math.sin(t * 3.1)) * blink;
      return [1.00 * l, 0.05 * l, 0.03 * l, 0];
    },
    seance: (t, s) => {
      const b = 0.5 + 0.5 * Math.sin(t * 0.80 + s * 0.6);
      return [...mix(VIOLET, GREEN, 0).map(v => v * (0.24 + 0.52 * b)), 0];
    },
    wisp: (t, s) => {
      const n = fbm(t * 2.1 + s * 5.3);
      const l = Math.max(0, 0.18 + 0.82 * n - 0.14);
      return [...mix(VIOLET, GREEN, 1).map(v => v * l), 0];
    },
    mansion: (t, s) => {
      const sweep = 0.5 + 0.5 * Math.sin(t * 0.38 + s * 0.7);
      const k = Math.min(1, Math.max(0, sweep * 0.8 + (P.hue - 0.5) * 0.9));
      const shimmer = 0.84 + 0.16 * fbm(t * 1.05 + s * 2.7);
      return [...mix(VIOLET, GREEN, k).map(v => v * 0.62 * shimmer), 0];
    },
    throb: (t, s) => {
      const p = Math.pow(0.5 + 0.5 * Math.sin(t * 7.4 + s * 0.4), 2);
      return [...mix(VIOLET, GREEN, P.hue * 0.5).map(v => v * (0.20 + 0.80 * p)), 0];
    },
    strobe: (t, s) => {
      if (P.soft) {                       // ~7 Hz hard strobe is a seizure risk
        const l = 0.34 + 0.44 * (0.5 + 0.5 * Math.sin(t * 3.1 + s));
        return [0.10 * l, 0.10 * l, 0.14 * l, 1.00 * l];
      }
      const on = (Math.sin(t * 44 + s) > 0) ? 1 : 0.06;
      return [0.12 * on, 0.12 * on, 0.18 * on, 1.00 * on];
    },
    chill: (t, s) => {
      const b = 0.5 + 0.5 * Math.sin(t * 0.50 + s * 1.1);
      return [...mix(VIOLET, GREEN, P.hue * 0.35).map(v => v * (0.14 + 0.16 * b)), 0];
    },
    blood: (t, s) => {
      // Near-dark deep red smoulder — the floor under the crypt heartbeat.
      const n = fbm(t * 0.35 + s * 1.7);
      const l = 0.045 + 0.05 * n;
      return [1.00 * l, 0.02 * l, 0.01 * l, 0];
    }
  };

  /* RGBW → screen colour: the white die is a real emitter, summed in. */
  const toScreen = c => [
    Math.min(1, c[0] + c[3] * WARM_W[0]),
    Math.min(1, c[1] + c[3] * WARM_W[1]),
    Math.min(1, c[2] + c[3] * WARM_W[2])
  ];


export { EFFECTS, toScreen, P, fbm, vnoise, hash };
