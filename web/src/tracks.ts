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

import { api } from "./api.js";
import { startEta } from "./eta.js";
import type { BandEditor } from "./band_editor.js";
import { fillOptsFrom, initImportOpts } from "./import_opts.js";
import { trackRowHtml } from "./track_rows.js";
import { wireUrlImport } from "./track_import_url.js";
import { detectOnsets, loudnessEnvelope } from "./onsets.js";
import { createPreview } from "./preview.js";
import { sceneYaml } from "./track_scene.js";
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
  format?: string;
  normalize?: boolean;
  fade_in?: number | string | null;
  fade_out?: number | string | null;
}

/** One entry from `GET /api/tracks`. */
export interface TrackInfo {
  id: string;
  /** Container it landed in: mp3 | wav | flac | opus. */
  ext?: string;
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


export interface TracksDeps {
  /** The show as loaded, for the capacity readout's "alongside the current show". */
  scenes: readonly Scene[];
  /**
   * Called when a track row is picked, so the host can open the clip editor
   * on it. Null means "nothing is selected any more" — a deleted row, say —
   * and the editor should close rather than keep showing a track that is gone.
   * Optional: the Tracks panel is useful without a waveform, and the
   * static/artifact build has no server to fetch one from.
   */
  onSelect?: (trackId: string | null) => void;
  /** Row preview started or stopped — so the transport can reflect it. */
  onPreviewState?: (playing: boolean) => void;
  /**
   * The library, every time it is redrawn: imported, deleted, re-imported.
   * The budget card is the consumer — what the card would hold is the whole
   * of the SD build's ledger, and only this panel ever learns it.
   */
  onList?: (tracks: readonly TrackInfo[]) => void;
  /**
   * Fired just before a row preview starts, so the host can stop whatever else
   * it has playing. Two audio sources at once is never what was meant.
   */
  onAudioClaim?: () => void;
  /**
   * Per-band zones and thresholds, as the clip editor has them. A generated
   * scene has to carry these or the render would detect different onsets from
   * the ones you just spent a minute tuning.
   */
  bands?: BandEditor;
}

export interface TracksApi {
  /** Silence the row preview — for when something else wants the speakers. */
  stopPreview: () => void;
  /** Whether a row preview is sounding right now — so the transport can
   *  show Pause and mean it instead of playing dead (round 2). */
  previewing: () => boolean;
}

export function initTracks(deps: TracksDeps): TracksApi {
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
    count: byId("trkCount"),
    yaml: byId("trkYaml"),
    modeEl: byId("trkMode"),
    /** Scene ids already in scenes.yaml, so a row can say it is in the show. */
    sceneIds: new Set<string>(),
    /** The row the clip editor is open on. */
    selected: null as string | null,
    /** Last list drawn, so a redraw does not need another round trip. */
    tracks: [] as TrackInfo[],
  };
  const say = (msg: string, err?: boolean): void => {
    T.note.textContent = msg;
    T.note.classList.toggle("err", !!err);
  };
  /**
   * A result the page cannot show without being rebuilt, plus the button that
   * rebuilds it. The scenes and their audio are baked into this HTML by
   * gen_previewer, so a new scene genuinely is not here until a reload — and
   * an auto-reload would throw away whatever the user was in the middle of.
   */
  const sayReload = (msg: string): void => {
    say(msg + " ");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "trk-reload";
    b.textContent = "Reload the desk";
    b.addEventListener("click", () => location.reload());
    T.note.append(b);
  };
  const val = (id: string): string => byId<HTMLInputElement>(id).value.trim();
  /* Roughly what the scenes already in the show cost in flash, at ≈96k mono.
     The readout wants "how much room is left", which is that subtracted from
     the partition. */
  /* What the scenes already in the show cost in flash.
     The rendered size, now that scenes carry it. This used to estimate from
     duration at ≈96k mono, which was both redundant and wrong the moment a
     scene was rendered at any other bitrate: with ten scenes loaded the
     estimate overshot the whole partition and the readout reported 0:00 of
     room left while the scene list, counting real bytes, said 0.62 MB spare.
     Two numbers for one quantity, and the guess was the one on screen.
     The estimate survives only for a scene that has not been rendered yet. */
  const flashUsed = (): number =>
    SCENES.reduce((a, s) => a + (s.bytes || (s.dur / 1000) * 12000), 0);
  const form = initImportOpts(flashUsed);
  const opts = form.values;

  void api.tracks()
    .then(d => {
      if (!d.tracks && !d.scenes) return Promise.reject(new Error("no list")); T.mode = "studio"; T.modeEl.textContent = "studio · connected";
                 byId("trkServer").hidden = false;
                 T.sceneIds = new Set(d.scenes || []);
                 drawTracks(d.tracks);
                 say("Import by link or drop a file. Press Play to hear a track, "
                   + "or click its row to trim it."); })
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

  /* Row audition. The button is the only thing that can make this speak — see
     preview.ts. Redrawing on every change keeps the label ("Play"/"Stop") and
     the row highlight from drifting out of step with what is actually on. */
  const preview = createPreview({
    onChange: () => { syncPlaying(); deps.onPreviewState?.(preview.playing() !== null); },
    onError: msg => say(msg, true),
    onClaim: () => deps.onAudioClaim?.(),
  });

