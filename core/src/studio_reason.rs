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
}
