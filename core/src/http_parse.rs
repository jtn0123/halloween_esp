//! Reading a request off the wire — httpd's parsing half.
//!
//! Everything here turns bytes into a `Request`, and nothing here knows a
//! route: the request line and its headers, the query string urllib would
//! have decoded, the one file part a multipart upload carries, and the
//! console-safe spelling of any of it. Split out of `httpd` when that file
//! reached the repo's 500-line cap; `httpd` re-exports the whole surface,
//! so every caller still says `httpd::Request`.

/// studio_http.MAX_BODY — one header must not allocate whatever it claims.
pub const MAX_BODY: u64 = 512 * 1024 * 1024;

use std::io::Read;
use std::net::TcpStream;

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
            // A '%' with fewer than two bytes behind it, or two that are
            // not hex, is a literal '%' — urllib's errors="replace" read.
            b'%' if i + 2 < b.len() => {
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

/// Method, target, and headers folded to lower-case names.
pub(crate) type Head = (String, String, Vec<(String, String)>);

/// The request line and headers, as a route never sees them: what
/// `read_request` does with the bytes before `\r\n\r\n`. Pulled out so the
/// malformed shapes can be asserted without a socket.
pub(crate) fn parse_head(head: &str) -> Result<Head, String> {
    let mut lines = head.split("\r\n");
    let mut first = lines.next().unwrap_or("").split_whitespace();
    let (Some(method), Some(target)) = (first.next(), first.next()) else {
        return Err("malformed request line".to_string());
    };
    let mut headers = Vec::new();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.push((k.trim().to_ascii_lowercase(), v.trim().to_string()));
        }
    }
    Ok((method.to_string(), target.to_string(), headers))
}

/// How many body bytes the head promises — absent is none, unreadable is
/// the caller's 400, and a claim past MAX_BODY is refused before anything
/// is allocated for it.
pub(crate) fn body_length(headers: &[(String, String)]) -> Result<u64, String> {
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
    Ok(length)
}

/// One kept-alive connection: requests are read in sequence, leftover
/// bytes carried between them.
pub struct Conn {
    stream: TcpStream,
    buf: Vec<u8>,
    pub ip: String,
    /// studio_http's `self.close_connection`: this reply has left the
    /// connection unusable and the serve loop must hang up after it.
    pub close: bool,
}

/// How long one socket operation may make no progress before the
/// connection is given up on. A client that opens a socket and never
/// finishes its head pins a thread for as long as it likes otherwise —
/// harmless on loopback, not on `--lan` (grade report 2026-09-01 E3).
/// This is per read, not per request: a 512 MB upload arrives in 64 KB
/// pieces and never waits this long between two of them.
pub const READ_TIMEOUT_S: u64 = 30;

impl Conn {
    pub fn new(stream: TcpStream) -> Conn {
        let ip = stream
            .peer_addr()
            .map(|a| a.ip().to_string())
            .unwrap_or_default();
        let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(READ_TIMEOUT_S)));
        Conn {
            stream,
            buf: Vec::new(),
            ip,
            close: false,
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
        let (method, target, headers) = parse_head(&head)?;
        let length = body_length(&headers)?;
        let need = head_end + 4 + length as usize;
        while self.buf.len() < need {
            if !self.fill()? {
                return Ok(None);
            }
        }
        // Hand the read buffer itself over as the body rather than copying
        // it out: a 512 MB import used to exist twice at once, the grown
        // buffer and the `to_vec` of it. The connection keeps only what
        // came after this request, so its capacity no longer follows the
        // biggest upload around for the rest of the keep-alive either.
        //
        // The `drain` below still memmoves the body down over the ~200
        // bytes of head — one pass, in place, no second allocation. Losing
        // it would mean carrying an offset on every body a route touches,
        // which is a worse trade than the copy (grade report 2026-09-01 B2).
        let leftover = self.buf.split_off(need);
        let mut body = std::mem::replace(&mut self.buf, leftover);
        body.drain(..head_end + 4);
        // Doubling growth leaves ~2× the body in slack. Worth one copy to
        // give back, but only when there is something real to give back.
        if body.capacity() > (8 << 20) && body.capacity() - body.len() > body.capacity() / 4 {
            body.shrink_to_fit();
        }
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

pub(crate) fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    find_from(hay, needle, 0)
}

