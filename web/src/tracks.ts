/**
 * Tracks panel — importing audio and turning it into scenes.
 *
 * Two modes, decided by whether tools/studio.py is serving this page.
 *
 * STUDIO  — the local server is there. Import by link or file, delete,
 *           and write scenes straight into scenes.yaml. Real work.
 * STATIC  — opened as a file, or as a published artifact. No server can
 *           be reached, so links are impossible (and yt-dlp is a binary,
 *           not something a page can run). Dropping a local file still
 *           works: it's analysed in the browser and you get the scene
 *           block to paste.
 */

import { detectOnsets } from "./onsets.js";
import type { Scene } from "./types.js";

/** The import options row, as tools/studio.py remembers them per track. */
export interface TrackOpts {
  id?: string;
  start?: string;
  take?: string;
  sensitivity?: string;
  bitrate?: number;
  sample_rate?: number;
  channels?: number;
  normalize?: boolean;
}

/** One entry from `GET /api/tracks`. */
export interface TrackInfo {
  id: string;
  /** File size on disk, kilobytes. */
  kb: number;
  /** Duration in seconds; missing if ffprobe could not say. */
  dur?: number;
  /** Where it came from — a URL, or `file:<name>` for a dropped file. */
  source: string;
  title: string;
  imported: string;
  opts: TrackOpts;
  notes: string;
  /** Band name -> how many onsets were found in it. */
  onsets?: Record<string, number>;
  error?: string;
}

/**
 * The option row as it goes *out* to the server. Deliberately strings, not
 * numbers: blank means "leave it as it was", and only a string can carry that
 * distinction — `+""` would arrive as a very definite zero.
 */
interface ImportOpts {
  id: string;
  start: string;
  take: string;
  sensitivity: string;
  bitrate: string;
  sample_rate: string;
  channels: string;
  normalize: boolean;
}

interface TracksResponse { tracks?: TrackInfo[] }
/** Import, re-import and scene writes all answer with ok plus a tail of log. */
interface ActionResponse { ok: boolean; tracks?: TrackInfo[]; log?: string; error?: string }

export interface TracksDeps {
  /** The show as loaded, for the capacity readout's "alongside the current show". */
  scenes: readonly Scene[];
  /**
   * Called when a track row is picked, so the host can open the clip editor
   * on it. Optional: the Tracks panel is useful without a waveform, and the
   * static/artifact build has no server to fetch one from.
   */
  onSelect?: (trackId: string) => void;
}

