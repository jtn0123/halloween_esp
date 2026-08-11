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

export async function detectOnsets(audio: AudioBuffer): Promise<Record<string, Onset[]>> {
  const out: Record<string, Onset[]> = {};
  for (const [name, lo, hi, gap] of BANDS) {
    const oc = new OfflineAudioContext(1, audio.length, audio.sampleRate);
    const src = oc.createBufferSource(); src.buffer = audio;
    const hp = oc.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = lo;
    const lp = oc.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = hi;
    src.connect(hp); hp.connect(lp); lp.connect(oc.destination); src.start();
    const band = (await oc.startRendering()).getChannelData(0);

    // Short-time RMS envelope, then positive difference = onset strength.
    // Every index below is in range by construction (n is a floor division of
    // the buffer length), so the `!` is bookkeeping for noUncheckedIndexedAccess
    // rather than a claim the maths could not make on its own.
    const hop = 512, sr = audio.sampleRate, n = Math.floor(band.length / hop);
    const env = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let s = 0;
      for (let j = i * hop; j < (i + 1) * hop; j++) s += band[j]! * band[j]!;
      env[i] = Math.log1p(Math.sqrt(s / hop) * 100);
    }
    const flux = new Float32Array(n);
    for (let i = 1; i < n; i++) flux[i] = Math.max(0, env[i]! - env[i - 1]!);
    let mx = 0; for (const v of flux) mx = Math.max(mx, v);
    if (!mx) continue;

    const hits: Onset[] = [], minGap = gap * sr / hop;
    const w = Math.max(3, Math.round(0.5 * sr / hop));
    let last = -1e9;
    for (let i = 1; i < n - 1; i++) {
      let sum = 0, c = 0;
      for (let j = Math.max(0, i - w); j < Math.min(n, i + w); j++) { sum += flux[j]!; c++; }
      const thr = sum / c + 0.12 * mx;
      if (flux[i]! < thr || flux[i]! < flux[i - 1]! || flux[i]! < flux[i + 1]!) continue;
      if (i - last < minGap) continue;
      hits.push([+(i * hop / sr).toFixed(3), +(flux[i]! / mx).toFixed(3)]);
      last = i;
    }
    if (hits.length) out[name] = hits;
  }
  return out;
}
