/**
 * The typed doorway to the studio server — every request the desk makes to
 * tools/studio.py goes through here.
 *
 * Twenty-eight hand-rolled fetch() calls with `as`-cast response shapes was
 * the biggest type-safety hole left in the app: a renamed field on the
 * server failed at paint time, in whichever panel happened to touch it
 * first. Now the contract lives in ONE file, next to the timeouts.
 *
 * Mostly NOT here: the castle's own /api/* endpoints (device.ts,
 * device_panel.ts). The studio owns /studio/* and relays /api/* to the
 * castle untouched (docs/API.md) — the studio authors the show, the device
 * performs it — and keeping the two contracts in separate files, under
 * separate prefixes, is what stops a call site from silently talking to
 * the wrong one. The two castle reads the Tracks panel makes to reconcile
 * the library with the card (`castleFiles`, `castleStatus`) sit at the
 * bottom, named for what they are, so track_send.ts has no raw fetch.
 *
 * Application failures (ok:false) are returned, not thrown: the call sites
 * own their wording, and the server's `log` tail is part of the message.
 * Thrown errors mean the conversation itself failed: no server, a timeout,
 * or a non-JSON reply.
 */

import type { CodecRow, SdFile, TrackInfo } from "./types.js";

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

/** /studio/stems/<id>: three layers × three channels, plus freshness. */
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

/** One background import, as /studio/import/async and /studio/job/<id> report it. */
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
    call("/studio/tracks"),
  tracksFresh: (): Promise<TracksResponse> =>
    call("/studio/tracks", { cache: "no-store" }),

  /** `withScene`: also take its scene out of scenes.yaml and re-render. */
  remove: (id: string, withScene = false): Promise<ActionResponse> =>
    call(`/studio/tracks/${encodeURIComponent(id)}${withScene ? "?scene=1" : ""}`,
         { method: "DELETE" }, withScene ? ENCODE : QUICK),

  importUrl: (req: object): Promise<ActionResponse> =>
    call("/studio/import", post(req), IMPORT),

  /** Start a background URL import; poll `job()` for progress. */
  importAsync: (req: object): Promise<JobResponse> =>
    call("/studio/import/async", post(req)),
  job: (id: string): Promise<JobResponse> =>
    call(`/studio/job/${encodeURIComponent(id)}`),

  importFile: (file: File, opts: unknown): Promise<ActionResponse> => {
    const fd = new FormData();
    fd.append("file", file);
    return call("/studio/import", {
      method: "POST",
      headers: { "X-Import-Opts": JSON.stringify(opts) },
      body: fd,
    }, IMPORT);
  },

  refresh: (req: object): Promise<ActionResponse> =>
    call("/studio/refresh", post(req), IMPORT),

  /** status too, because a 404 here is an ordinary outcome (track deleted
   *  under the panel) that deserves its own sentence, not a red error. */
  waveform: async (id: string, query = ""):
      Promise<{ status: number; body: WaveformResponse | null }> => {
    const res = await fetch(
      `/studio/waveform/${encodeURIComponent(id)}${query ? `?${query}` : ""}`,
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
    const res = await fetch(`/studio/stems/${encodeURIComponent(id)}`,
      { signal: AbortSignal.timeout(QUICK) });
    try {
      return { status: res.status, body: await res.json() as StemsResponse };
    } catch {
      return { status: res.status, body: null };
    }
  },
  /** Start a background Demucs split; poll `job()` for progress. */
  stemsSplit: (id: string, force = false): Promise<JobResponse> =>
    call("/studio/stems", post({ id, force })),

  scene: (id: string, yaml: string): Promise<ActionResponse> =>
    call("/studio/scene", post({ id, yaml }), ENCODE),

  compare: (req: object): Promise<CompareResponse> =>
    call("/studio/compare", post(req), ENCODE),

  serverStop: (): Promise<ActionResponse> =>
    call("/studio/server/stop", { method: "POST" }),
  serverRestart: (): Promise<ActionResponse> =>
    call("/studio/server/restart", { method: "POST" }),

  /** A track's bytes, for sending to the card. Throws on any non-2xx. */
  trackBytes: async (id: string): Promise<Blob> => {
    const r = await fetch(`/studio/track/${encodeURIComponent(id)}`,
                          { signal: AbortSignal.timeout(ENCODE) });
    if (!r.ok) throw new Error(String(r.status));
    return r.blob();
  },
  /** A file off the castle's card, relayed by the studio. The raw Response:
   *  the caller words a 404 differently from a dead castle (failReason). */
  cardFile: (name: string): Promise<Response> =>
    fetch(`/studio/card/${encodeURIComponent(name)}`,
          { signal: AbortSignal.timeout(ENCODE) }),

  /* ── the castle, through the page's origin (relayed or local) ── */

  /** The card listing. Throws when no castle answers. */
  castleFiles: async (): Promise<SdFile[]> => {
    const r = await fetch("/api/files", { signal: AbortSignal.timeout(QUICK) });
    if (!r.ok) throw new Error(String(r.status));
    return r.json() as Promise<SdFile[]>;
  },
  /** The castle's status line — here only for `sd_free_kb`; device.ts owns
   *  the probe that decides simulator-vs-device. */
  castleStatus: async (): Promise<{ sd_free_kb?: number }> => {
    const r = await fetch("/api/status", { signal: AbortSignal.timeout(QUICK) });
    if (!r.ok) throw new Error(String(r.status));
    return r.json() as Promise<{ sd_free_kb?: number }>;
  },

  /* The five calls below moved here from device.ts / device_panel.ts
     (grade report A4): one shared timeout convention, one place the wire
     shapes are cast, no bespoke error path per call site. */

  /** The probe that decides simulator-vs-device. The raw Response — the
   *  caller reads the studio marker out of the body itself. */
  castleProbe: (timeoutMs: number): Promise<Response> =>
    fetch("/api/status", { signal: AbortSignal.timeout(timeoutMs) }),

  /** One castle action (scene, stop, volume, light, pir, delete…). The raw
   *  Response: castleAct words a failure from the castle's own reply. */
  castleAction: (path: string, method: "POST" | "DELETE"): Promise<Response> =>
    fetch(path, { method, signal: AbortSignal.timeout(QUICK) }),

  /** A castle JSON read the panel renders whole (status, files). Throws on
   *  any non-2xx — the panel's catch renders "stopped answering". */
  castleGet: async <T>(path: "/api/status" | "/api/files"): Promise<T> => {
    const r = await fetch(path, { signal: AbortSignal.timeout(QUICK) });
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return (await r.json()) as T;
  },

  /** The castle's boot log, verbatim. */
  castleBootlog: async (): Promise<string> => {
    const r = await fetch("/api/bootlog", { signal: AbortSignal.timeout(QUICK) });
    return r.text();
  },

  /** PUT a file onto the card root. ENCODE, not QUICK: a multi-MB track
   *  over porch WiFi is minutes, and the firmware feeds the watchdog per
   *  chunk rather than hurrying. */
  castlePut: (name: string, body: Blob): Promise<Response> =>
    fetch(`/api/files/${encodeURIComponent(name)}`,
          { method: "PUT", body, signal: AbortSignal.timeout(ENCODE) }),
};
