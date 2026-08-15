/**
 * Stems panel — listening to the split, and auditing what mono analysis loses.
 *
 * Demucs pulls a track apart into voice and background; this panel puts the
 * three layers side by side (voice / background / combined), each with its own
 * play button and a strip of peaks with the detector's onsets ticked under
 * them. The combined row is the file the castle actually plays — the split
 * only ever teaches the LIGHT CUES, never the device.
 *
 * The channel selector is the audit tool. "Both" is the mono downmix — the
 * exact signal the pipeline analyses today. Left and Right re-draw every strip
 * from that channel alone AND re-route playback so you hear only that side.
 * Any hit that exists in a single channel but not in the mono analysis is
 * drawn as a full-height white tick and counted in the row's caption: that
 * tick is a sound the current pipeline never saw.
 */

import { api, type StemChannel, type StemsResponse } from "./api.js";
import { BANDS } from "./bands.js";

export interface StemsDeps {
  /** Fired when a stem starts playing, so the host can stop its audition. */
  onPlay: () => void;
}

export interface StemsApi {
  el: HTMLElement;
  /** Point the panel at a track, or null to clear it. */
  show(trackId: string | null): void;
  /** Stop playback. Safe when nothing is playing. */
  stop(): void;
  destroy(): void;
}

type Channel = "left" | "both" | "right";
type Layer = "vocals" | "backing" | "combined";

const LAYERS: readonly { key: Layer; label: string; hint: string; ink: string }[] = [
  { key: "vocals", label: "Voice", ink: "#ff5fa2",
    hint: "only when someone sings — the layer a door could sing with" },
  { key: "backing", label: "Background", ink: "#5f9fff",
    hint: "drums and synths — the rhythm the towers keep" },
  { key: "combined", label: "Combined", ink: "#ffb75f",
    hint: "the original file, untouched — what the castle plays" },
];

/** A channel onset with no mono ("both") onset within this window was never
 *  seen by the pipeline. 90 ms is just past the detector's own frame jitter. */
const MISS_WINDOW = 0.09;

const btn = (label: string): HTMLButtonElement => {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  return b;
};

/** Onsets in `chan` that have no counterpart in the mono analysis. */
function missedTimes(chan: StemChannel, mono: StemChannel | undefined,
                     band: string): number[] {
  const seen = (mono?.onsets[band] ?? []).map(h => h[0]).sort((a, z) => a - z);
  const out: number[] = [];
  for (const [t] of chan.onsets[band] ?? []) {
    // seen is sorted and short; a linear probe is cheaper than being clever.
    if (!seen.some(s => Math.abs(s - t) <= MISS_WINDOW)) out.push(t);
  }
  return out;
}

