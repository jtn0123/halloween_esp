/**
 * The live synth — a WebAudio port of the previewer's note engine.
 *
 * Nothing here is built until `arm()` is called from inside a user gesture: a
 * browser will not hand out a running AudioContext before one, and constructing
 * a suspended context on page load just leaves a dead graph lying around.
 *
 * The maths in the voices is deliberately unchanged from the original — the
 * frequencies, envelope times and rank amplitudes are what make an organ sound
 * like an organ rather than an organ patch, and they were tuned by ear.
 */

import type { EffectParams } from "./effects.js";
import { defaultParams } from "./effects.js";

/**
 * Everything that only exists after `arm()`. Bundling it into one object means
 * a single null check at the top of a voice proves every node is live, instead
 * of a non-null assertion on each node in turn.
 */
interface Graph {
  ctx: AudioContext;
  /* `master` is the summing bus, NOT the volume control — everything lands
     here, then goes through a 28 Hz highpass (inaudible sub just eats
     headroom) and a limiter before `outGain` sets level. Without the limiter
     two overlapping organ chords clip at ~1.6x full scale. */
  master: GainNode;
  outGain: GainNode;
  showBus: GainNode;
  reverbIn: GainNode;
  wetGain: GainNode;
  tremAmt: GainNode;
  noiseBuf: AudioBuffer;
  windGain: GainNode | null;
}

/** A stone hall, faked: exponentially-decaying noise as an impulse response.
    This is the single biggest thing separating "organ patch" from "organ". */
function makeIR(ctx: AudioContext, secs: number, decay: number): AudioBuffer {
  const len = Math.floor(ctx.sampleRate * secs);
  const b = ctx.createBuffer(2, len, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const d = b.getChannelData(c);
    for (let i = 0; i < len; i++) {
      d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
    }
  }
  return b;
}

function makeNoise(ctx: AudioContext, secs: number): AudioBuffer {
  const b = ctx.createBuffer(1, ctx.sampleRate * secs, ctx.sampleRate);
  const d = b.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  return b;
}

const env = (g: GainNode, t0: number, peak: number, atk: number, dec: number): void => {
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(peak, t0 + atk);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dec);
};

/* ── Note engine ─────────────────────────────────────────────────
   Semitones from A3. Original material in the haunted-parlour idiom:
   A minor, 3/4, raised 7th on the dominant. Not a transcription.     */
const NT = (s: number): number => 220 * Math.pow(2, s / 12);

type VoiceKind = "box" | "piano" | "pipe";

/* 32' 16' 8' 4' 2⅔' 2' 1⅗', plus a detuned celeste. The two sub ranks
   are where the weight lives; the upper chorus rides the Stops control,
   so pulling stops in leaves a dark, hollow 16-foot tone. */
const RANKS: ReadonlyArray<readonly [mult: number, amp: number, upper: number]> = [
  [0.25, 0.30, 0], [0.5, 0.86, 0], [1, 1.00, 0], [1.004, 0.40, 0],
  [2, 0.40, 1], [3, 0.22, 1], [4, 0.13, 1], [6, 0.06, 1],
];

export class Synth {
  /** Live tuning knobs. Only `stops` is read by the synth, but the previewer
      assigns the same object it gives the light effects, so a slider move is
      seen here without any plumbing. Defaults stand in until it does. */
  params: EffectParams = defaultParams();

  private g: Graph | null = null;
  private volume = 0.6;
  /* The graph is built muted by default; audio only ever starts on purpose. */
  private isMuted = true;

  get armed(): boolean {
    return this.g !== null;
  }

