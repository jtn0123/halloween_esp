/**
 * In-browser onset detection. Same idea as tools/analyze.py — energy rises
 * per frequency band — but built from biquads rendered offline rather than
 * an FFT, since the browser gives us filters for free and no FFT at all.
 *
 * This lives apart from tracks.ts only because that file has a hard 500-line
 * ceiling; the seam is clean, since nothing here touches the DOM or the server.
 */

/** [time in seconds, strength 0..1], both rounded to 3 dp for a tidy YAML. */
export type Onset = [number, number];

/** Band name, low edge Hz, high edge Hz, minimum spacing between hits (s). */
type Band = [string, number, number, number];

const BANDS: readonly Band[] = [
  ["onset_low", 20, 200, 0.16], ["onset_mid", 200, 2000, 0.11],
  ["onset_high", 2000, 16000, 0.09],
];

/**
 * Find onsets in one band of samples.
 *
 * Short-time RMS envelope, positive difference for onset strength, then peak
 * picking against a running local mean. A fixed threshold fails on real music
 * — a quiet intro and a loud chorus need different bars — so the threshold
 * tracks the material.
 *
 * `minGapSec` is a refractory period: the shortest believable spacing between
 * two separate hits in this band. Without it a single kick smeared over a few
 * frames reads as a burst.
 */
export function pickOnsets(
  band: Float32Array, sampleRate: number, minGapSec: number, hop = 512,
): Onset[] {
  const n = Math.floor(band.length / hop);
  if (n < 3) return [];

  const env = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = i * hop; j < (i + 1) * hop; j++) s += band[j]! * band[j]!;
    env[i] = Math.log1p(Math.sqrt(s / hop) * 100);
  }
  const flux = new Float32Array(n);
  for (let i = 1; i < n; i++) flux[i] = Math.max(0, env[i]! - env[i - 1]!);
  let mx = 0; for (const v of flux) mx = Math.max(mx, v);
  if (!mx) return [];

  const hits: Onset[] = [];
  const minGap = minGapSec * sampleRate / hop;
  const w = Math.max(3, Math.round(0.5 * sampleRate / hop));
  let last = -1e9;
  for (let i = 1; i < n - 1; i++) {
    let sum = 0, c = 0;
    for (let j = Math.max(0, i - w); j < Math.min(n, i + w); j++) { sum += flux[j]!; c++; }
    const thr = sum / c + 0.12 * mx;
    if (flux[i]! < thr || flux[i]! < flux[i - 1]! || flux[i]! < flux[i + 1]!) continue;
    if (i - last < minGap) continue;
    hits.push([+(i * hop / sampleRate).toFixed(3), +(flux[i]! / mx).toFixed(3)]);
    last = i;
  }
  return hits;
}

/**
 * Full-range loudness over time — the browser twin of the `env` field the
 * studio's waveform endpoint returns (tools/analyze.py envelope()). Same
 * output contract: [seconds, level 0..1] at ~6 Hz, log-compressed, scaled to
 * the material's own range, near-silent points dropped.
 */
export function loudnessEnvelope(
  samples: Float32Array, sampleRate: number, hz = 6,
): Onset[] {
  const hop = 512;
  const n = Math.floor(samples.length / hop);
  if (n < 3) return [];
  const env = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = i * hop; j < (i + 1) * hop; j++) s += samples[j]! * samples[j]!;
    env[i] = Math.log1p(Math.sqrt(s / hop) * 50);
  }
  let lo = Infinity, hi = -Infinity;
  for (const v of env) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (hi - lo < 1e-9) return [];
  const step = Math.max(1, Math.round(sampleRate / hop / hz));
  const out: Onset[] = [];
  for (let i = 0; i < n; i += step) {
    const v = (env[i]! - lo) / (hi - lo);
    if (v > 0.02) out.push([+(i * hop / sampleRate).toFixed(3), +v.toFixed(3)]);
  }
  return out;
}

export async function detectOnsets(audio: AudioBuffer): Promise<Record<string, Onset[]>> {
  const out: Record<string, Onset[]> = {};
  for (const [name, lo, hi, gap] of BANDS) {
    const oc = new OfflineAudioContext(1, audio.length, audio.sampleRate);
    const src = oc.createBufferSource(); src.buffer = audio;
    const hp = oc.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = lo;
    const lp = oc.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = hi;
    src.connect(hp); hp.connect(lp); lp.connect(oc.destination); src.start();
    const band = (await oc.startRendering()).getChannelData(0);

    // The detection itself is pure — no Web Audio, no DOM — so it lives in
    // its own function and can be tested in node. Everything above this line
    // is just getting band-filtered samples out of the browser.
    const hits = pickOnsets(band, audio.sampleRate, gap);
    if (hits.length) out[name] = hits;
  }
  return out;
}
