//! The one-line verdicts — studio_jobs.py's _explain/reason half: turn a
//! tool's whole output into the single line worth showing a person. Raw
//! shell output in a UI is a failure of nerve; the interesting line is
//! almost always in there, it just needs finding.

const NO_DEMUCS: &str = "Demucs is not installed in the studio's Python — pip install demucs.";

/// Phrases worth a sentence — studio_jobs.KNOWN, verbatim.
const KNOWN: [(&str, &str); 14] = [
    ("Private video", "That video is private."),
    ("Video unavailable", "That video is unavailable."),
    (
        "Sign in to confirm",
        "That video needs a signed-in account.",
    ),
    ("members-only", "That video is members-only."),
    ("is not a valid URL", "That does not look like a link."),
    (
        "Unsupported URL",
        "Nothing here knows how to read that link.",
    ),
    ("HTTP Error 404", "That link is a dead end (404)."),
    ("no audio file", "The download produced no audio."),
    (
        "Requested format",
        "No audio-only format was offered for that video.",
    ),
    ("No module named demucs", NO_DEMUCS),
    ("No module named 'demucs'", NO_DEMUCS),
    ("out of memory", "Ran out of memory — try a shorter track."),
    (
        "No such file or directory: 'ffmpeg'",
        "ffmpeg is not installed — brew install ffmpeg.",
    ),
    (
        "ffmpeg: command not found",
        "ffmpeg is not installed — brew install ffmpeg.",
    ),
];

const EXC_TAIL: [&str; 4] = ["Error", "Exception", "Exit", "Interrupt"];

/// JobRunner._explain — one line worth showing a person.
pub fn explain(log: &[String]) -> String {
    let text = log.join("\n").to_lowercase();
    for (needle, friendly) in KNOWN {
        if text.contains(&needle.to_lowercase()) {
            return friendly.to_string();
        }
    }
    for line in log.iter().rev() {
        if line.contains("ERROR") {
            let tail = line.split_once("ERROR:").map_or("", |x| x.1).trim();
            let pick = if tail.is_empty() { line.as_str() } else { tail };
            return basenames(pick);
        }
    }
    for line in log.iter().rev() {
        if let Some((name, rest)) = exc_match(line) {
            if EXC_TAIL.iter().any(|t| name.ends_with(t)) {
                return basenames(&exception_line(&name, rest.as_deref()));
            }
        }
    }
    for line in log.iter().rev() {
        let lt = line.trim();
        if !lt.is_empty()
            && !line.chars().next().is_some_and(char::is_whitespace)
            && !line.starts_with('[')
            && !line.starts_with("Traceback")
        {
            return basenames(lt);
        }
    }
    String::new()
}

/// `^([A-Za-z_][\w.]*)(?::\s*(.*))?$`
fn exc_match(line: &str) -> Option<(String, Option<String>)> {
    let b = line.as_bytes();
    if b.is_empty() || !(b[0].is_ascii_alphabetic() || b[0] == b'_') {
        return None;
    }
    let mut i = 1;
    while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_' || b[i] == b'.') {
        i += 1;
    }
    let name = line[..i].to_string();
    if i == b.len() {
        return Some((name, None));
    }
    if b[i] != b':' {
        return None;
    }
    Some((name, Some(line[i + 1..].trim_start().to_string())))
}

/// "Command '['ffmpeg', …]' returned non-zero exit status 1." and friends.
fn exception_line(name: &str, rest: Option<&str>) -> String {
    let rest = rest.unwrap_or("").trim();
    if let Some(at) = rest.find("Command '['") {
        let after = &rest[at + 11..];
        if let Some(end) = after.find('\'') {
            let prog = after[..end].rsplit('/').next().unwrap_or("");
            let code = rest
                .find("exit status ")
                .map(|p| {
                    let tail: String = rest[p + 12..]
                        .chars()
                        .take_while(|c| c.is_ascii_digit() || *c == '-')
                        .collect();
                    tail
                })
                .filter(|s| !s.is_empty());
            return match code {
                Some(c) => format!("{prog} failed (exit {c})"),
                None => format!("{prog} failed"),
            };
        }
    }
    if rest.is_empty() {
        name.rsplit('.').next().unwrap_or(name).to_string()
    } else {
        rest.to_string()
    }
}