export function initTracks(deps: TracksDeps): void {
  const SCENES = deps.scenes;

  /* The ids are the contract with the generated HTML. A missing one is a bug
     in the template, and the cast lets it fail as a TypeError at first use —
     the same way the untyped version did — instead of quietly doing nothing. */
  const byId = <T extends HTMLElement = HTMLElement>(id: string): T =>
    document.getElementById(id) as T;

  const T = {
    mode: "static" as "static" | "studio",
    note: byId("trkNote"),
    list: byId("trkList"),
    yaml: byId("trkYaml"),
    modeEl: byId("trkMode"),
  };
  const say = (msg: string, err?: boolean): void => {
    T.note.textContent = msg;
    T.note.classList.toggle("err", !!err);
  };
  const val = (id: string): string => byId<HTMLInputElement>(id).value.trim();
  const opts = (): ImportOpts => ({
    id: val("trkId"), start: val("trkStart"), take: val("trkTake"),
    sensitivity: val("trkSens"), bitrate: val("trkBitrate"),
    sample_rate: val("trkRate"), channels: val("trkCh"),
    normalize: byId<HTMLInputElement>("trkNorm").checked,
  });

  /* Live capacity readout. The whole SD-versus-flash argument comes down to
     bytes per second against three ceilings, so show the arithmetic rather
     than asserting it:

       1.67 MB  free PSRAM — MEASURED on the real board while playing, not
                estimated (bench_audio, 2026-08-10: 1713 KB free with a scene
                running). This is the cap on the whole-file SD load.
       ~2.9 MB  flash left for ALL scenes after the firmware
       32 GB    the card, i.e. no ceiling worth writing down — but only
                reachable by streaming, which isn't built yet
  */
  const PSRAM_FREE = 1713 * 1024, FLASH_FREE = 2.9 * 1024 * 1024;
  const mmss = (s: number): string =>
    `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
  /** Valid MPEG-1 Layer III range. Outside it the encoder has nothing to do
   *  with the number, so neither should the readout. */
  const MIN_KBPS = 32, MAX_KBPS = 320;

  function updateCapacity(): void {
    // `|| 96` alone let negatives through — -5 is truthy — and the readout
    // then formatted negative seconds as "-47:-47". Clamp instead, so every
    // out-of-range value lands somewhere the encoder would actually accept.
    const typed = +val("trkBitrate");
    const kbps = Number.isFinite(typed) && typed > 0
      ? Math.min(MAX_KBPS, Math.max(MIN_KBPS, typed))
      : 96;
    const ch = +val("trkCh") === 2 ? 2 : 1;
    const bps = kbps * 1000 / 8 * (ch === 2 ? 1 : 1);   // bitrate already covers channels
    // Only the flash figure *after* the current show is worth printing, so the
    // empty-flash number the original also computed is not kept.
    const psram = PSRAM_FREE / bps;
    const used = SCENES.reduce((a, s) => a + (s.dur / 1000) * 12000, 0); // ≈96k mono
    const left = Math.max(0, (FLASH_FREE - used) / bps);
    byId("trkCap").innerHTML =
      `<b>${kbps} kbps ${ch === 2 ? "stereo" : "mono"}</b> = ${(bps / 1024).toFixed(1)} KB/s &nbsp;·&nbsp; `
      + `flash, alongside the current show: <b>${mmss(left)}</b> &nbsp;·&nbsp; `
      + `SD loaded into PSRAM: <b>${mmss(psram)}</b> `
      + `<span class="${psram >= 240 ? "ok" : "no"}">${psram >= 240 ? "(4 min fits)" : "(under 4 min)"}</span>`
      + ` &nbsp;·&nbsp; streamed from SD: <b class="ok">no limit</b>`;
  }
  ["trkBitrate", "trkCh"].forEach(id =>
    byId(id).addEventListener("input", updateCapacity));
  updateCapacity();

  void fetch("/api/tracks").then(r => r.ok ? r.json() as Promise<TracksResponse> : Promise.reject())
    .then(d => { T.mode = "studio"; T.modeEl.textContent = "studio · connected";
                 byId("trkServer").hidden = false;
                 drawTracks(d.tracks); say("Import by link or drop a file. Scenes write straight to scenes.yaml."); })
    .catch(() => {
      T.mode = "static";
      T.modeEl.textContent = "read-only · studio not running";
      byId("trkOffline").hidden = false;
      const url = byId<HTMLInputElement>("trkUrl");
      url.placeholder = "Links need the local studio (see above)";
      url.disabled = true;
      byId<HTMLButtonElement>("trkGet").disabled = true;
      say("");
    });

  const ESCAPES: Record<string, string> =
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
  const esc = (s: unknown): string =>
    String(s).replace(/[&<>"]/g, c => ESCAPES[c] as string);

  function drawTracks(tracks: TrackInfo[] | undefined): void {
    T.list.innerHTML = (tracks || []).map(t => {
      const o = t.opts || {};
      const fmt = [`${o.bitrate || "?"}kbps`,
                   o.channels === 2 ? "stereo" : "mono",
                   `${(o.sample_rate || 44100) / 1000}k`,
                   o.normalize ? "normalised" : null].filter(Boolean).join(" · ");
      const onsets = Object.entries(t.onsets || {})
        .map(([k, n]) => `${k.replace("onset_", "")} ${n}`).join(" · ") || "no onsets";
      // The remembered source. A link if it came from one, so you can go back
      // to where it came from without digging through history.
      const isUrl = /^https?:\/\//.test(t.source || "");
      const src = !t.source ? ""
        : isUrl
          ? `<a href="${esc(t.source)}" target="_blank" rel="noreferrer noopener">${esc(t.title || t.source).slice(0, 64)}</a>`
          : esc(t.source.replace(/^file:/, "").split("/").pop());
      return `
      <div class="trk" data-id="${esc(t.id)}">
        <div class="trk__nm">${esc(t.id)}
          <small>${t.dur ?? "?"}s · ${t.kb} KB · ${fmt}</small>
          <small>${onsets}</small>
          ${src ? `<small class="trk__src">from ${src}</small>` : ""}
          ${t.notes ? `<small>${esc(t.notes)}</small>` : ""}
        </div>
        <div class="trk__act">
          <button data-act="scene">Make scene</button>
          ${t.source ? `<button data-act="refresh" title="Rebuild from the remembered source using the options above">Re-import</button>` : ""}
          <button data-act="del" class="danger">Delete</button>
        </div>
      </div>`;
    }).join("");
  }

  // Clicking anywhere on a row that is not a button opens the clip editor on
  // it. Picking a track and then looking at it is one intent, not two.
  T.list.addEventListener("click", e => {
    const el = e.target as HTMLElement | null;
    if (el?.closest("button")) return;
    const id = el?.closest<HTMLElement>(".trk")?.dataset["id"];
    if (id) deps.onSelect?.(id);
  });

  T.list.addEventListener("click", async e => {
    const btn = (e.target as HTMLElement | null)?.closest("button"); if (!btn) return;
    // Every button is rendered inside a .trk carrying the id, so the row and
    // the attribute are both there or the markup above is broken.
    const id = btn.closest<HTMLElement>(".trk")!.dataset["id"] ?? "";
    if (btn.dataset["act"] === "del") {
      if (!confirm(`Delete track "${id}"? The file is removed from tracks/.`)) return;
      const r = await fetch(`/api/tracks/${id}`, { method: "DELETE" })
        .then(res => res.json() as Promise<ActionResponse>);
      say(r.ok ? `Deleted ${id}.` : `Could not delete ${id}.`, !r.ok);
      refresh();
    } else if (btn.dataset["act"] === "refresh") {
      // Rebuild from the remembered source. Anything left blank in the option
      // row keeps whatever was used last time.
      say(`Re-importing ${id} from its remembered source…`);
      const o = opts();
      const r = await fetch("/api/refresh", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id, start: o.start, take: o.take, sensitivity: o.sensitivity,
          bitrate: val("trkBitrate"),
          sample_rate: val("trkRate"),
          channels: val("trkCh"),
          normalize: byId<HTMLInputElement>("trkNorm").checked,
        })
      }).then(res => res.json() as Promise<ActionResponse>);
      if (r.ok) { drawTracks(r.tracks); say(`Re-imported ${id}.`); }
      else say(`Re-import failed — ${(r.log || r.error || "").slice(-400)}`, true);
    } else {
      const r = await fetch("/api/tracks").then(res => res.json() as Promise<TracksResponse>);
      // The list was just refetched from the same server that drew the row, so
      // a miss here means the track vanished mid-click — let it throw.
      const t = (r.tracks || []).find(x => x.id === id)!;
      const block = sceneYaml(id, t.dur, t.onsets || {});
      T.yaml.hidden = false; T.yaml.textContent = block;
      say(`Writing scene "${id}" into scenes.yaml and rebuilding…`);
      const res = await fetch("/api/scene", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, yaml: block })
      }).then(x => x.json() as Promise<ActionResponse>);
      say(res.ok ? `Scene "${id}" written and rebuilt. Reload to see it in Scenes.`
                 : `Scene write failed — ${(res.log || "").slice(-300)}`, !res.ok);
    }
  });

  const refresh = (): void => {
    if (T.mode !== "studio") return;
    void fetch("/api/tracks").then(r => r.json() as Promise<TracksResponse>)
      .then(d => drawTracks(d.tracks));
  };

  /* ── Server controls ──────────────────────────────────────────────
     Stop and Restart can live here because the server is, by definition,
     reachable when these are visible. Starting it cannot — see the offline
     panel for why that one step happens outside the browser. */
  const goOffline = (msg: string): void => {
    T.mode = "static";
    T.modeEl.textContent = "read-only · studio not running";
    byId("trkServer").hidden = true;
    byId("trkOffline").hidden = false;
    byId<HTMLInputElement>("trkUrl").disabled = true;
    byId<HTMLButtonElement>("trkGet").disabled = true;
    T.list.innerHTML = "";
    say(msg || "");
  };

  byId("srvStop").addEventListener("click", async () => {
    if (!confirm("Stop the studio server? Importing and scene editing stop "
               + "until you launch it again with Castle Cue Desk.command.")) return;
    say("Stopping…");
    // The server answers before it shuts down, so a failure here is a real
    // failure rather than the expected disconnect.
    try { await fetch("/api/server/stop", { method: "POST" }); } catch { /* already gone */ }
    setTimeout(() => goOffline("Studio stopped. Everything on this page still "
      + "works — you just can't import or write scenes until it's running again."), 600);
  });

  byId("srvRestart").addEventListener("click", async () => {
    say("Restarting…");
    try { await fetch("/api/server/restart", { method: "POST" }); } catch { /* it's re-execing */ }
    // It re-execs itself, so poll until it answers again rather than guessing
    // how long that takes.
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 500));
      try {
        const d = await fetch("/api/tracks", { cache: "no-store" })
          .then(r => r.json() as Promise<TracksResponse>);
        drawTracks(d.tracks);
        return say("Studio restarted.");
      } catch { /* still coming up */ }
    }
    goOffline("Restart didn't come back within 15 s — relaunch with "
            + "Castle Cue Desk.command.");
  });

  byId("trkGet").addEventListener("click", async () => {
    const url = val("trkUrl");
    if (!url) return say("Paste a link first.", true);
    say("Importing… this runs yt-dlp and ffmpeg locally, so give it a moment.");
    try {
      const r = await fetch("/api/import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ url }, opts()))
      }).then(res => res.json() as Promise<ActionResponse>);
      if (r.ok) { drawTracks(r.tracks); say("Imported. Press “Make scene” to wire it into the show.");
                  byId<HTMLInputElement>("trkUrl").value = ""; }
      else say(`Import failed — ${(r.log || r.error || "").slice(-400)}`, true);
    } catch (err) { say(`Import failed — ${String(err)}`, true); }
  });

  /* ── Drag and drop ── */
  const drop = byId("trkDrop");
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("click", () => {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "audio/*";
    inp.onchange = () => { const f = inp.files?.[0]; if (f) void takeFile(f); };
    inp.click();
  });
  drop.addEventListener("drop", e => {
    const f = e.dataTransfer?.files[0];
    if (f) void takeFile(f);
  });

  async function takeFile(file: File): Promise<void> {
    if (T.mode === "studio") {
      say(`Uploading ${file.name}…`);
      const fd = new FormData(); fd.append("file", file);
      try {
        const r = await fetch("/api/import", {
          method: "POST", headers: { "X-Import-Opts": JSON.stringify(opts()) }, body: fd
        }).then(res => res.json() as Promise<ActionResponse>);
        if (r.ok) { drawTracks(r.tracks); say("Imported. Press “Make scene” to wire it in."); }
        else say(`Import failed — ${(r.log || r.error || "").slice(-400)}`, true);
      } catch (err) { say(`Import failed — ${String(err)}`, true); }
      return;
    }
    // Static mode: analyse right here.
    say(`Analysing ${file.name} in the browser…`);
    try {
      const buf = await file.arrayBuffer();
      // Safari still only has the prefixed constructor on some versions, and
      // decoding is the one thing static mode cannot do without.
      const Offline = window.OfflineAudioContext
        || (window as unknown as { webkitOfflineAudioContext: typeof OfflineAudioContext })
             .webkitOfflineAudioContext;
      const ctx = new Offline(1, 44100, 44100);
      const audio = await ctx.decodeAudioData(buf);
      const marks = await detectOnsets(audio);
      const id = (opts().id || file.name.replace(/\.[^.]+$/, "")
                 ).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32);
      const counts: Record<string, number> =
        Object.fromEntries(Object.entries(marks).map(([k, v]) => [k, v.length]));
      const block = sceneYaml(id, audio.duration, counts);
      T.yaml.hidden = false; T.yaml.textContent = block;
      const kb = Math.round(audio.duration * 96 * 1000 / 8 / 1024);
      say(`${file.name}: ${audio.duration.toFixed(1)}s, ~${kb} KB at 96 kbps. `
        + Object.entries(counts).map(([k, n]) => `${k.replace("onset_", "")} ${n}`).join(" · ")
        + `. Copy the block below, save the file into tracks/${id}.mp3, then run make audio.`);
    } catch (err) { say(`Could not read that file — ${String(err)}`, true); }
  }

  function sceneYaml(id: string, dur: number | undefined, counts: Record<string, number>): string {
    const zone: Record<string, string> = { onset_low: "door", onset_mid: "towerL", onset_high: "towerR" };
    const col: Record<string, string> = { onset_low: "[1.0, 0.12, 0.02, 0.0]", onset_mid: "[0.66, 0.10, 1.0, 0.05]",
                  onset_high: "[0.30, 1.0, 0.55, 0.0]" };
    const dec: Record<string, number> = { onset_low: 0.86, onset_mid: 0.92, onset_high: 0.94 };
    const L = [
      `  - id: ${id}`,
      `    name: ${id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}`,
      `    kind: custom`, `    volume: 0.7`,
      `    duration_ms: ${Math.round((dur ?? NaN) * 1000)}`, `    loop: true`,
      `    blurb: >`,
      `      Imported track. Light cues are onset-detected from the audio`,
      `      itself, so they follow whatever the track actually does.`,
      `    audio_file: tracks/${id}.mp3`,
      `    base: {towerL: chill, towerR: chill, door: ember}`,
      `    levels: {towerL: 0.4, towerR: 0.4, door: 0.5}`,
      `    pulse:`,
    ];
    for (const [band, n] of Object.entries(counts)) {
      if (!n) continue;
      L.push(`      - {synth: ${band}, zone: ${zone[band]}, intensity: 0.55, `
           + `decay: ${dec[band]}, color: ${col[band]}}   # ${n} onsets`);
    }
    L.push(`    cues: []`);
    return L.join("\n");
  }
}
