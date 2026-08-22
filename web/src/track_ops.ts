/**
 * The three slow row actions — Delete, Re-import, Make/Update scene.
 *
 * Split from tracks.ts at the 500-line cap along the seam judge B drew
 * (JB1-1/4/6/10): each of these is an operation with a busy state on its
 * row, its own status line, and a one-line reason when it fails. tracks.ts
 * keeps the panel's state and wiring; this file keeps what a click DOES.
 */

import { api, why } from "./api.js";
import { startEta } from "./eta.js";
import { fillOptsFrom, trimOwner, type ImportOpts } from "./import_opts.js";
import { sceneYaml } from "./track_scene.js";
import type { TrackStatus } from "./track_status.js";
import type { TrackInfo, TracksDeps } from "./tracks.js";
import { clock, parseClock, saveClip } from "./wave_clip.js";

export interface OpsCtx {
  T: {
    selected: string | null;
    tracks: TrackInfo[];
    sceneIds: Set<string>;
    /** id → act in flight; the row renders from it (track_rows.ts). */
    busy: Map<string, string>;
  };
  status: TrackStatus;
  say: (msg: string, err?: boolean) => void;
  drawTracks: (tracks?: TrackInfo[]) => void;
  refresh: () => void;
  deps: TracksDeps;
  /** The Options row as it stands. */
  opts: () => ImportOpts;
  preview: { playing(): string | null; stop(): void };
  yaml: HTMLElement;
}

function busy(ctx: OpsCtx, id: string, act: string | null): void {
  if (act) ctx.T.busy.set(id, act); else ctx.T.busy.delete(id);
  ctx.drawTracks();
}

/** Delete a track — and, when it is in the show, offer to take its scene
 *  out of scenes.yaml with it rather than leave a scene pointing at a file
 *  that is gone (the next render failed for a reason nobody caused). */
export async function deleteTrack(ctx: OpsCtx, id: string): Promise<void> {
  const inShow = ctx.T.sceneIds.has(id);
  const ask = inShow
    ? `"${id}" is IN THE SHOW.\n\nDelete the file AND take its scene out of `
      + `scenes.yaml? The show is re-rendered without it.\n\nCancel keeps both.`
    : `Delete track "${id}"? The file is removed from tracks/.`;
  if (!confirm(ask)) return;
  if (ctx.preview.playing() === id) ctx.preview.stop();
  busy(ctx, id, "del");
  const key = `del:${id}`;
  const eta = inShow
    ? startEta("scene", `Deleting ${id} and removing its scene — re-rendering the show`,
               ctx.status.slot(key))
    : null;
  try {
    const r = await api.remove(id, inShow);
    eta?.stop(r.ok);
    if (!r.ok) {
      ctx.say(`Could not delete ${id} — ${why(r)}`, true);
    } else if (inShow) {
      ctx.T.sceneIds = new Set(r.scenes ?? [...ctx.T.sceneIds].filter(x => x !== id));
      ctx.status.sayReload(`Deleted ${id} and removed its scene; the show re-rendered.`);
    } else {
      ctx.say(`Deleted ${id}.`);
    }
  } catch (err) {
    ctx.say(`Could not delete ${id} — ${String(err)}`, true);
  } finally {
    eta?.stop();
    ctx.status.clear(key);
    busy(ctx, id, null);
  }
  if (ctx.T.selected === id) { ctx.T.selected = null; ctx.deps.onSelect?.(null); }
  ctx.refresh();
}

/**
 * Rebuild from the remembered source with the Options row's settings.
 *
 * START/LENGTH mean three different things depending on who wrote them:
 *   - the clip editor, open on THIS track: a selection of the file as it
 *     is now, so it is offset by the remembered start — re-cutting an
 *     already-cut track used to count from the wrong origin (JB1-4);
 *   - the clip editor, open on another track: not ours, ignored (JB1-1);
 *   - a person: taken as typed, counted from the source like the CLI.
 */
