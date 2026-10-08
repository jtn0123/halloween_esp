//! The one-line verdicts — turn a tool's whole output into the single
//! sentence worth showing the castle's owner: what happened, and what to
//! do next. Raw shell output in a UI is a failure of nerve; the interesting
//! line is almost always in there, it just needs finding. The words
//! themselves live in `studio_reason_words`; `tools/import_reason.py` is
//! the same scan in Python, held to this one by one shared corpus
//! (`tests/import_reasons.json`, read by both test suites).

use crate::studio_reason_words::{DOWNLOAD_FAILED, DOWNLOADER_OLD, GENERIC, KNOWN, TOOL_FAILED};

const EXC_TAIL: [&str; 4] = ["Error", "Exception", "Exit", "Interrupt"];

/// A progress or info line (`[download] Destination: …`, `[youtube] …`):
/// it names the song, never the failure, so a title that happens to say
/// "Private Video" or "Timed Out" cannot pass for one.
fn is_chatter(line: &str) -> bool {
    line.starts_with('[')
}

/// One sentence worth showing a person, or "" when the log holds none.
///
/// Lines that start with whitespace are never read: they quote a tool
/// (the importer's detail, a line relayed from yt-dlp, a traceback's
/// frames) for whoever helps. Of the rest, in order: a last line that is
/// already the owner's sentence — the importer's own verdict, "what
/// happened — what to do" — as it stands; else a KNOWN phrase anywhere
/// outside the chatter; else the last `ERROR:` line, which is yt-dlp's —
/// a `[site]`-tagged one is an extractor the site has outgrown, any other
/// a download that did not finish; else a Python traceback's last line, a
/// program that failed or a crash; else the last meaningful line.
pub fn explain(log: &[String]) -> String {
    scan(log, true)
}

/// A line quoting a tool, kept for the log and never read for a verdict.
fn is_quoted(line: &str) -> bool {
    line.as_bytes().first().is_none_or(u8::is_ascii_whitespace)
}

/// Already the owner's sentence, rather than an exception's message that
/// happens to hold a dash.
fn is_verdict(line: &str) -> bool {
    line.contains(" — ") && !exc_match(line).is_some_and(|(n, _)| is_exception(&n))
}

fn is_exception(name: &str) -> bool {
    EXC_TAIL.iter().any(|t| name.ends_with(t))
}

fn scan(log: &[String], passthrough: bool) -> String {
    let said: Vec<&str> = log
        .iter()
        .map(String::as_str)
        .filter(|l| !is_quoted(l))
        .collect();
    let heard: Vec<&str> = said.iter().copied().filter(|l| !is_chatter(l)).collect();
    let last = heard
        .iter()
        .rev()
        .find(|l| !l.starts_with("Traceback"))
        .map_or("", |l| l.trim_ascii());
    if passthrough && is_verdict(last) {
        return basenames(last);
    }
    known(&heard)
        .or_else(|| download_error(&heard))
        .or_else(|| crash(&said))
        .unwrap_or_else(|| {
            if passthrough {
                basenames(last)
            } else {
                String::new()
            }
        })
}

/// The sentence for a KNOWN phrase anywhere in what was said.
fn known(heard: &[&str]) -> Option<String> {
    let text = heard.join("\n").to_lowercase();
    KNOWN
        .iter()
        .find(|(needle, _)| text.contains(&needle.to_lowercase()))
        .map(|(_, friendly)| (*friendly).to_string())
}

/// yt-dlp's last `ERROR:` line: a `[site]`-tagged one is an extractor the
/// site has outgrown, any other a download that did not finish.
fn download_error(heard: &[&str]) -> Option<String> {
    let (_, tail) = heard.iter().rev().find_map(|l| l.split_once("ERROR:"))?;
    let said = if tail.trim_ascii().starts_with('[') {
        DOWNLOADER_OLD
    } else {
        DOWNLOAD_FAILED
    };
    Some(said.to_string())
}