export function createStemsView(deps: StemsDeps): StemsApi {
  const el = document.createElement("section");
  el.className = "stems";
  el.hidden = true;

  const head = document.createElement("div");
  head.className = "stems-head";
  const title = document.createElement("h3");
  title.textContent = "Voice / background split";
  const split = btn("Split voices");
  split.title = "Run Demucs on this track (~30 s on this Mac). The castle "
    + "keeps playing the combined file; only the light analysis changes.";
  const seg = document.createElement("div");
  seg.className = "stems-seg";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "analysis channel");
  const segBtns: Record<Channel, HTMLButtonElement> = {
    left: btn("Left"), both: btn("Both"), right: btn("Right"),
  };
  segBtns.both.title = "The mono downmix — exactly what the pipeline analyses today.";
  segBtns.left.title = "This side only, in your ears and in the strips. "
    + "White ticks are hits the mono analysis never saw.";
  segBtns.right.title = segBtns.left.title.replace("This side", "The right side");
  seg.append(segBtns.left, segBtns.both, segBtns.right);
  head.append(title, seg, split);

  const note = document.createElement("p");
  note.className = "stems-note";

  const rows: Record<Layer, {
    row: HTMLElement; play: HTMLButtonElement; cv: HTMLCanvasElement;
    cap: HTMLElement; audio: HTMLAudioElement;
  }> = {} as never;
  const body = document.createElement("div");
  for (const L of LAYERS) {
    const row = document.createElement("div");
    row.className = "stems-row";
    const h = document.createElement("div");
    h.className = "stems-row-head";
    const dot = document.createElement("span");
    dot.className = "stems-dot";
    dot.style.background = L.ink;
    const name = document.createElement("b");
    name.textContent = L.label;
    const cap = document.createElement("span");
    cap.className = "stems-cap";
    const play = btn("Play");
    h.append(dot, name, play, cap);
    const cv = document.createElement("canvas");
    cv.className = "stems-strip";
    cv.title = L.hint;
    const audio = new Audio();
    audio.preload = "none";
    audio.muted = true;                // the Play press is the consent
    row.append(h, cv);
    body.append(row);
    rows[L.key] = { row, play, cv, cap, audio };
  }
  el.append(head, note, body);

  let trackId: string | null = null;
  let data: StemsResponse | null = null;
  let channel: Channel = "both";
  let playing: Layer | null = null;
  let raf = 0;
  let token = 0;                       // orphans stale fetches and poll loops

  const say = (msg: string, err = false): void => {
    note.textContent = msg;
    note.classList.toggle("err", err);
  };

  /* ── Channel routing ──────────────────────────────────────────────────
     One splitter/merger per layer element, four gains between them. Solo
     sends the chosen side to BOTH ears — a one-eared listen tells you less
     about the material than it does about your headphones. */
  let ctx: AudioContext | null = null;
  const gains: Partial<Record<Layer, GainNode[]>> = {};   // [ll, lr, rl, rr]

  function ensureGraph(): void {
    if (ctx) return;
    ctx = new AudioContext();
    for (const L of LAYERS) {
      const a = rows[L.key].audio;
      const src = ctx.createMediaElementSource(a);
      const sp = ctx.createChannelSplitter(2);
      const mg = ctx.createChannelMerger(2);
      const g = [0, 0, 0, 0].map(() => ctx!.createGain());
      src.connect(sp);
      sp.connect(g[0]!, 0); g[0]!.connect(mg, 0, 0);   // L → left ear
      sp.connect(g[1]!, 0); g[1]!.connect(mg, 0, 1);   // L → right ear
      sp.connect(g[2]!, 1); g[2]!.connect(mg, 0, 0);   // R → left ear
      sp.connect(g[3]!, 1); g[3]!.connect(mg, 0, 1);   // R → right ear
      mg.connect(ctx.destination);
      gains[L.key] = g;
    }
    applyRouting();
  }

  function applyRouting(): void {
    const on: Record<Channel, number[]> = {
      both: [1, 0, 0, 1], left: [1, 1, 0, 0], right: [0, 0, 1, 1],
    };
    for (const g of Object.values(gains))
      on[channel].forEach((v, i) => { g[i]!.gain.value = v; });
  }

  /* ── Drawing ── */

  function draw(layer: Layer): void {
    const r = rows[layer];
    const d = data?.layers?.[layer]?.[channel];
    const mono = data?.layers?.[layer]?.both;
    const dur = data?.duration ?? 1;
    const dpr = window.devicePixelRatio || 1;
    const w = r.cv.clientWidth, h = r.cv.clientHeight;
    if (!w) return;
    r.cv.width = w * dpr; r.cv.height = h * dpr;
    const g = r.cv.getContext("2d");
    if (!g || !d) return;
    g.scale(dpr, dpr);
    const peakH = h - 14;
    g.fillStyle = LAYERS.find(l => l.key === layer)!.ink;
    g.globalAlpha = 0.85;
    const n = d.peaks.length || 1;
    for (let i = 0; i < n; i++) {
      const bh = Math.max(1, (d.peaks[i] ?? 0) * peakH);
      g.fillRect(i / n * w, (peakH - bh) / 2 + 2, Math.max(1, w / n - 0.4), bh);
    }
    g.globalAlpha = 1;
    let missed = 0;
    for (const b of BANDS) {
      g.fillStyle = b.ink;
      for (const [t] of d.onsets[b.name] ?? [])
        g.fillRect(t / dur * w, h - 11, 1.5, 9);
      if (channel !== "both") {
        // The audit marks: this channel heard it, the pipeline did not.
        // Along the TOP edge, opposite the band ticks — full-height bars
        // drowned the waveform on real music (hundreds of them).
        g.fillStyle = "#fff";
        for (const t of missedTimes(d, mono, b.name)) {
          g.fillRect(t / dur * w, 1, 1.5, 8);
          missed++;
        }
      }
    }
    const total = Object.values(d.onsets).reduce((s, v) => s + v.length, 0);
    r.cap.textContent = channel === "both"
      ? `${total} hits — the pipeline's own picture`
      : `${total} hits · ${missed === 0 ? "all seen by mono analysis"
          : `${missed} the mono analysis missed`}`;
    if (playing === layer) {
      g.fillStyle = "#fff";
      g.fillRect(rows[layer].audio.currentTime / dur * w, 0, 1.5, h);
    }
  }

  const drawAll = (): void => { for (const L of LAYERS) draw(L.key); };

  function frame(): void {
    if (!playing) return;
    draw(playing);
    raf = requestAnimationFrame(frame);
  }

  /* ── Playback ── */

  function stop(): void {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    for (const L of LAYERS) {
      rows[L.key].audio.pause();
      rows[L.key].audio.muted = true;
      rows[L.key].play.textContent = "Play";
    }
    const was = playing;
    playing = null;
    if (was) draw(was);                // clear the playhead
  }

  function play(layer: Layer): void {
    if (playing === layer) { stop(); return; }
    const from = playing ? rows[playing].audio.currentTime : 0;
    stop();
    deps.onPlay();
    ensureGraph();
    void ctx?.resume();
    const a = rows[layer].audio;
    // Layer-switching keeps the position, so you compare the same bar.
    if (from) { try { a.currentTime = from; } catch { /* metadata pending */ } }
    a.muted = false;
    a.volume = 0.8;
    playing = layer;
    void a.play().catch(() => { stop(); say("Could not play that stem.", true); });
    rows[layer].play.textContent = "Stop";
    raf = requestAnimationFrame(frame);
  }

  for (const L of LAYERS) {
    rows[L.key].play.addEventListener("click", () => play(L.key));
    rows[L.key].audio.addEventListener("ended", stop);
    rows[L.key].cv.addEventListener("click", e => {
      const dur = data?.duration;
      if (!dur) return;
      const frac = e.offsetX / rows[L.key].cv.clientWidth;
      const target = playing ?? L.key;
      try { rows[target].audio.currentTime = frac * dur; } catch { /* not loaded */ }
      if (!playing) play(target);
    });
  }

  /* ── Channel selection ── */

  function setChannel(c: Channel): void {
    channel = c;
    for (const [k, b] of Object.entries(segBtns))
      b.setAttribute("aria-pressed", String(k === c));
    applyRouting();
    drawAll();
  }
  for (const [k, b] of Object.entries(segBtns))
    b.addEventListener("click", () => setChannel(k as Channel));

  /* ── Load / split ── */

  function ready(d: StemsResponse): void {
    data = d;
    body.hidden = false;
    seg.hidden = false;
    split.textContent = "Re-split";
    split.disabled = false;
    const id = trackId ?? "";
    rows.vocals.audio.src = `/api/stem/${encodeURIComponent(id)}/vocals`;
    rows.backing.audio.src = `/api/stem/${encodeURIComponent(id)}/backing`;
    rows.combined.audio.src = `/api/track/${encodeURIComponent(id)}`;
    say(d.stale
      ? "The track was re-imported after this split — the strips describe "
        + "the OLD audio. Re-split to catch up."
      : "Both = what the pipeline hears. Left/Right solo a side; white "
        + "ticks are hits the mono analysis never saw.", d.stale === true);
    setChannel(channel);
  }

  function empty(msg: string): void {
    data = null;
    body.hidden = true;
    seg.hidden = true;
    split.textContent = "Split voices";
    split.disabled = false;
    say(msg);
  }

  async function poll(jobId: string): Promise<void> {
    const mine = token;
    for (let i = 0; i < 400; i++) {
      await new Promise(r => setTimeout(r, 1500));
      if (mine !== token) return;
      let job;
      try { job = await api.job(jobId); }
      catch { continue; }              // one dropped poll is not a failure
      if (!job.done) {
        const last = job.log[job.log.length - 1] ?? "";
        say(`Splitting voices — Demucs running… ${last.slice(0, 60)}`);
        continue;
      }
      if (job.phase === "failed") {
        empty("Split failed — " + (job.error || "see the studio log."));
        note.classList.add("err");
        return;
      }
      await load();
      return;
    }
    empty("Split is taking too long — check the studio terminal.");
  }

  split.addEventListener("click", () => {
    const id = trackId;
    if (!id) return;
    void (async () => {
      split.disabled = true;
      say("Splitting voices — Demucs running…");
      try {
        const job = await api.stemsSplit(id, data !== null);
        if (!job.id) { empty(job.error || "The studio refused the split."); return; }
        void poll(job.id);
      } catch (err) {
        empty(`Could not reach the studio — ${String(err)}`);
      }
    })();
  });

  async function load(): Promise<void> {
    const id = trackId;
    if (!id) return;
    const mine = ++token;
    try {
      const r = await api.stems(id);
      if (mine !== token) return;
      if (r.status === 404 || !r.body?.ok) {
        empty("Not split yet. Splitting shows which hits are sung and which "
          + "side they live on — the castle still plays the combined file.");
        return;
      }
      ready(r.body);
    } catch (err) {
      if (mine === token) empty(`Could not reach the studio — ${String(err)}`);
    }
  }

  const onResize = (): void => drawAll();
  window.addEventListener("resize", onResize);

  return {
    el,
    show(id: string | null): void {
      token++;                         // cancel any poll on the old track
      stop();
      trackId = id;
      el.hidden = id === null;
      if (!id) { data = null; return; }
      body.hidden = true;
      seg.hidden = true;
      say("Checking for stems…");
      void load();
    },
    stop,
    destroy(): void {
      token++;
      stop();
      window.removeEventListener("resize", onResize);
      void ctx?.close();
      el.remove();
    },
  };
}