/// studio_jobs.basenames — '/a/b/x.wav' → 'x.wav'; URLs untouched
/// (a '/' preceded by ':', '/' or a word character never starts a match).
pub fn basenames(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = String::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'/' {
            let prev_ok = i == 0 || {
                let p = b[i - 1];
                !(p == b':' || p == b'/' || p.is_ascii_alphanumeric() || p == b'_')
            };
            if prev_ok {
                // Consume one-or-more `[^/\s'"]+/` segments possessively.
                let mut j = i + 1;
                let mut last_slash = None;
                let mut seg_len = 0;
                while j < b.len() {
                    let c = b[j];
                    if c == b'/' {
                        if seg_len == 0 {
                            break;
                        }
                        last_slash = Some(j);
                        seg_len = 0;
                        j += 1;
                    } else if c.is_ascii_whitespace() || c == b'\'' || c == b'"' {
                        break;
                    } else {
                        seg_len += 1;
                        j += 1;
                    }
                }
                if let Some(end) = last_slash {
                    i = end + 1;
                    continue;
                }
            }
        }
        // Copy one UTF-8 scalar.
        let start = i;
        i += 1;
        while i < b.len() && (b[i] & 0xC0) == 0x80 {
            i += 1;
        }
        out.push_str(&s[start..i]);
    }
    out
}

