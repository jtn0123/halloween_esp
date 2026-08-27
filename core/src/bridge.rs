//! A castle wire client — B4 of the typesafe plan, the bridge's verbs.
//!
//! Talks the firmware's own HTTP (`firmware/sd_web.h`, or castle_emu
//! standing in for it) over a bare TcpStream: the API is a handful of fixed
//! routes on a LAN device, which does not justify an HTTP dependency — and
//! the zero-dep rule keeps the crate WASM-able. Host discovery (devices.toml,
//! fallback lists) stays in tools/hosts.py for now; this takes host:port.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub struct Reply {
    pub code: u16,
    pub body: Vec<u8>,
    /// The castle's Content-Type, defaulted like castle_link's JSON_MIME.
    pub ctype: String,
}

/// Percent-encode one query VALUE or path segment, the firmware's
/// url_decode being the other half. Unreserved bytes pass; everything
/// else is %XX.
pub fn encode_query(v: &str) -> String {
    let mut out = String::new();
    for b in v.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// CRC-32 (IEEE, the zlib/esp_rom_crc32_le polynomial), bitwise — the
/// card-verify sum sd_web.h answers uploads with. No table: an upload's
/// time is the porch WiFi, not this loop.
pub fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFF_u32;
    for &b in data {
        crc ^= u32::from(b);
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xEDB8_8320 & 0u32.wrapping_sub(crc & 1));
        }
    }
    !crc
}

/// castle_link's two failure kinds: Unreachable means the body never
/// left (trying the next host is safe for any verb); Stalled means it
/// MAY have landed.
pub enum CallFault {
    Unreachable(String),
    Stalled(String),
}

impl CallFault {
    pub fn text(&self) -> &str {
        match self {
            CallFault::Unreachable(s) | CallFault::Stalled(s) => s,
        }
    }
}

/// One request, connection-close, body read to EOF. `read_s` is the verb's
/// read budget — status is quick, a card write is not.
pub fn request(
    host: &str,
    method: &str,
    target: &str,
    body: &[u8],
    read_s: f64,
) -> Result<Reply, String> {
    call(host, method, target, body, 2.0, read_s).map_err(|f| f.text().to_string())
}

/// The typed form, with a separate connect budget — castle_link._call.
pub fn call(
    host: &str,
    method: &str,
    target: &str,
    body: &[u8],
    connect_s: f64,
    read_s: f64,
) -> Result<Reply, CallFault> {
    let addr = host
        .to_socket_addrs()
        .map_err(|e| CallFault::Unreachable(format!("cannot resolve {host}: {e}")))?
        .next()
        .ok_or_else(|| CallFault::Unreachable(format!("no address for {host}")))?;
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs_f64(connect_s))
        .map_err(|e| CallFault::Unreachable(format!("cannot reach {host}: {e}")))?;
    s.set_read_timeout(Some(Duration::from_secs_f64(read_s)))
        .ok();
    s.set_nodelay(true).ok();
    // Content-Length only when there is a body — like urllib on the Python
    // side; the httpd treats a missing header as zero.
    let extra = if body.is_empty() {
        String::new()
    } else {
        format!("Content-Length: {}\r\n", body.len())
    };
    let head =
        format!("{method} {target} HTTP/1.1\r\nHost: castle\r\n{extra}Connection: close\r\n\r\n");
    s.write_all(head.as_bytes())
        .and_then(|()| s.write_all(body))
        .map_err(|e| CallFault::Stalled(format!("send to {host} failed: {e}")))?;
    let mut data = Vec::new();
    s.read_to_end(&mut data)
        .map_err(|e| CallFault::Stalled(format!("read from {host} failed: {e}")))?;
    let split = data
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| CallFault::Stalled(format!("malformed reply from {host}")))?;
    let head = String::from_utf8_lossy(&data[..split]);
    let code: u16 = head
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| CallFault::Stalled(format!("bad status line from {host}")))?;
    let ctype = head
        .lines()
        .skip(1)
        .find_map(|l| {
            let (k, v) = l.split_once(':')?;
            k.trim()
                .eq_ignore_ascii_case("content-type")
                .then(|| v.trim().to_string())
        })
        .unwrap_or_else(|| "application/json".to_string());
    Ok(Reply {
        code,
        body: data[split + 4..].to_vec(),
        ctype,
    })
}

/// One number out of the firmware's flat snprintf JSON (`"bytes":3002`).
/// Not a JSON parser: sd_web.h prints a fixed template with no nesting.
/// The one liberty taken is optional spaces after the colon — the
/// emulator answers through json.dumps, which writes `"bytes": 3002`.
pub fn json_uint(body: &str, key: &str) -> Option<u64> {
    let rest = after_key(body, key)?;
    let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
    digits.parse().ok()
}

/// One string field out of the same flat template (`"crc32":"00ab12ff"`).
pub fn json_str(body: &str, key: &str) -> Option<String> {
    let rest = after_key(body, key)?.strip_prefix('"')?;
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

/// The text right after `"key":` and any spaces — where the value starts.
fn after_key<'a>(body: &'a str, key: &str) -> Option<&'a str> {
    let pat = format!("\"{key}\":");
    let at = body.find(&pat)? + pat.len();
    Some(body[at..].trim_start_matches(' '))
}

