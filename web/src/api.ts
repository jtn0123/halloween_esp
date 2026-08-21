/**
 * The typed doorway to the studio server — every request the desk makes to
 * tools/studio.py goes through here.
 *
 * Twenty-eight hand-rolled fetch() calls with `as`-cast response shapes was
 * the biggest type-safety hole left in the app: a renamed field on the
 * server failed at paint time, in whichever panel happened to touch it
 * first. Now the contract lives in ONE file, next to the timeouts.
 *
 * Deliberately NOT here: the castle's own /api endpoints (device.ts,
 * device_panel.ts). Same paths, different machine, different semantics —
 * the studio authors the show, the device performs it — and keeping the
 * two contracts in separate files is what stops a call site from silently
 * talking to the wrong one.
 *
 * Application failures (ok:false) are returned, not thrown: the call sites
 * own their wording, and the server's `log` tail is part of the message.
 * Thrown errors mean the conversation itself failed: no server, a timeout,
 * or a non-JSON reply.
 */

import type { CodecRow } from "./codec_ab.js";
import type { TrackInfo } from "./tracks.js";

export interface TracksResponse { tracks?: TrackInfo[]; scenes?: string[] }

export interface ActionResponse {
  ok: boolean;
  error?: string;
  /** Tail of the underlying tool's output, for the curious. */
  log?: string;
  /** The server's one-line verdict on a failure (studio_jobs.reason):
   *  the last meaningful line, basenames not paths, never a traceback. */
  reason?: string;
  tracks?: TrackInfo[];
  scenes?: string[];
  replaced?: boolean;
  /** DELETE ?scene=1: whether a scene block was taken out with the track. */
  scene_removed?: boolean;
}

/** The one line to show a person for a failed action. */
export function why(r: { reason?: string; error?: string; log?: string }): string {
  if (r.reason) return r.reason;
  if (r.error) return r.error;
  const lines = (r.log || "").split("\n").map(l => l.trim()).filter(Boolean);
  return lines[lines.length - 1] ?? "no reason given";
}

export interface WaveformResponse {
  id: string;
  duration: number;
  peaks: number[];
  onsets: Record<string, [number, number, number?][]>;
  env?: [number, number][];
}

/** One (layer, channel) picture from the stems analysis. */
export interface StemChannel {
  peaks: number[];
  /** Raw peak level of the channel before per-channel normalisation —
   *  how the two sides genuinely compare in loudness. */
  level: number;
  onsets: Record<string, [number, number][]>;
}

/** /api/stems/<id>: three layers × three channels, plus freshness. */
export interface StemsResponse {
  ok: boolean;
  error?: string;
  id?: string;
  duration?: number;
  /** vocals | backing | combined  →  left | right | both */
  layers?: Record<string, Record<string, StemChannel>>;
  /** True when the track was re-imported after the split — the stems on
   *  disk describe audio that no longer exists. */
  stale?: boolean;
}

export interface CompareResponse {
  ok: boolean; error?: string; reference?: string; codecs?: CodecRow[];
}

/** One background import, as /api/import/async and /api/job/<id> report it. */
export interface JobResponse {
  id: string;
  /** queued | fetching | converting | analysing | done | failed */
  phase: string;
  percent: number;
  detail: string;
  error: string | null;
  done: boolean;
  log: string[];
  /** Present on the final poll only, so the panel can redraw. */
  tracks?: TrackInfo[];
}

/* One ceiling per kind of wait. The server's own subprocess timeouts are
   the real backstop (studio.py run()); these just keep the UI honest when
   the server itself is gone. */
const QUICK = 30_000;              // lists, waveforms: seconds at most
const ENCODE = 5 * 60_000;         // codec compare, scene write + re-render
const IMPORT = 16 * 60_000;        // yt-dlp download: server gives up at 15

async function call<T>(path: string, init: RequestInit = {},
                       timeoutMs = QUICK): Promise<T> {
  const res = await fetch(path,
    { ...init, signal: AbortSignal.timeout(timeoutMs) });
  try {
    return await res.json() as T;
  } catch {
    throw new Error(`the studio sent no JSON (HTTP ${res.status})`);
  }
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  tracks: (): Promise<TracksResponse> =>
    call("/api/tracks"),
  tracksFresh: (): Promise<TracksResponse> =>
    call("/api/tracks", { cache: "no-store" }),

  /** `withScene`: also take its scene out of scenes.yaml and re-render. */
  remove: (id: string, withScene = false): Promise<ActionResponse> =>
    call(`/api/tracks/${encodeURIComponent(id)}${withScene ? "?scene=1" : ""}`,
         { method: "DELETE" }, withScene ? ENCODE : QUICK),

  importUrl: (req: object): Promise<ActionResponse> =>
    call("/api/import", post(req), IMPORT),

  /** Start a background URL import; poll `job()` for progress. */
  importAsync: (req: object): Promise<JobResponse> =>
    call("/api/import/async", post(req)),
  job: (id: string): Promise<JobResponse> =>
    call(`/api/job/${encodeURIComponent(id)}`),

  importFile: (file: File, opts: unknown): Promise<ActionResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    return call("/api/import", {
      method: "POST",
      headers: { "X-Import-Opts": JSON.stringify(opts) },
      body: fd,
    }, IMPORT);
  },

  refresh: (req: object): Promise<ActionResponse> =>
    call("/api/refresh", post(req), IMPORT),

  /** status too, because a 404 here is an ordinary outcome (track deleted
   *  under the panel) that deserves its own sentence, not a red error. */
  waveform: async (id: string, query = ""):
      Promise<{ status: number; body: WaveformResponse | null }> => {
    const res = await fetch(
      `/api/waveform/${encodeURIComponent(id)}${query ? `?${query}` : ""}`,
      { signal: AbortSignal.timeout(ENCODE) });
    return {
      status: res.status,
      body: res.ok ? await res.json() as WaveformResponse : null,
    };
  },

  /** status too: a 404 means "not split yet", which is a state the panel
   *  renders (a Split button), not an error it reports. */
  stems: async (id: string):
      Promise<{ status: number; body: StemsResponse | null }> => {
    const res = await fetch(`/api/stems/${encodeURIComponent(id)}`,
      { signal: AbortSignal.timeout(QUICK) });
    try {
      return { status: res.status, body: await res.json() as StemsResponse };
    } catch {
      return { status: res.status, body: null };
    }
  },
  /** Start a background Demucs split; poll `job()` for progress. */
  stemsSplit: (id: string, force = false): Promise<JobResponse> =>
    call("/api/stems", post({ id, force })),

  scene: (id: string, yaml: string): Promise<ActionResponse> =>
    call("/api/scene", post({ id, yaml }), ENCODE),

  compare: (req: object): Promise<CompareResponse> =>
    call("/api/compare", post(req), ENCODE),

  serverStop: (): Promise<ActionResponse> =>
    call("/api/server/stop", { method: "POST" }),
  serverRestart: (): Promise<ActionResponse> =>
    call("/api/server/restart", { method: "POST" }),
};