/// A Python traceback's last line: a program that failed, or a crash.
fn crash(said: &[&str]) -> Option<String> {
    said.iter().rev().find_map(|line| match exc_match(line) {
        Some((name, rest)) if is_exception(&name) => Some(exception_line(rest.as_deref())),
        _ => None,
    })
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
    Some((name, Some(line[i + 1..].trim_ascii_start().to_string())))
}

/// "Command '['ffmpeg', …]' returned non-zero exit status 1." names the
/// program that failed; any other exception is a crash, and its message
/// is for whoever reads the log, not for the owner.
fn exception_line(rest: Option<&str>) -> String {
    let rest = rest.unwrap_or("").trim_ascii();
    if let Some(at) = rest.find("Command '['") {
        let after = &rest[at + 11..];
        if let Some(end) = after.find('\'') {
            // A Windows repr doubles its backslashes ('C:\\ff\\ffmpeg.exe');
            // splitting on each one leaves the same last segment.
            let prog = strip_exe(after[..end].rsplit(['/', '\\']).next().unwrap_or(""));
            if !prog.is_empty() {
                return TOOL_FAILED.replace("{prog}", prog);
            }
        }
    }
    GENERIC.to_string()
}

/// `ffmpeg.exe` reads as `ffmpeg` — the desk names the tool, not the file.
fn strip_exe(prog: &str) -> &str {
    let n = prog.len();
    if n > 4 && prog.is_char_boundary(n - 4) && prog[n - 4..].eq_ignore_ascii_case(".exe") {
        &prog[..n - 4]
    } else {
        prog
    }
}

fn is_sep(c: u8) -> bool {
    c == b'/' || c == b'\\'
}

