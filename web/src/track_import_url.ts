/**
 * URL import — the background-job flow behind the GET button.
 *
 * Split out of tracks.ts at the 500-line cap, along a real seam: this is the
 * one flow that talks to the job runner, and nothing else in the panel needs
 * to know a job id exists.
 *
 * A long yt-dlp download used to sit behind one blocking POST, showing a
 * frozen "Importing…" for minutes with no sign of life. Now the server's own
 * progress parser feeds the status line: while downloading, yt-dlp's percent
 * and its OWN eta come through verbatim; the tail of the job (ffmpeg convert
 * + beat detection) has no server-side progress, so a learned ETA covers it.
 */

import { api, why, type JobResponse } from "./api.js";
import { startEta, type EtaHandle } from "./eta.js";
import type { ImportOpts } from "./import_opts.js";
import type { TrackStatus } from "./track_status.js";
import type { TrackInfo } from "./tracks.js";
import { req as byReq } from "./dom.js";

const PHASE_TEXT: Record<string, string> = {
  queued: "waiting for the previous job",
  fetching: "downloading",
  converting: "converting with ffmpeg",
  analysing: "detecting beats",
};

/** The status line while yt-dlp is downloading: its own percent and its own
 *  words about the file, verbatim — the server has already parsed them. */
function fetchLine(job: JobResponse): string {
  const pct = job.percent > 0 ? ` ${Math.round(job.percent)}%` : "";
  const detail = job.detail ? ` · ${job.detail}` : "";
  return `Importing — ${PHASE_TEXT[job.phase] ?? job.phase}${pct}${detail}`;
}

/** The tail's ETA lives in a box so the poll loop can start it and the
 *  caller's `finally` can still stop it when a poll throws. */
interface TailBox { eta: EtaHandle | null }

/**
 * Poll the job to its end, feeding the status line as it goes.
 *
 * 1200 polls at 800 ms is ~16 min — past the server's own 15-minute kill, so
 * the UI never gives up on a job the server still believes in.
 */
async function pollJob(started: JobResponse, progress: (msg: string) => void,
                       tail: TailBox): Promise<JobResponse> {
  let job = started;
  for (let i = 0; i < 1200 && !job.done; i++) {
    await new Promise(r => setTimeout(r, 800));
    job = await api.job(job.id);
    if (job.phase === "fetching" || job.phase === "queued") {
      progress(fetchLine(job));
    } else if (!job.done) {
      tail.eta ??= startEta("import-tail",
        "Importing — converting and detecting beats", null);
      progress(tail.eta.line());
    }
  }
  return job;
}

export interface UrlImportDeps {
  say(msg: string, err?: boolean): void;
  /** Progress goes on the import's own line, so a scene write or a
   *  re-import running at the same time cannot overwrite it (JB1-10). */
  status: TrackStatus;
  /** The Options row, as the import request wants it. */
  opts(): ImportOpts;
  drawTracks(tracks?: TrackInfo[]): void;
  /** A track landed: the options it used are consumed. */
  imported?(): void;
}

export function wireUrlImport({ say, status, opts, drawTracks, imported }: UrlImportDeps): void {
  const btn = byReq<HTMLButtonElement>("trkGet", "track_import_url");
  const urlBox = byReq<HTMLInputElement>("trkUrl", "track_import_url");

  btn.addEventListener("click", () => void (async () => {
    const url = urlBox.value.trim();
    if (!url) return say("Paste a link first.", true);
    btn.disabled = true;
    const progress = status.slot("import:url");
    progress("Importing…");
    /** Times the post-download tail, which reports no progress of its own. */
    const tail: TailBox = { eta: null };
    try {
      const started = await api.importAsync({ url, ...opts() });
      if (!started.id) {                   // refused at the door (bad url/id)
        return say(`Import failed — ${(started as { error?: string }).error
                     ?? "the studio refused the request"}`, true);
      }
      const job = await pollJob(started, progress, tail);
      if (job.phase === "done") {
        tail.eta?.stop(true);
        if (job.tracks) drawTracks(job.tracks);
        imported?.();
        say("Imported. Press Play to hear it, or “Make scene” to wire it into the show.");
        urlBox.value = "";
      } else {
        say(`Import failed — ${job.error || why({ log: job.log.join("\n") })
             || "gave up waiting"}`, true);
      }
    } catch (err) { say(`Import failed — ${String(err)}`, true); }
    finally {
      tail.eta?.stop();
      status.clear("import:url");
      btn.disabled = false;
    }
  })());
}
