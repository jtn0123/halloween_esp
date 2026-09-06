//! What a background job says about itself — studio_jobs.py's Job record,
//! the reader that turns yt-dlp's chatter into a phase and a percentage,
//! and the small JSON object the desk polls for.
//!
//! This half is arithmetic over strings: no children, no threads, no
//! registry. It lives apart from the runner next door because it is the
//! half a person actually sees — a phase that never leaves "queued" is
//! indistinguishable from a hang — and because both halves plus their
//! tests no longer fit in one file under the 500-line rule.

use crate::jsonio::Json;

/// One import, as far as the polling page is concerned.
pub struct Job {
    pub id: String,
    pub phase: String,
    pub percent: f64,
    pub detail: String,
    pub log: Vec<String>,
    pub error: String,
}

fn round1(v: f64) -> f64 {
    format!("{v:.1}").parse().unwrap_or(v)
}

impl Job {
    /// A job exists before its child does, and it says so: "queued" is what
    /// the page shows while the work is still in line behind the studio's
    /// encode lock (studio_jobs.py's dataclass defaults).
    pub fn new(id: String) -> Job {
        Job {
            id,
            phase: "queued".to_string(),
            percent: 0.0,
            detail: String::new(),
            log: Vec::new(),
            error: String::new(),
        }
    }

    pub fn as_json(&self) -> Json {
        let done = self.phase == "done" || self.phase == "failed";
        let tail = if self.log.len() > 40 {
            &self.log[self.log.len() - 40..]
        } else {
            &self.log[..]
        };
        Json::Obj(vec![
            ("id".into(), Json::Str(self.id.clone())),
            ("phase".into(), Json::Str(self.phase.clone())),
            ("percent".into(), Json::Num(round1(self.percent))),
            ("detail".into(), Json::Str(self.detail.clone())),
            ("error".into(), Json::Str(self.error.clone())),
            ("done".into(), Json::Bool(done)),
            (
                "log".into(),
                Json::Arr(tail.iter().map(|l| Json::Str(l.clone())).collect()),
            ),
        ])
    }
}

/// JobRunner._interpret — yt-dlp's progress line and the phase markers.
pub fn interpret(job: &mut Job, line: &str) {
    if let Some((pct, size, rate, eta)) = progress(line) {
        job.phase = "fetching".to_string();
        job.percent = pct;
        job.detail = size;
        if let Some(r) = rate {
            job.detail.push_str(&format!(" at {r}"));
        }
        if let Some(e) = eta {
            job.detail.push_str(&format!(", {e} left"));
        }
        return;
    }
    if line.contains("ExtractAudio") || line.starts_with("[ffmpeg]") {
        job.phase = "converting".to_string();
        job.percent = 100.0;
        job.detail = "extracting audio".to_string();
    } else if line.starts_with("imported ") {
        job.phase = "analysing".to_string();
        job.detail = "detecting onsets".to_string();
    }
}

