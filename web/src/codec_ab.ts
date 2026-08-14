/**
 * Hearing what a codec costs, on the actual clip, at the actual settings.
 *
 * The format dropdown asks a question it cannot answer. MP3 at 96k is four
 * times smaller than WAV and the WAV costs the S2 no decode CPU at all — which
 * of those matters more depends entirely on the material, and no table of
 * formats will tell you. So: encode the selected clip every way, and switch
 * between them instantly at the same position, which is the only way an A/B
 * is worth anything. A gap while the next file loads is long enough to forget
 * what the last one sounded like.
 *
 * The dB figure alongside each is a spectral distance from the lossless
 * encode — see tools/codec_compare.py for what it is and, more importantly,
 * what it is not. It ranks; it does not judge.
 *
 * Muted by default like everything else here: the element is created muted and
 * only the explicit click unmutes it.
 */

export interface CodecRow {
  codec: string;
  bytes: number;
  /** Spectral distance from the lossless reference, dB. 0 for the reference. */
  db: number;
  url: string;
}

interface CompareResponse {
  ok: boolean;
  error?: string;
  reference?: string;
  codecs?: CodecRow[];
}

export interface CodecAbDeps {
  /** The track to encode, and which piece of it. Null when nothing is picked. */
  target: () => { id: string; start: number; take: number } | null;
  /** Encoder settings from the options row, so the test matches the import. */
  opts: () => { bitrate: string; channels: string; sample_rate: string };
  /** Fired before sound starts, so the host can silence everything else. */
  onClaim?: () => void;
}

export interface CodecAb {
  readonly el: HTMLElement;
  /** Stop and clear. Safe at any time. */
  reset: () => void;
  stop: () => void;
}

const kb = (n: number): string =>
  n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(2)} MB` : `${Math.round(n / 1024)} KB`;

export function createCodecAb(deps: CodecAbDeps): CodecAb {
  const el = document.createElement("div");
  el.className = "codecab";

  const bar = document.createElement("div");
  bar.className = "codecab__bar";
  const run = document.createElement("button");
  run.type = "button";
  run.textContent = "Compare codecs";
  run.title = "Encode this clip as WAV, MP3, Opus and FLAC, then switch "
            + "between them while it plays";
  const note = document.createElement("span");
  note.className = "codecab__note";
  bar.append(run, note);

  const rowsEl = document.createElement("div");
  rowsEl.className = "codecab__rows";
  rowsEl.hidden = true;
  el.append(bar, rowsEl);

  const audio = new Audio();
  audio.muted = true;
  audio.loop = true;              // an A/B you have to restart is a bad A/B
  audio.preload = "auto";

  let rows: CodecRow[] = [];
  let current: string | null = null;
  let busy = false;
  /** See preview.ts. Switching codecs quickly is the normal way to use this
   *  panel, so the superseded-play race is not a corner case here. */
  let epoch = 0;

  function stop(): void {
    epoch++;
    audio.pause();
    audio.muted = true;
    current = null;
    draw();
  }

  function reset(): void {
    stop();
    rows = [];
    rowsEl.hidden = true;
    rowsEl.replaceChildren();
    note.textContent = "";
  }

  /**
   * Switch codec without losing the position.
   *
   * This is the whole point: the same instant of the same clip, one encoder
   * after another. Reloading from zero would make the comparison worthless.
   */
  function pick(codec: string): void {
    const row = rows.find(r => r.codec === codec);
    if (!row) return;
    if (current === codec) return stop();
    const at = audio.currentTime;
    const mine = ++epoch;
    deps.onClaim?.();
    current = codec;
    audio.src = row.url;
    audio.muted = false;          // the click is the consent
    audio.volume = 0.7;
    const resume = (): void => {
      // Only meaningful once the new file knows how long it is.
      if (Number.isFinite(at) && at > 0) {
        try { audio.currentTime = at; } catch { /* not seekable yet */ }
      }
      audio.removeEventListener("loadedmetadata", resume);
    };
    audio.addEventListener("loadedmetadata", resume);
    void audio.play().catch(() => {
      if (mine !== epoch) return;      // a newer pick already took over
      stop();
      note.textContent = `Could not play the ${codec} encode.`;
    });
    draw();
  }

  function draw(): void {
    rowsEl.replaceChildren();
    if (!rows.length) return;
    const smallest = Math.min(...rows.map(r => r.bytes));
    for (const r of rows) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "codecab__pick" + (current === r.codec ? " on" : "");
      b.dataset["codec"] = r.codec;
      // Size and fidelity are the trade being made, so both are on the button.
      const loss = r.db <= 0 ? "reference" : `${r.db.toFixed(2)} dB vs lossless`;
      b.innerHTML = `<b>${r.codec.toUpperCase()}</b>`
        + `<span>${kb(r.bytes)}${r.bytes === smallest ? " · smallest" : ""}</span>`
        + `<span>${loss}</span>`;
      b.title = r.db <= 0
        ? "The lossless encode everything else is measured against"
        : `Spectral distance from the lossless encode. Lower is closer; `
          + `a lossless codec floors at about 0.06 dB.`;
      b.addEventListener("click", () => pick(r.codec));
      rowsEl.append(b);
    }
    rowsEl.hidden = false;
  }

  run.addEventListener("click", async () => {
    const t = deps.target();
    if (!t) { note.textContent = "Pick a track first."; return; }
    if (busy) return;
    busy = true;
    reset();
    run.disabled = true;
    run.textContent = "Encoding…";
    // Four ffmpeg passes over the clip; on a long selection that is a wait
    // worth naming rather than leaving as a dead button.
    note.textContent = `Encoding ${t.take.toFixed(1)}s four ways…`;
    try {
      const o = deps.opts();
      const r = await fetch("/api/compare", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: t.id, start: t.start, take: t.take,
                               bitrate: o.bitrate, channels: o.channels,
                               sample_rate: o.sample_rate }),
      }).then(x => x.json() as Promise<CompareResponse>);
      if (!r.ok || !r.codecs) {
        note.textContent = `Could not encode — ${r.error || "unknown error"}`;
        return;
      }
      rows = r.codecs;
      draw();
      note.textContent = "Press one to hear it. Switching keeps the position, "
                       + "so you are comparing the same moment.";
    } catch (err) {
      note.textContent = `Could not encode — ${String(err)}`;
    } finally {
      busy = false;
      run.disabled = false;
      run.textContent = "Compare codecs";
    }
  });

  audio.addEventListener("error", () => { if (current) stop(); });

  return { el, reset, stop };
}