  /** Build the graph. Idempotent — a second call is a no-op, so this can sit
      on every gesture handler without guarding at the call site. */
  arm(): void {
    if (this.g) return;
    const Ctor: typeof AudioContext | undefined =
      window.AudioContext ??
      (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();

    const master = ctx.createGain(); master.gain.value = 1;    // summing bus
    const hp = ctx.createBiquadFilter();
    hp.type = "highpass"; hp.frequency.value = 28;             // dump wasted sub
    const lim = ctx.createDynamicsCompressor();
    lim.threshold.value = -9; lim.knee.value = 6;
    lim.ratio.value = 14; lim.attack.value = 0.004; lim.release.value = 0.28;
    // Respect mute at construction — the graph is built muted by default.
    const outGain = ctx.createGain();
    outGain.gain.value = this.isMuted ? 0 : this.volume;
    master.connect(hp); hp.connect(lim); lim.connect(outGain);
    outGain.connect(ctx.destination);

    const noiseBuf = makeNoise(ctx, 3);

    const conv = ctx.createConvolver();
    conv.buffer = makeIR(ctx, 3.4, 2.4);
    const reverbIn = ctx.createGain(); reverbIn.gain.value = 1;
    const wetGain = ctx.createGain(); wetGain.gain.value = 0.58;
    reverbIn.connect(conv); conv.connect(wetGain); wetGain.connect(master);

    // Shared tremulant — one LFO feeding every pipe voice's gain.
    const trem = ctx.createOscillator();
    trem.type = "sine"; trem.frequency.value = 5.1;
    const tremAmt = ctx.createGain(); tremAmt.gain.value = 0.046; // multiplicative depth
    trem.connect(tremAmt); trem.start();

    // showBus is replaced immediately by newShowBus(); this placeholder only
    // exists so Graph has no nullable field that every voice would re-check.
    const showBus = ctx.createGain();
    this.g = {
      ctx, master, outGain, showBus, reverbIn, wetGain, tremAmt,
      noiseBuf, windGain: null,
    };
    this.newShowBus();
  }

  /* Every one-shot cue routes through showBus. Cutting a scene short means
     dropping the bus wholesale — cancelling individually scheduled notes
     would mean tracking every oscillator we ever created. */
  newShowBus(): void {
    const g = this.g;
    if (!g) return;
    try { g.showBus.disconnect(); } catch { /* already gone */ }
    const bus = g.ctx.createGain();
    bus.gain.value = 1;
    bus.connect(g.master);        // dry
    bus.connect(g.reverbIn);      // wet
    g.showBus = bus;
  }

  /* ── Mixer ───────────────────────────────────────────────────────
     Volume and mute are remembered before `arm()` and applied when the graph
     is built; reverb and tremolo are not, matching the original — their
     sliders were plain writes onto nodes that did not exist yet. */

  setVolume(v: number): void {
    this.volume = v;
    this.applyVolume();
  }

  setMuted(m: boolean): void {
    this.isMuted = m;
    this.applyVolume();
  }

  private applyVolume(): void {
    if (this.g) this.g.outGain.gain.value = this.isMuted ? 0 : this.volume;
  }

  /** Wet mix, 0..1. */
  setReverb(wet: number): void {
    if (this.g) this.g.wetGain.gain.value = wet;
  }

  /** Tremulant depth, 0..1, scaled to the 0..0.14 the pipe voices expect —
      past that the multiplication starts chopping the note rather than
      breathing it. */
  setTremolo(depth: number): void {
    if (this.g) this.g.tremAmt.gain.value = depth * 0.14;
  }

  /** Upper-chorus level, 0..1. Read by the pipe voice at note time, so it
      only affects notes started after the change. */
  setStops(v: number): void {
    this.params.stops = v;
  }

  /* ── Wind ────────────────────────────────────────────────────────── */

  startWind(): void {
    const g = this.g;
    if (!g) return;
    const { ctx } = g;
    if (g.windGain) {                     // already built — just bring it back up
      const gain = g.windGain.gain, now = ctx.currentTime;
      gain.cancelScheduledValues(now);
      gain.setValueAtTime(Math.max(0.0001, gain.value), now);
      gain.exponentialRampToValueAtTime(0.10, now + 1.2);
      return;
    }
    const src = ctx.createBufferSource(); src.buffer = g.noiseBuf; src.loop = true;
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass"; bp.frequency.value = 420; bp.Q.value = 0.7;
    const lfo = ctx.createOscillator(); lfo.type = "sine"; lfo.frequency.value = 0.09;
    const lfoAmt = ctx.createGain(); lfoAmt.gain.value = 240;
    lfo.connect(lfoAmt); lfoAmt.connect(bp.frequency);
    // Swell is a separate multiplying stage. Summed into windGain it went
    // negative once the envelope ducked, so a blackout left the wind
    // wobbling around zero instead of stopping.
    const swell = ctx.createOscillator(); swell.type = "sine"; swell.frequency.value = 0.06;
    const swellAmt = ctx.createGain(); swellAmt.gain.value = 0.38;
    const windSwell = ctx.createGain(); windSwell.gain.value = 1;
    swell.connect(swellAmt); swellAmt.connect(windSwell.gain);

    const windGain = ctx.createGain(); windGain.gain.value = 0.0001;
    windGain.gain.exponentialRampToValueAtTime(0.10, ctx.currentTime + 2.5);
    src.connect(bp); bp.connect(windGain); windGain.connect(windSwell);
    windSwell.connect(g.master);
    src.start(); lfo.start(); swell.start();
    g.windGain = windGain;
  }

  /** Ramp the wind down without tearing the nodes out — `startWind()` brings
      the same source back up, so the noise never restarts from a fixed point.
      The default matches a blackout; the transport's pause used 0.4. */
  stopWind(fadeSec = 0.6): void {
    const g = this.g;
    if (!g?.windGain) return;
    g.windGain.gain.exponentialRampToValueAtTime(0.0001, g.ctx.currentTime + fadeSec);
  }

  /* ── Voices ──────────────────────────────────────────────────────── */

  private voice(
    g: Graph, kind: VoiceKind, f: number, t0: number, dur: number, vel: number,
  ): void {
    const { ctx } = g;
    if (kind === "box") {                       // music box / celesta
      [1, 3.01, 5.42].forEach((m, i) => {
        const o = ctx.createOscillator(); o.type = "sine"; o.frequency.value = f * m;
        const gn = ctx.createGain();
        gn.gain.setValueAtTime(0.0001, t0);
        gn.gain.exponentialRampToValueAtTime(vel / (i * 2.2 + 1), t0 + 0.004);
        gn.gain.exponentialRampToValueAtTime(0.0001, t0 + dur / (i * 0.8 + 1));
        o.connect(gn); gn.connect(g.showBus); o.start(t0); o.stop(t0 + dur + 0.05);
      });
      return;
    }
    const gn = ctx.createGain();
    if (kind === "piano") {
      gn.connect(g.showBus);
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 2400;
      const o1 = ctx.createOscillator(); o1.type = "triangle"; o1.frequency.value = f;
      const o2 = ctx.createOscillator(); o2.type = "sine"; o2.frequency.value = f * 2.004;
      const h = ctx.createGain(); h.gain.value = 0.28;
      o1.connect(lp); o2.connect(h); h.connect(lp); lp.connect(gn);
      gn.gain.setValueAtTime(0.0001, t0);
      gn.gain.exponentialRampToValueAtTime(vel, t0 + 0.006);
      gn.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      o1.start(t0); o2.start(t0); o1.stop(t0 + dur + 0.05); o2.stop(t0 + dur + 0.05);
      return;
    }
    // "pipe" — drawbar ranks
    const stops = this.params.stops;
    const atk = Math.min(1.7, Math.max(0.07, dur * 0.20));
    const rel = Math.min(2.6, Math.max(0.35, dur * 0.34));

    RANKS.forEach(([m, amp, upper]) => {
      const fr = f * m * (m === 1 ? 1 : 1.0012);
      if (fr < 24 || fr > 11000) return;        // below hearing / above useful
      const a = amp * (upper ? stops : 1);
      if (a < 0.012) return;
      const o = ctx.createOscillator(); o.type = "sine"; o.frequency.value = fr;
      const rg = ctx.createGain(); rg.gain.value = a;
      o.connect(rg); rg.connect(gn); o.start(t0); o.stop(t0 + dur + 0.25);
    });

    // Chiff only on a quick attack — a slow swell has no breath in it.
    if (atk < 0.22) {
      const ch = ctx.createBufferSource(); ch.buffer = g.noiseBuf;
      const chf = ctx.createBiquadFilter(); chf.type = "bandpass";
      chf.frequency.value = Math.min(9000, f * 5); chf.Q.value = 3;
      const chg = ctx.createGain();
      chg.gain.setValueAtTime(Math.max(0.0001, vel * 0.26), t0);
      chg.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.055);
      ch.connect(chf); chf.connect(chg); chg.connect(g.showBus);
      ch.start(t0); ch.stop(t0 + 0.09);
    }

    gn.gain.setValueAtTime(0.0001, t0);
    gn.gain.exponentialRampToValueAtTime(vel, t0 + atk);
    gn.gain.setValueAtTime(vel, t0 + Math.max(atk + 0.05, dur - rel));
    gn.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

    // Tremulant multiplies the envelope rather than adding to it. Summed
    // into gn.gain it drove the value negative on release — phase flip, and
    // a 5 Hz buzz riding every note tail.
    const tg = ctx.createGain(); tg.gain.value = 1;
    g.tremAmt.connect(tg.gain);
    gn.connect(tg); tg.connect(g.showBus);
  }

