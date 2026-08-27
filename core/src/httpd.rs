//! The studio server's transport half — studio_http.py, on a bare socket.
//!
//! Same seam as the Python split: none of this knows a route, a track or a
//! scene. It reads requests off a TcpStream (keep-alive, bounded bodies),
//! and writes the reply shapes the routes hand back — JSON, a validated
//! page, a Range-served file, or a relayed castle answer. Differences from
//! the Python on purpose: no gzip (content negotiation is optional, and a
//! DEFLATE implementation buys nothing on a loopback link) and no
//! content-hash ETag fallback (every HTML route supplies its own).

use std::io::{Read, Seek, SeekFrom, Write};
use std::net::TcpStream;
use std::path::PathBuf;

use crate::jsonio::{self, Json};

/// studio_http.MAX_BODY — one header must not allocate whatever it claims.
pub const MAX_BODY: u64 = 512 * 1024 * 1024;

/// sd_web_site.h set_csp(), the same policy on the studio's pages.
pub const CSP: &str = "default-src 'self'; script-src 'self' 'unsafe-inline'; \
    style-src 'self' 'unsafe-inline'; img-src 'self' data:; \
    media-src 'self' data: blob:; connect-src 'self'";

pub struct Request {
    pub method: String,
    pub target: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
    pub client_ip: String,
}

impl Request {
    pub fn header(&self, key: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }
    /// The target before any query string.
    pub fn path(&self) -> &str {
        self.target.split('?').next().unwrap_or("")
    }
    /// parse_qs's answer: decoded pairs, blanks dropped.
    pub fn query(&self) -> Vec<(String, String)> {
        query_pairs(&self.target)
    }
    pub fn query_get(&self, key: &str) -> Option<String> {
        self.query()
            .into_iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v)
    }
}

/// urllib.parse.parse_qs over the target's query — %XX and '+' decoded,
/// pairs with an empty key or value dropped (keep_blank_values=False).
pub fn query_pairs(target: &str) -> Vec<(String, String)> {
    let Some((_, q)) = target.split_once('?') else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for kv in q.split('&') {
        let Some((k, v)) = kv.split_once('=') else {
            continue;
        };
        let (k, v) = (unquote_plus(k), unquote_plus(v));
        if !k.is_empty() && !v.is_empty() {
            out.push((k, v));
        }
    }
    out
}