export async function reimportTrack(ctx: OpsCtx, id: string): Promise<void> {
  const t = ctx.T.tracks.find(x => x.id === id);
  if (!t) return;
  if (t.source_missing) {
    return ctx.say(`${id} cannot be re-imported — its original file is gone. `
                 + `Drop the file again to import it afresh.`, true);
  }
  if (ctx.T.selected !== id) fillOptsFrom(t);
  const o = ctx.opts();
  const owner = trimOwner();
  let start = o.start, take = o.take, cut = "";
  if (owner === id) {
    const base = parseClock(String(t.opts?.start ?? "0")) ?? 0;
    start = clock(base + (parseClock(start) ?? 0));
    cut = take ? ` — keeping ${take}s from ${start} of the source` : "";
  } else if (owner !== null) {
    start = ""; take = "";
  }
  const key = `reimport:${id}`;
  const eta = startEta("reimport", `Re-importing ${id} from its remembered source${cut}`,
                       ctx.status.slot(key));
  busy(ctx, id, "refresh");
  try {
    const r = await api.refresh({
      id, start, take, sensitivity: o.sensitivity, bitrate: o.bitrate,
      sample_rate: o.sample_rate, channels: o.channels, format: o.format,
      // "0" rather than blank: blank means "keep the remembered fade", so
      // a fade could never be cleared from here.
      fade_in: o.fade_in || "0", fade_out: o.fade_out || "0",
      normalize: o.normalize,
    });
    eta.stop(r.ok);
    if (!r.ok) {
      ctx.say(`Re-import of ${id} failed — ${why(r)}`, true);
      if (r.tracks) ctx.drawTracks(r.tracks);
      return;
    }
    ctx.drawTracks(r.tracks);
    // Say what came OUT, not just that something happened: fourteen
    // silent seconds ending in an unchanged row read as a dead button.
    const after = (r.tracks || []).find(x => x.id === id);
    const ch = after?.opts?.channels === 2 ? "stereo" : "mono";
    ctx.say(`Re-imported ${id} — ${ch}${cut}.`
      + (ch === "mono"
        ? " Still mono: click its mono ⚠ badge (or set CHANNELS to stereo in"
          + " Options) and Re-import again."
        : ""));
    // The editor is showing the OLD bytes; its remembered selection
    // described them too. Forget the clip and reload the track.
    if (ctx.T.selected === id) {
      saveClip(id, null, undefined);
      ctx.deps.onSelect?.(null);
      ctx.deps.onSelect?.(id);
    }
  } catch (err) {
    ctx.say(`Re-import of ${id} failed — ${String(err)}`, true);
  } finally {
    eta.stop();
    ctx.status.clear(key);
    busy(ctx, id, null);
  }
}

/** Write the track's scene into scenes.yaml and re-render the show. */
export async function makeScene(ctx: OpsCtx, id: string): Promise<void> {
  busy(ctx, id, "scene");
  const key = `scene:${id}`;
  let eta: { stop(learn?: boolean): void } | null = null;
  try {
    const r = await api.tracks();
    const t = (r.tracks || []).find(x => x.id === id);
    if (!t) return ctx.say(`${id} is no longer in the library.`, true);
    // No duration means the import never produced playable audio; a scene
    // with `duration_ms: NaN` would break the next render for everyone.
    if (!t.dur) {
      return ctx.say(`${id} has no playable audio (its import failed) — `
                   + `Re-import it, or delete it.`, true);
    }
    // The loudness envelope drives the scene's quiet/verse/chorus set
    // cues. Missing it degrades to one standing look, not to a failure.
    const wf = await api.waveform(id).then(w => w.body).catch(() => null);
    const block = sceneYaml(id, t.dur, t.onsets || {}, t.ext, ctx.deps.bands, wf?.env);
    ctx.yaml.hidden = false; ctx.yaml.textContent = block;
    eta = startEta("scene",
      `Writing scene "${id}" into scenes.yaml and re-rendering the show`,
      ctx.status.slot(key));
    const res = await api.scene(id, block);
    eta.stop(res.ok);
    if (!res.ok) return ctx.say(`Scene write failed — ${why(res)}`, true);
    // The row now says "in the show", which is the visible proof the click
    // did something. The page's own scene list is baked in at generate
    // time, so it needs a reload — offered, not forced.
    ctx.T.sceneIds = new Set(res.scenes || [...ctx.T.sceneIds, id]);
    const sandbox = /^sandbox:/m.test(res.log ?? "");
    ctx.status.sayReload(`Scene "${id}" ${res.replaced ? "updated" : "added"} in `
      + `scenes.yaml and the audio re-rendered${sandbox ? " (sandbox build)" : ""}.`);
  } catch (err) {
    ctx.say(`Scene write failed — ${String(err)}`, true);
  } finally {
    eta?.stop();
    ctx.status.clear(key);
    busy(ctx, id, null);
  }
}