/// `[download]  41.8% of ~2.39MiB at 15.81MiB/s ETA 00:00`
fn progress(line: &str) -> Option<(f64, String, Option<String>, Option<String>)> {
    let at = line.find("[download]")?;
    let mut rest = line[at + 10..].trim_start();
    let pct_end = rest.find('%')?;
    let pct: f64 = rest[..pct_end]
        .trim()
        .parse()
        .ok()
        .filter(|_| !rest[..pct_end].trim().is_empty())?;
    rest = rest[pct_end + 1..].trim_start();
    rest = rest.strip_prefix("of")?.trim_start();
    rest = rest.strip_prefix('~').map(str::trim_start).unwrap_or(rest);
    let mut words = rest.split_whitespace();
    let size = words.next()?.to_string();
    let toks: Vec<&str> = words.collect();
    let mut rate = None;
    let mut eta = None;
    let mut i = 0;
    while i + 1 < toks.len() {
        if toks[i] == "at" && rate.is_none() && eta.is_none() {
            rate = Some(toks[i + 1].to_string());
            i += 2;
        } else if toks[i] == "ETA" {
            eta = Some(toks[i + 1].to_string());
            break;
        } else {
            break;
        }
    }
    Some((pct, size, rate, eta))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The percentage the page draws comes from parsing stdout, so the
    /// parse is the feature. These are real lines, copied in shape.
    #[test]
    fn progress_lines_parse_like_the_regex() {
        let (pct, size, rate, eta) =
            progress("[download]  41.8% of 2.39MiB at 15.81MiB/s ETA 00:00").unwrap();
        assert_eq!(pct, 41.8);
        assert_eq!(size, "2.39MiB");
        assert_eq!(rate.as_deref(), Some("15.81MiB/s"));
        assert_eq!(eta.as_deref(), Some("00:00"));
        let (pct, size, rate, eta) = progress("[download] 100% of ~ 4.0MiB").unwrap();
        assert_eq!(
            (pct, size.as_str(), rate, eta),
            (100.0, "4.0MiB", None, None)
        );
        assert!(progress("[youtube] extracting").is_none());
    }

    /// `~` means yt-dlp is only guessing the total, and a rate it cannot
    /// measure is the word "Unknown" followed by a unit. Python's regex
    /// takes "Unknown" as the rate and then finds no `ETA` where one must
    /// be, so it reports no ETA at all — matching that exactly is the
    /// difference between "0.0% of 12.34MiB at Unknown" and a parse that
    /// silently reports 0% forever.
    #[test]
    fn an_estimated_total_with_an_unknown_rate_still_parses() {
        let (pct, size, rate, eta) =
            progress("[download]   0.0% of ~  12.34MiB at  Unknown B/s ETA Unknown").unwrap();
        assert_eq!(pct, 0.0);
        assert_eq!(size, "12.34MiB");
        assert_eq!(rate.as_deref(), Some("Unknown"));
        assert_eq!(eta, None);
    }

    /// yt-dlp says `[download]` about things that are not downloads yet;
    /// treating one as progress would jam the bar at whatever number the
    /// line happened to contain.
    #[test]
    fn a_line_without_a_percentage_is_not_progress() {
        assert!(progress("[youtube] Extracting URL: https://x").is_none());
        assert!(progress("[download] Destination: foo.webm").is_none());
    }

    /// The page can tell "in line" from "in trouble" only because a job
    /// admits it has not started.
    #[test]
    fn a_fresh_job_starts_queued() {
        let job = Job::new("x".to_string());
        assert_eq!(job.phase, "queued");
        assert_eq!(job.percent, 0.0);
        assert_eq!(job.detail, "");
        assert_eq!(job.error, "");
        assert!(job.log.is_empty());
    }

    /// The detail line is the only place the size, the rate and the wait
    /// are ever shown, so its wording is the feature and not a debug aid.
    #[test]
    fn a_progress_line_moves_the_job_to_fetching() {
        let mut job = Job::new("x".to_string());
        interpret(
            &mut job,
            "[download]  41.8% of 2.39MiB at 17.12MiB/s ETA 00:03",
        );
        assert_eq!(job.phase, "fetching");
        assert_eq!(job.percent, 41.8);
        assert_eq!(job.detail, "2.39MiB at 17.12MiB/s, 00:03 left");
    }

    /// The last line of a download drops the ETA, because there is nothing
    /// left to wait for. Printing "at , left" would be worse than nothing.
    #[test]
    fn the_detail_omits_a_rate_and_an_eta_that_were_never_printed() {
        let mut job = Job::new("x".to_string());
        interpret(&mut job, "[download] 100% of 2.39MiB");
        assert_eq!(job.detail, "2.39MiB");
    }

    /// The bytes are all in by now; the wait that remains is ffmpeg's, and
    /// it announces itself under two different names.
    #[test]
    fn audio_extraction_moves_the_job_to_converting() {
        for line in [
            "[ExtractAudio] Destination: clip.mp3",
            "[ffmpeg] Merging formats into \"clip.mkv\"",
        ] {
            let mut job = Job::new("x".to_string());
            interpret(&mut job, line);
            assert_eq!(job.phase, "converting", "{line}");
            assert_eq!(job.percent, 100.0, "{line}");
            assert_eq!(job.detail, "extracting audio", "{line}");
        }
    }

    /// import_track prints its one-line verdict when the audio is on disk
    /// and the onset pass begins — the last stretch, and the one with no
    /// progress output of its own.
    #[test]
    fn an_imported_line_moves_the_job_to_analysing() {
        let mut job = Job::new("x".to_string());
        interpret(&mut job, "imported chant  24.0s  onsets 40/88/31");
        assert_eq!(job.phase, "analysing");
        assert_eq!(job.detail, "detecting onsets");
    }

    /// The order is the story a waiting person reads. A phase that arrives
    /// out of turn — or twice — reads as a restart.
    #[test]
    fn a_real_imports_output_walks_the_phases_in_order() {
        let mut job = Job::new("x".to_string());
        let mut seen = vec![job.phase.clone()];
        for line in [
            "[youtube] Extracting URL: https://example.invalid/v",
            "[download]   5.0% of 2.39MiB at 1.00MiB/s ETA 00:03",
            "[download] 100% of 2.39MiB",
            "[ExtractAudio] Destination: clip.mp3",
            "imported clip  24.0s",
        ] {
            interpret(&mut job, line);
            if seen.last().map(String::as_str) != Some(job.phase.as_str()) {
                seen.push(job.phase.clone());
            }
        }
        assert_eq!(seen, ["queued", "fetching", "converting", "analysing"]);
    }

    /// yt-dlp warns about cosmetic things mid-download; a warning is not a
    /// change of phase, and treating it as one would walk the page
    /// backwards.
    #[test]
    fn cosmetic_warnings_leave_the_phase_alone() {
        let mut job = Job::new("x".to_string());
        job.phase = "fetching".to_string();
        interpret(&mut job, "WARNING: something cosmetic");
        assert_eq!(job.phase, "fetching");
    }

    /// The object is the wire format the polling page reads; its key set
    /// is a contract with web/src, not an internal dump.
    #[test]
    fn the_wire_shape_is_the_pages_contract() {
        let d = Job::new("abc123".to_string()).as_json();
        let keys: Vec<&str> = d
            .as_obj()
            .unwrap()
            .iter()
            .map(|(k, _)| k.as_str())
            .collect();
        assert_eq!(
            keys,
            ["id", "phase", "percent", "detail", "error", "done", "log"]
        );
        assert_eq!(d.get("id").and_then(Json::as_str), Some("abc123"));
        assert_eq!(d.get("phase").and_then(Json::as_str), Some("queued"));
        assert_eq!(d.get("percent").and_then(Json::as_f64), Some(0.0));
        assert_eq!(d.get("detail").and_then(Json::as_str), Some(""));
        assert_eq!(d.get("error").and_then(Json::as_str), Some(""));
        assert!(matches!(d.get("done"), Some(Json::Bool(false))));
        assert!(matches!(d.get("log"), Some(Json::Arr(v)) if v.is_empty()));
    }

    /// A progress bar does not need seven decimal places, and the number
    /// is rendered as text the moment it lands.
    #[test]
    fn the_percent_is_rounded_for_display() {
        let mut job = Job::new("x".to_string());
        job.percent = 41.833_333_3;
        assert_eq!(
            job.as_json().get("percent").and_then(Json::as_f64),
            Some(41.8)
        );
    }

    /// `done` is what stops the page polling, so it must be true for a
    /// failure as surely as for a success — and false for every phase
    /// where more output is still coming.
    #[test]
    fn done_is_true_for_the_two_terminal_phases_and_false_for_the_rest() {
        for phase in ["done", "failed"] {
            let mut job = Job::new("x".to_string());
            job.phase = phase.to_string();
            assert!(
                matches!(job.as_json().get("done"), Some(Json::Bool(true))),
                "{phase}"
            );
        }
        for phase in ["queued", "fetching", "converting", "analysing"] {
            let mut job = Job::new("x".to_string());
            job.phase = phase.to_string();
            assert!(
                matches!(job.as_json().get("done"), Some(Json::Bool(false))),
                "{phase}"
            );
        }
    }

    /// yt-dlp is chatty; shipping all of it on every poll would make the
    /// progress bar the most expensive part of the import. The tail is
    /// still enough to diagnose a failure.
    #[test]
    fn the_log_ships_only_its_tail() {
        let mut job = Job::new("x".to_string());
        job.log = (0..500).map(|i| format!("line {i}")).collect();
        let d = job.as_json();
        let Some(Json::Arr(tail)) = d.get("log") else {
            panic!("the log is not an array");
        };
        assert_eq!(tail.len(), 40);
        assert_eq!(tail[39].as_str(), Some("line 499"));
        assert_eq!(tail[0].as_str(), Some("line 460"));
    }
}
