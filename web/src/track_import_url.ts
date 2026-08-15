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

import { api } from "./api.js";
import { startEta, type EtaHandle } from "./eta.js";
import type { ImportOpts } from "./import_opts.js";
import type { TrackInfo } from "./tracks.js";

const PHASE_TEXT: Record<string, string> = {
  queued: "waiting for the previous job",
  fetching: "downloading",
  converting: "converting with ffmpeg",
  analysing: "detecting beats",
};

export interface UrlImportDeps {
  say(msg: string, err?: boolean): void;
  /** The Options row, as the import request wants it. */
  opts(): ImportOpts;
  drawTracks(tracks?: TrackInfo[]): void;
}

export function wireUrlImport({ say, opts, drawTracks }: UrlImportDeps): void {
  const btn = document.getElementById("trkGet") as HTMLButtonElement;
  const urlBox = document.getElementById("trkUrl") as HTMLInputElement;

  btn.addEventListener("click", () => void (async () => {
    const url = urlBox.value.trim();
    if (!url) return say("Paste a link first.", true);
    btn.disabled = true;
    say("Importing…");
    /** Times the post-download tail, which reports no progress of its own. */
    let tail: EtaHandle | null = null;
    try {
      let job = await api.importAsync(Object.assign({ url }, opts()));
      if (!job.id) {                       // refused at the door (bad url/id)
        return say(`Import failed — ${(job as { error?: string }).error
                     ?? "the studio refused the request"}`, true);
      }
      // ~16 min at 800 ms/poll — past the server's own 15-minute kill.
      for (let i = 0; i < 1200 && !job.done; i++) {
        await new Promise(r => setTimeout(r, 800));
        job = await api.job(job.id);
        if (job.phase === "fetching" || job.phase === "queued") {
          const pct = job.percent > 0 ? ` ${Math.round(job.percent)}%` : "";
          say(`Importing — ${PHASE_TEXT[job.phase] ?? job.phase}${pct}`
            + `${job.detail ? ` · ${job.detail}` : ""}`);
        } else if (!job.done) {
          tail ??= startEta("import-tail",
            "Importing — converting and detecting beats", null);
          say(tail.line());
        }
      }
      if (job.phase === "done") {
        tail?.stop(true);
        if (job.tracks) drawTracks(job.tracks);
        say("Imported. Press Play to hear it, or “Make scene” to wire it into the show.");
        urlBox.value = "";
      } else {
        say(`Import failed — ${job.error || job.log.slice(-3).join(" ")
             || "gave up waiting"}`, true);
      }
    } catch (err) { say(`Import failed — ${String(err)}`, true); }
    finally {
      tail?.stop();
      btn.disabled = false;
    }
  })());
}