  /**
   * Reflect what is sounding, touching only the controls that say so.
   *
   * Redrawing the whole list on every play and stop worked, but it replaced
   * every row under the pointer for a change to one of them — which drops
   * focus, and can lose a click that arrives while the rebuild is in flight.
   * That last one showed up as a one-in-eighty flake in the browser suite,
   * where clicking one row's Play immediately after another's occasionally
   * hit a node that no longer existed.
   */
  function syncPlaying(): void {
    const id = preview.playing();
    for (const el of Array.from(T.list.querySelectorAll(".trk"))) {
      const rowEl = el as HTMLElement;
      const on = rowEl.dataset["id"] === id;
      rowEl.classList.toggle("playing", on);
      const b = rowEl.querySelector("button[data-act='play']");
      if (!b) continue;
      b.textContent = on ? "Stop" : "Play";
      b.classList.toggle("on", on);
    }
  }

  function drawTracks(tracks: TrackInfo[] | undefined): void {
    if (tracks) T.tracks = tracks;
    const playingId = preview.playing();
    // The row markup lives in track_rows.ts (500-line cap; the seam is
    // "how a row looks" vs "what clicking it does").
    T.list.innerHTML = T.tracks.map(t => trackRowHtml(t, {
      selected: T.selected === t.id,
      inShow: T.sceneIds.has(t.id),
      sounding: playingId === t.id,
    })).join("");
    const n = T.tracks.length;
    T.count.textContent = n === 0 ? "empty" : `${n} imported`;
    deps.onList?.(T.tracks);
  }

  // Clicking anywhere on a row that is not a button opens the clip editor on
  // it. Picking a track and then looking at it is one intent, not two.
  T.list.addEventListener("click", e => {
    const el = e.target as HTMLElement | null;
    if (el?.closest("button")) return;
    const id = el?.closest<HTMLElement>(".trk")?.dataset["id"];
    if (!id) return;
    T.selected = id;
    const t = T.tracks.find(x => x.id === id);
    if (t) fillOptsFrom(t);
    // The mono badge IS the fix: clicking it stages stereo so the next
    // Re-import does what the warning promised.
    if (el?.closest(".trk__mono")) {
      const ch = byId<HTMLInputElement>("trkCh");
      ch.value = "2";
      ch.dispatchEvent(new Event("input", { bubbles: true }));
      say(`Channels set to stereo for ${id} — press Re-import on its row to rebuild it.`);
    }
    drawTracks(undefined);
    deps.onSelect?.(id);
  });

  T.list.addEventListener("click", async e => {
    const btn = (e.target as HTMLElement | null)?.closest("button"); if (!btn) return;
    // Every button is rendered inside a .trk carrying the id, so the row and
    // the attribute are both there or the markup above is broken.
    const id = btn.closest<HTMLElement>(".trk")!.dataset["id"] ?? "";
    if (btn.dataset["act"] === "play") {
      preview.toggle(id);
    } else if (btn.dataset["act"] === "del") {
      if (!confirm(`Delete track "${id}"? The file is removed from tracks/.`)) return;
      if (preview.playing() === id) preview.stop();
      try {
        const r = await api.remove(id);
        say(r.ok ? `Deleted ${id}.` : `Could not delete ${id}.`, !r.ok);
      } catch (err) {
        say(`Could not delete ${id} — ${String(err)}`, true);
      }
      if (T.selected === id) { T.selected = null; deps.onSelect?.(null); }
      refresh();
    } else if (btn.dataset["act"] === "refresh") {
      // Rebuild from the remembered source. Anything left blank in the option
      // row keeps whatever was used last time.
      // Re-import must use THIS row's settings even when a different track
      // (or none) is open in the editor — a leftover panel state silently
      // rebuilding the wrong way was round 1's nastiest trap.
      if (T.selected !== id) {
        const t = T.tracks.find(x => x.id === id);
        if (t) fillOptsFrom(t);
      }
      const o = opts();
      const eta = startEta("reimport", `Re-importing ${id} from its remembered source`, say);
      // Busy state on the button itself: a re-import takes seconds, and a
      // button that looks dead for that long reads as broken (round 1).
      btn.disabled = true;
      const label = btn.textContent;
      btn.textContent = "Working…";
      try {
        const r = await api.refresh({
          id, start: o.start, take: o.take, sensitivity: o.sensitivity,
          bitrate: val("trkBitrate"),
          sample_rate: val("trkRate"),
          channels: val("trkCh"),
          normalize: byId<HTMLInputElement>("trkNorm").checked,
        });
        if (r.ok) {
          drawTracks(r.tracks);
          // Say what came OUT, not just that something happened: fourteen
          // silent seconds ending in an unchanged row read as a dead button.
          const after = (r.tracks || []).find(x => x.id === id);
          const ch = after?.opts?.channels === 2 ? "stereo" : "mono";
          say(`Re-imported ${id} — ${ch}.`
            + (ch === "mono"
              ? " Still mono: click its mono ⚠ badge (or set CHANNELS to"
                + " stereo in Options) and Re-import again."
              : ""));
        }
        else say(`Re-import failed — ${(r.log || r.error || "").slice(-400)}`, true);
        eta.stop(r.ok);
      } finally {
        eta.stop();
        btn.disabled = false;
        if (label !== null) btn.textContent = label;
      }
    } else {
      // Re-rendering the whole show takes seconds — longer than anyone waits
      // before deciding a button is broken. Say so on the button itself.
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Working…";
      try {
        const r = await api.tracks();
        // The list was just refetched from the same server that drew the row, so
        // a miss here means the track vanished mid-click — let it throw.
        const t = (r.tracks || []).find(x => x.id === id)!;
        // The loudness envelope drives the scene's quiet/verse/chorus set
        // cues. Missing it degrades to one standing look, not to a failure.
        const wf = await api.waveform(id).then(w => w.body).catch(() => null);
        const block = sceneYaml(id, t.dur, t.onsets || {}, t.ext, deps.bands, wf?.env);
        T.yaml.hidden = false; T.yaml.textContent = block;
        const eta = startEta("scene",
          `Writing scene "${id}" into scenes.yaml and re-rendering the show`, say);
        const res = await api.scene(id, block).finally(() => eta.stop());
        if (!res.ok) {
          say(`Scene write failed — ${(res.log || res.error || "").slice(-300)}`, true);
          return;
        }
        eta.stop(true);
        // The row now says "in the show", which is the visible proof the click
        // did something. The page's own scene list is baked in at generate
        // time, so it needs a reload — offered, not forced.
        T.sceneIds = new Set(res.scenes || [...T.sceneIds, id]);
        drawTracks(undefined);
        sayReload(`Scene "${id}" ${res.replaced ? "updated" : "added"} in scenes.yaml `
                + `and the audio re-rendered.`);
      } finally {
        btn.disabled = false;
        if (label !== null) btn.textContent = label;
      }
    }
  });