/// '/a/b/x.wav' → 'x.wav'; URLs untouched
/// (a '/' preceded by ':', '/' or a word character never starts a match).
/// Windows paths too: '\' separates like '/', and a drive-letter path
/// ('C:\Users\…\x.wav', 'C:/…/x.wav') loses its prefix, drive and all.
pub fn basenames(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = String::new();
    let mut i = 0;
    while i < b.len() {
        if is_sep(b[i])
            && starts_a_path(b, i)
            && let Some(end) = path_prefix_end(b, i)
        {
            i = end + 1;
            continue;
        }
        if let Some(end) = drive_prefix_end(b, i) {
            i = end + 1;
            continue;
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

/// Whether the '/' at `i` can open a path: the byte before it must not be
/// one the regex's lookbehind excludes, which is what keeps URLs whole.
fn starts_a_path(b: &[u8], i: usize) -> bool {
    if i == 0 {
        return true;
    }
    let p = b[i - 1];
    !(p == b':' || is_sep(p) || p.is_ascii_alphanumeric() || p == b'_')
}

/// A Windows drive path starting at `i` — one letter, ':', a separator,
/// with nothing word-like before the letter (so 'https:' is never a
/// drive) — and the end of what to strip: its last separator, or just
/// the 'C:\' of a file in the drive's root.
fn drive_prefix_end(b: &[u8], i: usize) -> Option<usize> {
    let letter = b[i].is_ascii_alphabetic()
        && b.get(i + 1) == Some(&b':')
        && b.get(i + 2).is_some_and(|&c| is_sep(c));
    if !letter || (i > 0 && (b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_')) {
        return None;
    }
    Some(path_prefix_end(b, i + 2).unwrap_or(i + 2))
}

/// The last '/' of the `[^/\s'"]+/` run starting at `i`, consumed
/// possessively like the Python regex — None when there is no run, so the
/// '/' is just a character. '\' counts as '/', and a doubled one (a
/// Python repr of a Windows path) as one separator.
fn path_prefix_end(b: &[u8], i: usize) -> Option<usize> {
    let mut j = i + 1;
    let mut last_slash = None;
    let mut seg_len = 0;
    while j < b.len() {
        let c = b[j];
        if is_sep(c) {
            if seg_len == 0 {
                if c == b'\\' && b[j - 1] == b'\\' {
                    last_slash = Some(j);
                    j += 1;
                    continue;
                }
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
    last_slash
}

fn lines_of(text: &str) -> Vec<String> {
    text.lines()
        .map(|l| l.trim_ascii_end().to_string())
        .filter(|l| !l.trim_ascii().is_empty())
        .collect()
}

/// The verdict for a tool's whole output, blank lines and all.
pub fn reason(text: &str) -> String {
    explain(&lines_of(text))
}

/// Like reason(), but only a sentence from the vocabulary — never a line
/// passed through. For a caller with a better sentence of its own when the
/// tool said nothing this module recognises.
pub fn recognised(text: &str) -> String {
    scan(&lines_of(text), false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jsonio::Json;
    use crate::studio_reason_words::named;

    /// The corpus both copies read — this crate and `tools/import_reason.py`.
    const CORPUS: &str = include_str!("../../tests/import_reasons.json");

    fn corpus() -> Json {
        crate::jsonio_parse::parse(CORPUS).expect("tests/import_reasons.json parses")
    }

    fn arr<'a>(v: &'a Json, key: &str) -> &'a [Json] {
        match v.get(key) {
            Some(Json::Arr(a)) => a,
            _ => panic!("corpus has no {key} list"),
        }
    }

    fn strs(v: &Json) -> Vec<String> {
        match v {
            Json::Arr(a) => a.iter().map(|s| s.as_str().unwrap().to_string()).collect(),
            _ => panic!("not a list of strings"),
        }
    }

    /// What a case expects: a sentence by name, a program for TOOL_FAILED,
    /// or the literal line.
    fn expected(case: &Json) -> String {
        if let Some(name) = case.get("want").and_then(Json::as_str) {
            return named(name)
                .unwrap_or_else(|| panic!("no sentence {name}"))
                .to_string();
        }
        if let Some(prog) = case.get("tool").and_then(Json::as_str) {
            return TOOL_FAILED.replace("{prog}", prog);
        }
        case.get("say")
            .and_then(Json::as_str)
            .expect("want, tool or say")
            .to_string()
    }

    /// Every failure an owner will actually hit — a private link, a full
    /// disk, a site the downloader has fallen behind — comes back as the
    /// sentence the Python copy also says, word for word.
    #[test]
    fn the_shared_corpus_explains_the_same_here() {
        let c = corpus();
        for case in arr(&c, "explain") {
            let log = strs(case.get("log").unwrap());
            assert_eq!(explain(&log), expected(case), "log: {log:?}");
        }
    }

    /// The synchronous paths hand over a child's whole output, blank lines,
    /// CRLFs and all.
    #[test]
    fn the_shared_corpus_reasons_the_same_here() {
        let c = corpus();
        for case in arr(&c, "reason") {
            let text = case.get("text").and_then(Json::as_str).unwrap();
            assert_eq!(reason(text), expected(case), "text: {text:?}");
        }
    }

    /// Only the vocabulary, never a passed-through line.
    #[test]
    fn the_shared_corpus_recognises_the_same_here() {
        let c = corpus();
        for case in arr(&c, "recognised") {
            let text = case.get("text").and_then(Json::as_str).unwrap();
            assert_eq!(recognised(text), expected(case), "text: {text:?}");
        }
    }

    /// Paths lose their folders — POSIX, drive letters, UNC shares, a
    /// Python repr's doubled backslashes, names in any script — and links
    /// keep theirs.
    #[test]
    fn the_shared_corpus_strips_paths_the_same_here() {
        let c = corpus();
        for pair in arr(&c, "basenames") {
            let p = strs(pair);
            assert_eq!(basenames(&p[0]), p[1], "in: {:?}", p[0]);
        }
    }

    /// Every row of the table is exercised by a case: a row nobody tests is
    /// a row a typo can quietly disable.
    #[test]
    fn every_row_of_the_table_has_a_case() {
        let lower = CORPUS.to_lowercase();
        for (needle, _) in KNOWN {
            let escaped = needle.replace('"', "\\\"").to_lowercase();
            assert!(lower.contains(&escaped), "no case for {needle:?}");
        }
    }
}