/// studio_jobs.reason — the one-line verdict for a tool's whole output.
pub fn reason(text: &str) -> String {
    let lines: Vec<String> = text
        .lines()
        .map(|l| l.trim_end().to_string())
        .filter(|l| !l.trim().is_empty())
        .collect();
    explain(&lines)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explain_prefers_known_then_error_then_exception() {
        assert_eq!(
            explain(&["ERROR: Video unavailable".to_string()]),
            "That video is unavailable."
        );
        assert_eq!(
            explain(&["ERROR: /a/b/broken.mp4 refused".to_string()]),
            "broken.mp4 refused"
        );
        assert_eq!(
            explain(&[
                "Traceback (most recent call last):".to_string(),
                "subprocess.CalledProcessError: Command '['/opt/bin/ffmpeg', '-i']' returned non-zero exit status 1.".to_string(),
            ]),
            "ffmpeg failed (exit 1)"
        );
        assert_eq!(explain(&[]), "");
    }

    #[test]
    fn basenames_strips_paths_but_not_urls() {
        assert_eq!(basenames("/a/b/x.wav told us"), "x.wav told us");
        assert_eq!(
            basenames("https://example.com/watch/thing"),
            "https://example.com/watch/thing"
        );
        assert_eq!(basenames("word /tmp/у/f.mp3"), "word f.mp3");
    }

    /// Each of these is a failure someone will actually hit — a private
    /// link, one pasted from a members-only stream, a typo'd URL — and each
    /// has to come back as a sentence rather than as
    /// `ERROR: [youtube] abc: Private video. Sign in…`.
    #[test]
    fn every_known_failure_becomes_a_sentence() {
        let cases: [(&str, &str); 9] = [
            (
                "ERROR: [youtube] abc: Private video. Sign in if you've been granted access",
                "That video is private.",
            ),
            (
                "ERROR: [youtube] abc: Video unavailable",
                "That video is unavailable.",
            ),
            (
                "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
                "That video needs a signed-in account.",
            ),
            (
                "ERROR: [youtube] abc: Join this channel: members-only content",
                "That video is members-only.",
            ),
            (
                "ERROR: 'not a link' is not a valid URL",
                "That does not look like a link.",
            ),
            (
                "ERROR: Unsupported URL: https://example.invalid/thing",
                "Nothing here knows how to read that link.",
            ),
            (
                "ERROR: unable to download: HTTP Error 404: Not Found",
                "That link is a dead end (404).",
            ),
            (
                "no audio file was produced",
                "The download produced no audio.",
            ),
            (
                "ERROR: Requested format is not available",
                "No audio-only format was offered for that video.",
            ),
        ];
        for (raw, friendly) in cases {
            let log = ["[youtube] abc".to_string(), raw.to_string()];
            assert_eq!(explain(&log), friendly, "not translated: {raw}");
        }
    }

    /// yt-dlp's wording drifts between releases and its capitalisation
    /// drifts with it; matching on case would quietly stop translating.
    #[test]
    fn the_match_ignores_case() {
        assert_eq!(
            explain(&["error: PRIVATE VIDEO".to_string()]),
            "That video is private."
        );
    }

    /// A run can print several ERRORs and survive the first few. The last
    /// one is the one that killed it, and the `ERROR:` prefix is shell
    /// bookkeeping the operator does not need.
    #[test]
    fn an_unknown_failure_falls_back_to_the_last_error_line() {
        let log = [
            "[youtube] fine".to_string(),
            "ERROR: first thing went wrong".to_string(),
            "still going".to_string(),
            "ERROR: the thing that actually killed it".to_string(),
        ];
        assert_eq!(explain(&log), "the thing that actually killed it");
    }

    /// "ERROR" can appear mid-sentence with no prefix to strip; cutting on
    /// a colon that is not there would leave an empty message.
    #[test]
    fn the_fallback_keeps_the_whole_line_when_there_is_no_prefix() {
        assert_eq!(
            explain(&["something ERROR happened".to_string()]),
            "something ERROR happened"
        );
    }

    /// Progress output is not a failure. A log of nothing but downloads
    /// has no verdict in it, and inventing one would mark a healthy job
    /// broken.
    #[test]
    fn a_log_with_no_error_explains_nothing() {
        assert_eq!(explain(&["[download] 100% of 2MiB".to_string()]), "");
    }

    /// Judge B, grade report 2026-08-31 JB1-10: ffmpeg and demucs failures
    /// reached the operator as "import failed (exit 1)" or as a raw
    /// traceback. The last line of a traceback names the program that
    /// actually failed, which is the only part worth showing.
    #[test]
    fn a_traceback_names_the_program_that_failed() {
        let log = [
            "Traceback (most recent call last):".to_string(),
            "  File \"tools/import_track.py\", line 450, in _import".to_string(),
            "    x = ana.load_audio(out)".to_string(),
            "subprocess.CalledProcessError: Command '['ffmpeg', '-v', 'quiet', \
             '-i', '/private/tmp/x/jb_drop.mp3']' returned non-zero exit status 1."
                .to_string(),
        ];
        assert_eq!(explain(&log), "ffmpeg failed (exit 1)");
    }

    /// import_track's own SystemExit sentences are already written for a
    /// person; they must come through whole instead of being replaced by a
    /// generic exit code.
    #[test]
    fn the_last_meaningful_line_is_the_fallback() {
        let log = [
            "fetching x".to_string(),
            "[download] 100% of 1MiB".to_string(),
            "clip.wav doesn't look like playable audio — ffmpeg could not \
             convert it (exit 1)"
                .to_string(),
        ];
        assert_eq!(explain(&log), log[2]);
    }

    /// /private/tmp/…/_upload/x.wav tells an operator nothing that x.wav
    /// does not, and the prefix is the part that makes the line too long
    /// for the box it lands in.
    #[test]
    fn paths_come_back_as_basenames() {
        assert_eq!(
            explain(&["no such file: /private/tmp/abc/_upload/jb.wav".to_string()]),
            "no such file: jb.wav"
        );
        assert_eq!(
            basenames("see https://youtu.be/abc/def then /a/b/c.wav"),
            "see https://youtu.be/abc/def then c.wav"
        );
    }

    /// A missing tool is the one failure the operator can fix in one
    /// command, so the message says which command.
    #[test]
    fn missing_demucs_and_ffmpeg_are_sentences() {
        assert!(
            explain(&["ModuleNotFoundError: No module named 'demucs'".to_string()])
                .contains("Demucs is not installed")
        );
        let missing_ffmpeg =
            "FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'".to_string();
        assert!(explain(&[missing_ffmpeg]).contains("ffmpeg is not installed"));
    }

    /// The synchronous paths have no job to read a log off — they hand
    /// over the child's whole output in one string, blank lines and all.
    #[test]
    fn reason_takes_the_sync_paths_whole_output() {
        let text = "x\n\nTraceback (most recent call last):\n\
             subprocess.CalledProcessError: Command '['ffmpeg']' \
             returned non-zero exit status 1.\n";
        assert_eq!(reason(text), "ffmpeg failed (exit 1)");
        assert_eq!(reason(""), "");
    }
}
