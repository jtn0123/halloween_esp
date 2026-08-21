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

import { api, why } from "./api.js";
import type { BandEditor } from "./band_editor.js";
import { clearTrim, fillOptsFrom, forImport, initImportOpts } from "./import_opts.js";
import { cardRowsHtml, mountCard, renderSyncButton, sendAction } from "./track_card.js";
import { cardState } from "./track_send.js";
import { trackRowHtml } from "./track_rows.js";
import { analyseLocally, wireDrop } from "./track_drop.js";
import { wireUrlImport } from "./track_import_url.js";
import { deleteTrack, makeScene, reimportTrack } from "./track_ops.js";
import { createStatus } from "./track_status.js";
import { createPreview } from "./preview.js";
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
  /** Exact size in bytes — what proves a card copy current vs stale. */
  bytes?: number;
  /** Duration in seconds; missing if ffprobe could not say. */
  dur?: number;
  /** Where it came from — a URL, or `file:<path>` (tracks/_src/<id>.<ext>
   *  for a dropped or card-pulled file, kept so Re-import can work). */
  source: string;
  /** A file: source whose file has since gone — no Re-import possible. */
  source_missing?: boolean;
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

/** Is this page on the laptop that runs the studio? Restart / Stop-server
 *  belong to that machine alone: a phone on the LAN pressing Stop would take
 *  the server out from under everyone (JB1-7). */
export const servedLocally = (): boolean =>
  /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);

export function initTracks(deps: TracksDeps): TracksApi {
  const SCENES = deps.scenes;

  /* The ids are the contract with the generated HTML. A missing one is a bug
     in the template, and the cast lets it fail as a TypeError at first use —
     the same way the untyped version did — instead of quietly doing nothing. */
  const byId = <T extends HTMLElement = HTMLElement>(id: string): T =>
    document.getElementById(id) as T;

  const T = {
    mode: "static" as "static" | "studio",
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
    /** The first /api/tracks answer has landed. Until then the library
     *  says "loading", not EMPTY, and the card's files are not drawn as
     *  "castle only" — which read as "my songs are gone" (JB1-5). */
    loaded: false,
    /** id → act in flight on that row (scene | refresh | del). */
    busy: new Map<string, string>(),
    /** name → bytes on the castle's SD card; null when no castle answers.
     *  Feeds badges, card-only rows and Sync (dogfood 005/006). */
    onCard: null as Map<string, number> | null,
  };
  // One headline plus a line per operation in flight — concurrent jobs
  // used to overwrite each other's progress (track_status.ts).
  const status = createStatus(byId("trkNote"));
  const say = status.say;
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
      if (!d.tracks && !d.scenes) return Promise.reject(new Error("no list"));
      T.mode = "studio"; T.modeEl.textContent = "studio · connected";
      byId("trkServer").hidden = !servedLocally();
      T.sceneIds = new Set(d.scenes || []);
      T.loaded = true;
      drawTracks(d.tracks);
      say("Import by link or drop a file. Press Play to hear a track, "
        + "or click its row to trim it."); })
    .catch(() => {
      T.mode = "static";
      T.loaded = true;
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

  // The castle's card: badges, card-only rows, Sync — track_card.ts owns
  // the wiring; this panel only lends it the list and the redraw.
  const cardCtx = () => ({ tracks: T.tracks, sceneIds: T.sceneIds,
                           card: T.onCard, canPull: T.mode === "studio" });
  const loadCard = mountCard({
    list: T.list, syncBtn: byId<HTMLButtonElement>("trkSync"), ctx: cardCtx,
    say, importFile: f => takeFile(f), active: () => T.mode === "studio",
    apply: c => { T.onCard = c; drawTracks(undefined); } });

  function drawTracks(tracks: TrackInfo[] | undefined): void {
    if (tracks) T.tracks = tracks;
    const playingId = preview.playing();
    // Row markup lives in track_rows.ts; card-only rows and the Sync button
    // follow the same redraw so the merged view never goes stale.
    T.list.innerHTML = T.tracks.map(t => trackRowHtml(t, {
      selected: T.selected === t.id,
      inShow: T.sceneIds.has(t.id),
      sounding: playingId === t.id,
      onCastle: cardState(t, T.onCard),
      busy: T.busy.get(t.id) ?? null,
    })).join("") + (T.loaded ? cardRowsHtml(cardCtx()) : "");
    renderSyncButton(byId<HTMLButtonElement>("trkSync"), cardCtx());
    const n = T.tracks.length;
    T.count.textContent = !T.loaded ? "loading library…"
      : n === 0 ? "empty" : `${n} imported`;
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

  // The slow three — Delete, Re-import, Make/Update scene — live in
  // track_ops.ts with their busy states and status lines.
  const ops = {
    T, status, say, drawTracks: (t?: TrackInfo[]) => drawTracks(t),
    refresh: () => refresh(), deps, opts, preview, yaml: T.yaml,
  };
  T.list.addEventListener("click", async e => {
    const btn = (e.target as HTMLElement | null)?.closest("button"); if (!btn) return;
    if (btn.dataset["cardact"]) return;   // card-only rows: track_card.ts owns them
    // Every button is rendered inside a .trk carrying the id, so the row and
    // the attribute are both there or the markup above is broken.
    const id = btn.closest<HTMLElement>(".trk")!.dataset["id"] ?? "";
    const act = btn.dataset["act"];
    if (act === "play") {
      preview.toggle(id);
    } else if (act === "send") {
      const t = T.tracks.find(x => x.id === id);
      if (t) await sendAction(btn as HTMLButtonElement, t, say, loadCard);
    } else if (T.busy.has(id)) {
      return;                            // its row is already working
    } else if (act === "del") {
      await deleteTrack(ops, id);
    } else if (act === "refresh") {
      await reimportTrack(ops, id);
    } else if (act === "scene") {
      await makeScene(ops, id);
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
    byId<HTMLButtonElement>("trkSync").hidden = true;
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
  // Trim values the clip editor wrote belong to ITS track — a new import
  // takes only what a person typed (forImport), and consumes it.
  const importOpts = () => forImport(opts());
  const consumed = (): void => {
    clearTrim();
    byId<HTMLInputElement>("trkId").value = "";
  };
  wireUrlImport({ say, status, opts: importOpts, drawTracks, imported: consumed });

  /* ── Drag and drop ── track_drop.ts owns the zone and the no-server
     analysis; this panel only decides studio-vs-static. */
  wireDrop(byId("trkDrop"), f => void takeFile(f));

  async function takeFile(file: File): Promise<void> {
    if (T.mode === "studio") {
      const progress = status.slot(`import:${file.name}`);
      progress(`Uploading ${file.name}…`);
      try {
        const r = await api.importFile(file, importOpts());
        if (r.ok) { drawTracks(r.tracks); consumed();
                    say("Imported. Press Play to hear it, or “Make scene” to wire it in."); }
        else { if (r.tracks) drawTracks(r.tracks);
               say(`Import of ${file.name} failed — ${why(r)}`, true); }
      } catch (err) { say(`Import of ${file.name} failed — ${String(err)}`, true); }
      finally { status.clear(`import:${file.name}`); }
      return;
    }
    // Static mode: analyse right here.
    say(`Analysing ${file.name} in the browser…`);
    try {
      const a = await analyseLocally(file, importOpts().id, deps.bands);
      T.yaml.hidden = false; T.yaml.textContent = a.block;
      say(a.summary);
    } catch (err) { say(`Could not read that file — ${String(err)}`, true); }
  }

  return {
    stopPreview: () => preview.stop(),
    previewing: () => preview.playing() !== null,
  };
}