/// How an upload can go wrong, each with its own exit code at the CLI:
/// transport is exit 1, the castle refusing or the card disagreeing is 2.
pub enum UploadFault {
    Transport(String),
    Refused(u16, String),
    Mismatch(String),
}

/// PUT one file at `route` and hold the castle to its own answer — the
/// byte count always, the CRC32 when the firmware is new enough to send
/// one (v5.42+; "bytes matched" cannot see a bad SD sector). The Ok value
/// is the castle's reply body, for the caller to print.
pub fn upload(host: &str, route: &str, name: &str, data: &[u8]) -> Result<String, UploadFault> {
    let target = format!("{route}/{}", encode_query(name));
    let r = request(host, "PUT", &target, data, 600.0).map_err(UploadFault::Transport)?;
    let body = String::from_utf8_lossy(&r.body).trim_end().to_string();
    if !(200..300).contains(&r.code) {
        return Err(UploadFault::Refused(r.code, body));
    }
    let said = json_uint(&body, "bytes");
    if said != Some(data.len() as u64) {
        let got = said.map_or_else(|| "?".to_string(), |v| v.to_string());
        return Err(UploadFault::Mismatch(format!(
            "{name}: card wrote {got} of {} bytes",
            data.len()
        )));
    }
    if let Some(said) = json_str(&body, "crc32") {
        let want = crc32(data);
        if u32::from_str_radix(&said, 16) != Ok(want) {
            return Err(UploadFault::Mismatch(format!(
                "{name}: crc mismatch — card wrote {said}, sent {want:08x} \
                 (bad sector or corrupt transfer)"
            )));
        }
    }
    Ok(body)
}

/// The (name, is_dir) rows of a /api/files listing — the firmware's own
/// template, `{"name":"…","size":N,"dir":bool}` rows in a bare array. A
/// name safe_name allowed can never contain `"` or `\\`, so the name runs
/// to the next quote; the trailing `{"skipped":N}` row has no name and
/// falls out naturally.
pub fn list_entries(body: &str) -> Vec<(String, bool)> {
    let mut out = Vec::new();
    let mut rest = body;
    while let Some(at) = rest.find("\"name\":") {
        rest = &rest[at + 7..];
        let Some((name, after)) = json_first_str(rest) else {
            break;
        };
        let dir = after_key(after, "dir").is_some_and(|v| v.starts_with("true"));
        out.push((name, dir));
        rest = after;
    }
    out
}

/// The first `"…"` literal at the start of `rest` (spaces allowed), and
/// what follows it.
fn json_first_str(rest: &str) -> Option<(String, &str)> {
    let rest = rest.trim_start_matches(' ').strip_prefix('"')?;
    let end = rest.find('"')?;
    Some((rest[..end].to_string(), &rest[end + 1..]))
}

/// The first candidate that accepts a TCP connection (2 s each), or the
/// first candidate when nobody does — the verb's own error then names it.
/// This is castle_link's fallback walk: devices.toml lists the leases a
/// retired router handed the board, and the living one wins.
pub fn probe(cands: &[String]) -> String {
    for h in cands {
        let hp = if h.contains(':') {
            h.clone()
        } else {
            format!("{h}:80")
        };
        let Some(addr) = hp.to_socket_addrs().ok().and_then(|mut a| a.next()) else {
            continue;
        };
        if TcpStream::connect_timeout(&addr, Duration::from_secs(2)).is_ok() {
            return h.clone();
        }
    }
    cands.first().cloned().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32_matches_zlib() {
        // The check value from the CRC-32 specification, what zlib.crc32
        // and esp_rom_crc32_le both answer.
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(crc32(b""), 0);
    }

    #[test]
    fn listing_rows_parse_and_the_skipped_row_falls_out() {
        let b = r#"[{"name":"a.mp3","size":10,"dir":false},{"name":"site","size":0,"dir":true},{"skipped":2}]"#;
        assert_eq!(
            list_entries(b),
            vec![("a.mp3".to_string(), false), ("site".to_string(), true)]
        );
        assert!(list_entries("[]").is_empty());
    }

    #[test]
    fn firmware_reply_fields_parse() {
        // The firmware's tight snprintf form and the emulator's
        // json.dumps form (a space after each colon) both parse.
        for b in [
            r#"{"path":"/sd/a.mp3","bytes":3002,"crc32":"00ab12ff"}"#,
            r#"{"path": "/sd/a.mp3", "bytes": 3002, "crc32": "00ab12ff"}"#,
        ] {
            assert_eq!(json_uint(b, "bytes"), Some(3002));
            assert_eq!(json_str(b, "crc32").as_deref(), Some("00ab12ff"));
            assert_eq!(json_uint(b, "nope"), None);
            assert_eq!(json_str(b, "path").as_deref(), Some("/sd/a.mp3"));
        }
    }
}
