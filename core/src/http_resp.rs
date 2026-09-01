//! Writing the reply — httpd's response half.
//!
//! A route hands back a `Reply` — JSON, a validated page, a file served by
//! Range, or a castle answer passed through untouched — and `deliver`
//! turns it into bytes with this request's validators and Range header
//! applied. Split out of `httpd` when that file reached the repo's
//! 500-line cap; `httpd` re-exports the whole surface.

use std::io::{Read, Seek, SeekFrom, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::Arc;

use crate::http_parse::{Conn, Request};
use crate::jsonio::{self, Json};

/// sd_web_site.h set_csp(), the same policy on the studio's pages.
pub const CSP: &str = "default-src 'self'; script-src 'self' 'unsafe-inline'; \
    style-src 'self' 'unsafe-inline'; img-src 'self' data:; \
    media-src 'self' data: blob:; connect-src 'self'";

/// What a route means to answer; `deliver` turns it into bytes with the
/// request's validators and Range header applied.
pub enum Reply {
    Json(Json, u16),
    /// send_bytes with an ETag: 304 on a match, no-cache + CSP on HTML.
    /// The body is shared, not owned: the lean page is ~3 MB and the same
    /// bytes for every caller until the file underneath it changes.
    Page {
        body: Arc<Vec<u8>>,
        ctype: &'static str,
        etag: String,
    },
    /// send_range: a file on disk, single Range honoured.
    FileRange {
        path: PathBuf,
        ctype: String,
    },
    /// A relayed castle answer, passed through untouched.
    Raw {
        code: u16,
        body: Vec<u8>,
        ctype: String,
    },
}

pub fn reason(code: u16) -> &'static str {
    match code {
        200 => "OK",
        206 => "Partial Content",
        304 => "Not Modified",
        400 => "Bad Request",
        404 => "Not Found",
        500 => "Internal Server Error",
        502 => "Bad Gateway",
        504 => "Gateway Timeout",
        _ => "",
    }
}

fn head(code: u16, extra: &[(&str, String)]) -> String {
    let mut out = format!("HTTP/1.1 {code} {}\r\n", reason(code));
    for (k, v) in extra {
        out.push_str(k);
        out.push_str(": ");
        out.push_str(v);
        out.push_str("\r\n");
    }
    out.push_str("\r\n");
    out
}

pub fn respond(
    s: &mut TcpStream,
    code: u16,
    ctype: &str,
    extra: &[(&str, String)],
    body: &[u8],
) -> std::io::Result<()> {
    let mut hdrs: Vec<(&str, String)> = vec![
        ("Content-Type", ctype.to_string()),
        ("Content-Length", body.len().to_string()),
    ];
    hdrs.extend(extra.iter().map(|(k, v)| (*k, v.clone())));
    s.write_all(head(code, &hdrs).as_bytes())?;
    s.write_all(body)
}

pub fn respond_json(s: &mut TcpStream, obj: &Json, code: u16) -> std::io::Result<()> {
    respond(
        s,
        code,
        "application/json",
        &[],
        jsonio::dumps(obj).as_bytes(),
    )
}

/// If-None-Match: `*`, or a list of (possibly weak) validators.
pub fn etag_matches(header: Option<&str>, etag: &str) -> bool {
    let Some(header) = header else {
        return false;
    };
    if header.trim() == "*" {
        return true;
    }
    header
        .split(',')
        .any(|c| c.trim().trim_start_matches("W/") == etag)
}

/// studio_http.send_range's arithmetic: (offset, length, partial).
///
/// The Python does this in signed ints, where a zero-byte file makes
/// `total - 1` a harmless -1 that fails every comparison and collapses to
/// an empty 200. In u64 that same subtraction wrapped: the caller promised
/// `Content-Length: 1` and then wrote nothing, desynchronising the
/// keep-alive connection for every later request on it (and panicking
/// outright in a debug build). So the empty file is named here, and every
/// other subtraction is guarded.
pub(crate) fn pick_range(total: u64, rng: &str) -> (u64, u64, bool) {
    if total == 0 {
        return (0, 0, false);
    }
    let last = total - 1;
    let (mut lo, mut hi, mut partial) = (0u64, last, false);
    if let Some(spec) = rng.trim().strip_prefix("bytes=") {
        if !spec.contains(',') {
            let (a, b) = spec.split_once('-').unwrap_or((spec, ""));
            if !a.is_empty() {
                match (a.parse::<u64>(), b.parse::<u64>()) {
                    (Ok(av), Ok(bv)) if !b.is_empty() => (lo, hi, partial) = (av, bv, true),
                    (Ok(av), _) if b.is_empty() => (lo, hi, partial) = (av, last, true),
                    _ => partial = false,
                }
            } else if let Ok(bv) = b.parse::<u64>() {
                (lo, hi, partial) = (total.saturating_sub(bv), last, true);
            }
        }
    }
    hi = hi.min(last);
    if !partial || lo > hi {
        (lo, hi, partial) = (0, last, false);
    }
    (lo, hi + 1 - lo, partial)
}