  const refresh = (): void => {
    if (T.mode !== "studio") return;
    void api.tracks()
      .then(d => { T.sceneIds = new Set(d.scenes || []); drawTracks(d.tracks); })
      // The list is redrawn after deletes and imports; if the server died in
      // between, the row must not just silently stay stale (round-1 C5).
      .catch(() => say("Lost the studio server — is it still running?", true));
  };

  /* ── Server controls ──────────────────────────────────────────────
     Stop and Restart can live here because the server is, by definition,
     reachable when these are visible. Starting it cannot — see the offline
     panel for why that one step happens outside the browser. */
  const goOffline = (msg: string): void => {
    preview.stop();                  // the file it was streaming just went away
    T.mode = "static";
    T.tracks = [];
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
    try { await api.serverStop(); } catch { /* already gone */ }
    setTimeout(() => goOffline("Studio stopped. Everything on this page still "
      + "works — you just can't import or write scenes until it's running again."), 600);
  });

  byId("srvRestart").addEventListener("click", async () => {
    say("Restarting…");
    try { await api.serverRestart(); } catch { /* it's re-execing */ }
    // It re-execs itself, so poll until it answers again rather than guessing
    // how long that takes.
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 500));
      try {
        const d = await api.tracksFresh();
        drawTracks(d.tracks);
        return say("Studio restarted.");
      } catch { /* still coming up */ }
    }
    goOffline("Restart didn't come back within 15 s — relaunch with "
            + "Castle Cue Desk.command.");
  });

  // URL imports live in track_import_url.ts — the one flow that talks to
  // the background job runner, with yt-dlp's own download ETA and a learned
  // one for the convert/analyse tail.
  wireUrlImport({ say, opts, drawTracks });

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
      try {
        const r = await api.importFile(file, opts());
        if (r.ok) { drawTracks(r.tracks);
                    say("Imported. Press Play to hear it, or “Make scene” to wire it in."); }
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
      // Static mode cannot convert anything, so the scene has to point at the
      // container the file already is — not at the .mp3 the studio would have
      // made of it.
      const ext = (file.name.match(/\.([a-z0-9]+)$/i)?.[1] || "mp3").toLowerCase();
      const env = loudnessEnvelope(audio.getChannelData(0), audio.sampleRate);
      const block = sceneYaml(id, audio.duration, counts, ext, deps.bands, env);
      T.yaml.hidden = false; T.yaml.textContent = block;
      const kb = Math.round(audio.duration * 96 * 1000 / 8 / 1024);
      say(`${file.name}: ${audio.duration.toFixed(1)}s, ~${kb} KB at 96 kbps. `
        + Object.entries(counts).map(([k, n]) => `${k.replace("onset_", "")} ${n}`).join(" · ")
        + `. Copy the block below, save the file into tracks/${id}.${ext}, then run make audio.`);
    } catch (err) { say(`Could not read that file — ${String(err)}`, true); }
  }

  return {
    stopPreview: () => preview.stop(),
    previewing: () => preview.playing() !== null,
  };
}