  /* ── Cues ─────────────────────────────────────────────────────────
     Each entry schedules a whole cue up front against the context clock, so
     nothing depends on timers firing on time once it has started. */
  private readonly SYNTH: Record<string, ((g: Graph) => void) | undefined> = {
    /* 8 bars, 3/4. Bass on the downbeat, triad on 2 and 3, music box on top. */
    waltz: (g) => {
      const t0 = g.ctx.currentTime + 0.05, B = 0.52;
      const prog: ReadonlyArray<readonly [number, readonly number[]]> = [
        [0, [0, 3, 7]], [0, [0, 3, 7]], [5, [0, 3, 7]], [7, [0, 4, 7]],
        [0, [0, 3, 7]], [8, [0, 4, 7]], [7, [0, 4, 7]], [0, [0, 3, 7]],
      ];
      const mel: ReadonlyArray<number | null> = [
        12, 15, 19, 17, 15, 14, 17, 20, 17, 19, 14, 11,
        12, 15, 19, 20, 19, 17, 19, 14, 11, 12, null, null,
      ];
      prog.forEach(([root, iv], bar) => {
        const bt = t0 + bar * 3 * B;
        this.voice(g, "piano", NT(root - 24), bt, 1.35, 0.22);
        for (let k = 1; k < 3; k++) {
          iv.forEach(s => this.voice(g, "piano", NT(root + s - 12), bt + k * B, 0.75, 0.07));
        }
      });
      mel.forEach((s, i) => {
        if (s !== null) this.voice(g, "box", NT(s), t0 + i * B, 1.7, 0.15);
      });
    },

    /* Procession — D minor. Four chords, ~7.5 s each, overlapping so one
       never fully releases before the next swells in. The second chord is
       the Neapolitan (bII): a major triad a semitone above the tonic, which
       is the oldest trick in the book for making a room feel wrong.        */
    organ: (g) => {
      const t0 = g.ctx.currentTime + 0.1;
      const CH: ReadonlyArray<{ ped: number; notes: readonly number[] }> = [
        { ped: -19, notes: [5, 8, 12] },        // i     — D minor
        { ped: -18, notes: [6, 10, 13] },       // bII   — E flat major
        { ped: -19, notes: [5, 8, 12] },        // i
        { ped: -12, notes: [12, 13, 16, 19] },  // V7b9  — A, B flat, C#, E
      ];
      CH.forEach((c, i) => {
        const bt = t0 + i * 6.6;                // 7.5 s chords, 0.9 s overlap
        this.voice(g, "pipe", NT(c.ped), bt, 7.5, 0.078);
        this.voice(g, "pipe", NT(c.ped + 12), bt, 7.5, 0.038);
        c.notes.forEach(s => this.voice(g, "pipe", NT(s - 12), bt, 7.5, 0.030));
      });
    },

    /* Descent — a 32-foot pedal holds the whole way while the upper voices
       walk down chromatically. Nothing moves fast; the dread is in the drift. */
    descent: (g) => {
      const t0 = g.ctx.currentTime + 0.1;
      this.voice(g, "pipe", NT(-19), t0, 25.5, 0.090);   // pedal D, held throughout
      this.voice(g, "pipe", NT(-31), t0, 25.5, 0.060);   // 32' underneath
      const cls: ReadonlyArray<readonly number[]> =
        [[17, 20, 24], [16, 19, 23], [15, 18, 22], [14, 17, 21]];
      cls.forEach((cl, i) => {
        const bt = t0 + 1.2 + i * 5.8;
        cl.forEach(s => this.voice(g, "pipe", NT(s - 12), bt, 6.6, 0.030));
      });
      // Final diminished pile-up: D F Ab B, all at once, very long.
      [5, 8, 11, 14].forEach(s => this.voice(g, "pipe", NT(s - 12), t0 + 20.4, 6.4, 0.034));
    },

    musicbox: (g) => {
      const t0 = g.ctx.currentTime + 0.05;
      [24, 22, 19, 15, 12].forEach((s, i) =>
        this.voice(g, "box", NT(s), t0 + i * 0.30, 1.9, 0.17));
    },

    thunder: (g) => {
      const { ctx } = g;
      const t0 = ctx.currentTime;
      const s = ctx.createBufferSource(); s.buffer = g.noiseBuf;
      const lp = ctx.createBiquadFilter(); lp.type = "lowpass";
      lp.frequency.setValueAtTime(900, t0);
      lp.frequency.exponentialRampToValueAtTime(70, t0 + 2.6);
      const gn = ctx.createGain(); env(gn, t0, 0.85, 0.05, 3.0);
      s.connect(lp); lp.connect(gn); gn.connect(g.showBus);
      s.start(t0); s.stop(t0 + 3.1);
      const o = ctx.createOscillator(); o.type = "sine";
      o.frequency.setValueAtTime(50, t0);
      o.frequency.exponentialRampToValueAtTime(27, t0 + 2.4);
      const og = ctx.createGain(); env(og, t0, 0.55, 0.14, 2.9);
      o.connect(og); og.connect(g.showBus); o.start(t0); o.stop(t0 + 3);
    },

    creak: (g) => {
      const { ctx } = g;
      const t0 = ctx.currentTime;
      const o = ctx.createOscillator(); o.type = "sawtooth";
      for (let i = 0; i < 26; i++) {
        o.frequency.setValueAtTime(70 + Math.random() * 90, t0 + i * 0.035);
      }
      const bp = ctx.createBiquadFilter();
      bp.type = "bandpass"; bp.frequency.value = 760; bp.Q.value = 7;
      const gn = ctx.createGain(); env(gn, t0, 0.30, 0.06, 1.0);
      o.connect(bp); bp.connect(gn); gn.connect(g.showBus);
      o.start(t0); o.stop(t0 + 1.05);
    },

    shriek: (g) => {
      const { ctx } = g;
      const t0 = ctx.currentTime;
      const s = ctx.createBufferSource(); s.buffer = g.noiseBuf;
      const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.Q.value = 9;
      bp.frequency.setValueAtTime(700, t0);
      bp.frequency.exponentialRampToValueAtTime(2900, t0 + 0.42);
      bp.frequency.exponentialRampToValueAtTime(900, t0 + 0.8);
      const gn = ctx.createGain(); env(gn, t0, 0.34, 0.04, 0.85);
      s.connect(bp); bp.connect(gn); gn.connect(g.showBus);
      s.start(t0); s.stop(t0 + 0.9);
    },

    toll: (g) => {
      const { ctx } = g;
      const t0 = ctx.currentTime;
      [1, 2.76, 5.4, 8.9].forEach((m, i) => {
        const o = ctx.createOscillator(); o.type = "sine"; o.frequency.value = 138 * m;
        const gn = ctx.createGain();
        env(gn, t0, 0.24 / (i + 1), 0.01, 4.5 / (i * 0.6 + 1));
        o.connect(gn); gn.connect(g.showBus); o.start(t0); o.stop(t0 + 5);
      });
    },
  };

  /** Play one named cue. Unknown names are ignored — a scene naming a sound
      the synth has no voice for should fall silent, not throw mid-show. */
  play(name: string): void {
    const g = this.g;
    if (!g) return;
    if (name === "wind") { this.startWind(); return; }
    const fn = this.SYNTH[name];
    if (fn) fn(g);
  }
}