/// memchr's trick, by hand (the crate takes no dependencies): test the
/// needle's first byte before comparing a whole window. On a 100 MB
/// upload the boundary scan is the request thread's longest single stretch
/// of work, and `windows().position()` pays iterator setup at every one of
/// those hundred million offsets.
pub(crate) fn find_from(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if needle.is_empty() || hay.len() < needle.len() {
        return None;
    }
    let first = needle[0];
    let last = hay.len() - needle.len();
    let mut i = from;
    while i <= last {
        if hay[i] == first && &hay[i..i + needle.len()] == needle {
            return Some(i);
        }
        i += 1;
    }
    None
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

/// studio_http.parse_multipart — the single file part of an upload:
/// (filename, bytes). An empty name or data means "no file in upload";
/// a name that is not a file name ("", ".", "..") is the caller's 400.
pub fn parse_multipart(raw: &[u8], ctype: &str) -> Result<(String, Vec<u8>), String> {
    let Some(bpos) = ctype.find("boundary=") else {
        return Ok((String::new(), Vec::new()));
    };
    let boundary = ctype[bpos + 9..].trim().trim_matches('"');
    let marker = format!("--{boundary}");
    let marker = marker.as_bytes();
    let mut at = 0usize;
    while at <= raw.len() {
        let next = find_from(raw, marker, at);
        let end = next.unwrap_or(raw.len());
        let part = &raw[at..end];
        at = match next {
            Some(p) => p + marker.len(),
            None => break,
        };
        let Some(split) = find(part, b"\r\n\r\n") else {
            continue;
        };
        let head = String::from_utf8_lossy(&part[..split]);
        if !head.contains("filename=") {
            continue;
        }
        let name_part = head.split("filename=").nth(1).unwrap_or("");
        let name = if let Some(q) = name_part.split('"').nth(1) {
            q.to_string()
        } else {
            name_part.trim().to_string()
        };
        let name = name
            .trim_end_matches('/')
            .rsplit('/')
            .next()
            .unwrap_or("")
            .to_string();
        if name.is_empty() || name == "." || name == ".." {
            return Err(format!("upload filename {name:?} is not a file name"));
        }
        let data = &part[split + 4..];
        // Strip exactly the CRLF before the boundary — never real bytes.
        let data = data.strip_suffix(b"\r\n").unwrap_or(data);
        return Ok((name, data.to_vec()));
    }
    Ok((String::new(), Vec::new()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_request_line_wants_two_words() {
        let (m, t, h) = parse_head("GET /studio/tracks HTTP/1.1\r\nHost: x\r\n").unwrap();
        assert_eq!((m.as_str(), t.as_str()), ("GET", "/studio/tracks"));
        assert_eq!(h, vec![("host".to_string(), "x".to_string())]);
        assert!(parse_head("GET\r\n").is_err());
        assert!(parse_head("").is_err());
        assert!(parse_head("\r\nHost: x").is_err());
    }

    #[test]
    fn header_names_fold_and_values_keep_their_colons() {
        let (_, _, h) = parse_head("GET / HTTP/1.1\r\nCoNtEnT-TyPe: text/html\r\nX: a:b\r\njunk")
            .expect("a well-formed request line");
        assert_eq!(h[0], ("content-type".to_string(), "text/html".to_string()));
        assert_eq!(h[1], ("x".to_string(), "a:b".to_string()));
        assert_eq!(h.len(), 2, "a line without a colon is not a header");
    }

    #[test]
    fn content_length_is_absent_a_number_or_a_refusal() {
        let hdr = |v: &str| vec![("content-length".to_string(), v.to_string())];
        assert_eq!(body_length(&[]).unwrap(), 0);
        assert_eq!(body_length(&hdr("12")).unwrap(), 12);
        assert!(body_length(&hdr("-1")).is_err());
        assert!(body_length(&hdr("twelve")).is_err());
        assert!(body_length(&hdr(&(MAX_BODY + 1).to_string())).is_err());
        assert_eq!(body_length(&hdr(&MAX_BODY.to_string())).unwrap(), MAX_BODY);
    }

    #[test]
    fn the_query_decodes_like_parse_qs() {
        assert_eq!(
            query_pairs("/x?a=1&b=two+words&c=%2Fslash%2F"),
            vec![
                ("a".into(), "1".into()),
                ("b".into(), "two words".into()),
                ("c".into(), "/slash/".into()),
            ]
        );
        assert!(query_pairs("/x").is_empty());
        // keep_blank_values=False, and a pair without '=' is not a pair.
        assert!(query_pairs("/x?a=&=1&bare").is_empty());
        // A truncated or non-hex escape survives as a literal '%'.
        assert_eq!(query_pairs("/x?a=%zz"), vec![("a".into(), "%zz".into())]);
        assert_eq!(query_pairs("/x?a=b%2"), vec![("a".into(), "b%2".into())]);
    }

    #[test]
    fn the_needle_search_finds_the_first_and_only_real_hit() {
        assert_eq!(find(b"--X--Y", b"--Y"), Some(3));
        assert_eq!(find(b"aaab", b"ab"), Some(2));
        assert_eq!(find(b"ab", b"abc"), None, "a needle past the end");
        assert_eq!(find(b"abc", b""), None);
        assert_eq!(find_from(b"--X--X", b"--X", 1), Some(3));
        assert_eq!(find_from(b"--X", b"--X", 9), None, "a start past the end");
    }

    #[test]
    fn multipart_answers_the_one_file_part() {
        let raw = b"--B\r\nContent-Disposition: form-data; name=\"f\"; \
                    filename=\"a.mp3\"\r\n\r\nDATA\r\n--B--\r\n";
        let (name, data) = parse_multipart(raw, "multipart/form-data; boundary=B").unwrap();
        assert_eq!(name, "a.mp3");
        assert_eq!(data, b"DATA");
        // Quoted boundaries, and a part with no filename, are both normal.
        let (name, _) = parse_multipart(raw, "multipart/form-data; boundary=\"B\"").unwrap();
        assert_eq!(name, "a.mp3");
        let plain = b"--B\r\nContent-Disposition: form-data; name=\"f\"\r\n\r\nx\r\n--B--\r\n";
        assert_eq!(
            parse_multipart(plain, "multipart/form-data; boundary=B").unwrap(),
            (String::new(), Vec::new())
        );
    }

    #[test]
    fn multipart_refuses_a_name_that_is_not_a_file_name() {
        let with = |fname: &str| {
            format!("--B\r\nContent-Disposition: form-data; filename=\"{fname}\"\r\n\r\nx\r\n--B--")
                .into_bytes()
        };
        for bad in ["", ".", "..", "../.."] {
            assert!(
                parse_multipart(&with(bad), "boundary=B").is_err(),
                "{bad:?} is not a file name"
            );
        }
        // A path is not traversal here, it is a name with leading noise.
        let (name, _) = parse_multipart(&with("/etc/passwd"), "boundary=B").unwrap();
        assert_eq!(name, "passwd");
        // No boundary at all: nothing to find, and not an error.
        assert_eq!(
            parse_multipart(&with("a.mp3"), "application/json").unwrap(),
            (String::new(), Vec::new())
        );
    }

    #[test]
    fn scrub_spells_control_bytes_and_stops_at_300_chars() {
        assert_eq!(scrub("GET /x\r\n\tHTTP"), "GET /x\\r\\n\\tHTTP");
        assert_eq!(scrub("a\u{7}b"), "a\\x07b");
        let long = scrub(&"é".repeat(400));
        assert_eq!(long.chars().count(), 301, "300 chars and the ellipsis");
        assert!(long.ends_with('…'));
    }
}