fn respond_range(
    s: &mut TcpStream,
    path: &std::path::Path,
    ctype: &str,
    range: Option<&str>,
) -> std::io::Result<u16> {
    let total = match std::fs::metadata(path) {
        Ok(m) => m.len(),
        Err(e) => {
            let body = Json::Obj(vec![
                ("ok".into(), Json::Bool(false)),
                ("error".into(), Json::Str(format!("OSError: {e}"))),
            ]);
            respond_json(s, &body, 500)?;
            return Ok(500);
        }
    };
    let (lo, length, partial) = pick_range(total, range.unwrap_or(""));
    let code = if partial { 206 } else { 200 };
    let mut hdrs: Vec<(&str, String)> = vec![
        ("Content-Type", ctype.to_string()),
        ("Accept-Ranges", "bytes".to_string()),
        ("Content-Length", length.to_string()),
    ];
    if partial {
        let hi = lo + length - 1;
        hdrs.push(("Content-Range", format!("bytes {lo}-{hi}/{total}")));
    }
    hdrs.push(("Cache-Control", "no-store".to_string()));
    s.write_all(head(code, &hdrs).as_bytes())?;
    // 64 KB slices, like the Python: never the whole file in RAM.
    let mut fh = std::fs::File::open(path)?;
    fh.seek(SeekFrom::Start(lo))?;
    let mut left = length;
    let mut chunk = vec![0u8; 65536];
    while left > 0 {
        let want = chunk.len().min(left as usize);
        let n = fh.read(&mut chunk[..want])?;
        if n == 0 {
            break; // file shrank mid-serve
        }
        s.write_all(&chunk[..n])?;
        left -= n as u64;
    }
    Ok(code)
}

/// Write one reply; returns the status code actually sent (for the log).
pub fn deliver(conn: &mut Conn, req: &Request, reply: &Reply) -> std::io::Result<u16> {
    let inm = req.header("if-none-match").map(str::to_string);
    let range = req.header("range").map(str::to_string);
    let s = conn.stream();
    match reply {
        Reply::Json(obj, code) => {
            respond_json(s, obj, *code)?;
            Ok(*code)
        }
        Reply::Page { body, ctype, etag } => {
            if etag_matches(inm.as_deref(), etag) {
                let hdrs = [
                    ("ETag", etag.clone()),
                    ("Cache-Control", "no-cache".to_string()),
                ];
                s.write_all(head(304, &hdrs).as_bytes())?;
                return Ok(304);
            }
            let mut hdrs: Vec<(&str, String)> = vec![
                ("ETag", etag.clone()),
                ("Cache-Control", "no-cache".to_string()),
                ("Vary", "Accept-Encoding".to_string()),
            ];
            if ctype.starts_with("text/html") {
                hdrs.push(("Content-Security-Policy", CSP.to_string()));
            }
            respond(s, 200, ctype, &hdrs, body.as_slice())?;
            Ok(200)
        }
        Reply::FileRange { path, ctype } => respond_range(s, path, ctype, range.as_deref()),
        Reply::Raw { code, body, ctype } => {
            respond(s, *code, ctype, &[], body)?;
            Ok(*code)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_zero_byte_file_has_no_range_to_serve() {
        // The bug this test exists for: `total - 1` wrapping to u64::MAX,
        // promising a byte that is not there. Every spelling of a Range
        // over an empty file is a plain, empty 200 — the Python's answer.
        for rng in [
            "",
            "bytes=0-",
            "bytes=0-0",
            "bytes=-50",
            "bytes=100-199",
            "bytes=zz",
        ] {
            assert_eq!(
                pick_range(0, rng),
                (0, 0, false),
                "{rng:?} on an empty file"
            );
        }
    }

    #[test]
    fn a_range_less_request_is_the_whole_file() {
        assert_eq!(pick_range(3000, ""), (0, 3000, false));
        assert_eq!(pick_range(1, ""), (0, 1, false));
        assert_eq!(pick_range(3000, "  \t "), (0, 3000, false));
    }

    #[test]
    fn the_three_range_forms_a_media_element_asks_for() {
        assert_eq!(pick_range(3000, "bytes=100-199"), (100, 100, true));
        assert_eq!(pick_range(3000, "bytes=2900-"), (2900, 100, true));
        assert_eq!(pick_range(3000, "bytes=-50"), (2950, 50, true));
        // A suffix longer than the file is the whole file, still a 206.
        assert_eq!(pick_range(3000, "bytes=-9000"), (0, 3000, true));
        // A tail that runs off the end is clamped, not refused.
        assert_eq!(pick_range(3000, "bytes=2990-9999"), (2990, 10, true));
    }

    #[test]
    fn nonsense_ranges_fall_back_to_the_whole_file() {
        for rng in [
            "bytes=zz",         // not a number
            "bytes=5-2",        // backwards
            "bytes=9999-",      // wholly past the end
            "bytes=0-10,20-30", // multi-range, which we never serve
            "items=0-10",       // not a byte range
            "bytes=-abc",       // an unreadable suffix length
            "0-10",             // no unit at all
            // Neither side of the dash names no range at all. The Python
            // twin set its `partial` flag here regardless and answered a
            // 206 over the whole file; it now agrees with this line.
            "bytes=",
            "bytes=-",
        ] {
            assert_eq!(pick_range(3000, rng), (0, 3000, false), "{rng:?}");
        }
    }

    #[test]
    fn if_none_match_takes_a_star_a_list_and_weak_validators() {
        assert!(etag_matches(Some("*"), "\"abc\""));
        assert!(etag_matches(Some("\"abc\""), "\"abc\""));
        assert!(etag_matches(Some("\"x\", W/\"abc\""), "\"abc\""));
        assert!(!etag_matches(Some("\"x\""), "\"abc\""));
        assert!(!etag_matches(None, "\"abc\""));
        assert!(!etag_matches(Some(""), "\"abc\""));
    }

    #[test]
    fn the_status_line_and_headers_are_crlf_framed() {
        let h = head(206, &[("Content-Length", "10".to_string())]);
        assert_eq!(
            h,
            "HTTP/1.1 206 Partial Content\r\nContent-Length: 10\r\n\r\n"
        );
        assert!(head(200, &[]).ends_with("\r\n\r\n"));
        // An unlisted code still frames — a reason phrase is optional.
        assert!(head(418, &[]).starts_with("HTTP/1.1 418 \r\n"));
        assert_eq!(reason(304), "Not Modified");
        assert_eq!(reason(999), "");
    }
}