fn unquote_plus(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < b.len() + 1 && i + 2 < b.len() + 1 => {
                let hex = b.get(i + 1..i + 3).and_then(|h| {
                    std::str::from_utf8(h)
                        .ok()
                        .and_then(|h| u8::from_str_radix(h, 16).ok())
                });
                match hex {
                    Some(v) => {
                        out.push(v);
                        i += 2;
                    }
                    None => out.push(b'%'),
                }
            }
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// One kept-alive connection: requests are read in sequence, leftover
/// bytes carried between them.
pub struct Conn {
    stream: TcpStream,
    buf: Vec<u8>,
    pub ip: String,
}

impl Conn {
    pub fn new(stream: TcpStream) -> Conn {
        let ip = stream
            .peer_addr()
            .map(|a| a.ip().to_string())
            .unwrap_or_default();
        Conn {
            stream,
            buf: Vec::new(),
            ip,
        }
    }

    pub fn stream(&mut self) -> &mut TcpStream {
        &mut self.stream
    }

    /// The next request, `Ok(None)` when the client hung up cleanly, or a
    /// client mistake to answer with a 400 and a closed connection.
    pub fn read_request(&mut self) -> Result<Option<Request>, String> {
        let head_end = loop {
            if let Some(at) = find(&self.buf, b"\r\n\r\n") {
                break at;
            }
            if self.buf.len() > 1 << 20 {
                return Err("request head too large".to_string());
            }
            if !self.fill()? {
                return Ok(None);
            }
        };
        let head = String::from_utf8_lossy(&self.buf[..head_end]).into_owned();
        let mut lines = head.split("\r\n");
        let mut first = lines.next().unwrap_or("").split_whitespace();
        let (Some(method), Some(target)) = (first.next(), first.next()) else {
            return Err("malformed request line".to_string());
        };
        let (method, target) = (method.to_string(), target.to_string());
        let mut headers = Vec::new();
        for line in lines {
            if let Some((k, v)) = line.split_once(':') {
                headers.push((k.trim().to_ascii_lowercase(), v.trim().to_string()));
            }
        }
        let length: u64 = match headers.iter().find(|(k, _)| k == "content-length") {
            None => 0,
            Some((_, v)) => v
                .parse()
                .map_err(|_| "Content-Length is not a number".to_string())?,
        };
        if length > MAX_BODY {
            return Err(format!(
                "request body too large ({length} bytes; the limit is {MAX_BODY})"
            ));
        }
        let need = head_end + 4 + length as usize;
        while self.buf.len() < need {
            if !self.fill()? {
                return Ok(None);
            }
        }
        let body = self.buf[head_end + 4..need].to_vec();
        self.buf.drain(..need);
        Ok(Some(Request {
            method,
            target,
            headers,
            body,
            client_ip: self.ip.clone(),
        }))
    }

    fn fill(&mut self) -> Result<bool, String> {
        let mut tmp = [0u8; 65536];
        match self.stream.read(&mut tmp) {
            Ok(0) => Ok(false),
            Ok(n) => {
                self.buf.extend_from_slice(&tmp[..n]);
                Ok(true)
            }
            Err(_) => Ok(false),
        }
    }
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// What a route means to answer; `deliver` turns it into bytes with the
/// request's validators and Range header applied.
pub enum Reply {
    Json(Json, u16),
    /// send_bytes with an ETag: 304 on a match, no-cache + CSP on HTML.
    Page {
        body: Vec<u8>,
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
    let rng = range.unwrap_or("").trim();
    let (mut lo, mut hi, mut partial) = (0u64, total.saturating_sub(1), false);
    if let Some(spec) = rng.strip_prefix("bytes=") {
        if !spec.contains(',') {
            let (a, b) = spec.split_once('-').unwrap_or((spec, ""));
            if !a.is_empty() {
                match (a.parse::<u64>(), b.parse::<u64>()) {
                    (Ok(av), Ok(bv)) if !b.is_empty() => (lo, hi, partial) = (av, bv, true),
                    (Ok(av), _) if b.is_empty() => (lo, hi, partial) = (av, total - 1, true),
                    _ => partial = false,
                }
            } else if let Ok(bv) = b.parse::<u64>() {
                (lo, hi, partial) = (total.saturating_sub(bv), total - 1, true);
            }
        }
    }
    hi = hi.min(total.saturating_sub(1));
    if !partial || lo > hi {
        (lo, hi, partial) = (0, total.saturating_sub(1), false);
    }
    let length = hi + 1 - lo;
    let code = if partial { 206 } else { 200 };
    let mut hdrs: Vec<(&str, String)> = vec![
        ("Content-Type", ctype.to_string()),
        ("Accept-Ranges", "bytes".to_string()),
        ("Content-Length", length.to_string()),
    ];
    if partial {
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
            respond(s, 200, ctype, &hdrs, body)?;
            Ok(200)
        }
        Reply::FileRange { path, ctype } => respond_range(s, path, ctype, range.as_deref()),
        Reply::Raw { code, body, ctype } => {
            respond(s, *code, ctype, &[], body)?;
            Ok(*code)
        }
    }
}

/// studio_http.scrub — a request-tainted line made safe for the console.
pub fn scrub(line: &str) -> String {
    let mut out = String::new();
    for c in line.chars() {
        if c == '\n' {
            out.push_str("\\n");
        } else if c == '\r' {
            out.push_str("\\r");
        } else if c == '\t' {
            out.push_str("\\t");
        } else if c.is_control() {
            let mut buf = [0u8; 4];
            for b in c.encode_utf8(&mut buf).bytes() {
                out.push_str(&format!("\\x{b:02x}"));
            }
        } else {
            out.push(c);
        }
    }
    if out.chars().count() > 300 {
        out = out.chars().take(300).collect::<String>() + "…";
    }
    out
}
