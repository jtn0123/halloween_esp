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
  /** Tail of the underlying tool's output — the human-readable "why". */
  log?: string;
  tracks?: TrackInfo[];
  scenes?: string[];
  replaced?: boolean;
}

export interface WaveformResponse {
  id: string;
  duration: number;
  peaks: number[];
  onsets: Record<string, [number, number, number?][]>;
  env?: [number, number][];
}

export interface CompareResponse {
  ok: boolean; error?: string; reference?: string; codecs?: CodecRow[];
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

  remove: (id: string): Promise<ActionResponse> =>
    call(`/api/tracks/${encodeURIComponent(id)}`, { method: "DELETE" }),

  importUrl: (req: object): Promise<ActionResponse> =>
    call("/api/import", post(req), IMPORT),

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

  scene: (id: string, yaml: string): Promise<ActionResponse> =>
    call("/api/scene", post({ id, yaml }), ENCODE),

  compare: (req: object): Promise<CompareResponse> =>
    call("/api/compare", post(req), ENCODE),

  serverStop: (): Promise<ActionResponse> =>
    call("/api/server/stop", { method: "POST" }),
  serverRestart: (): Promise<ActionResponse> =>
    call("/api/server/restart", { method: "POST" }),
};
