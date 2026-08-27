//! A castle wire client — B4 of the typesafe plan, the bridge's first verbs.
//!
//! Talks the firmware's own HTTP (`firmware/sd_web.h`, or castle_emu
//! standing in for it) over a bare TcpStream: the API is five fixed routes
//! on a LAN device, which does not justify an HTTP dependency — and the
//! zero-dep rule keeps the crate WASM-able. Host discovery (devices.toml,
//! fallback lists) stays in tools/hosts.py for now; this takes host:port.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub struct Reply {
    pub code: u16,
    pub body: Vec<u8>,
}

/// Percent-encode one query VALUE, the firmware's url_decode being the
/// other half. Unreserved bytes pass; everything else is %XX.
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

/// One request, connection-close, body read to EOF. `read_s` is the verb's
/// read budget — status is quick, a card write is not.
pub fn request(host: &str, method: &str, target: &str, read_s: f64) -> Result<Reply, String> {
    let addr = host
        .to_socket_addrs()
        .map_err(|e| format!("cannot resolve {host}: {e}"))?
        .next()
        .ok_or_else(|| format!("no address for {host}"))?;
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs_f64(2.0))
        .map_err(|e| format!("cannot reach {host}: {e}"))?;
    s.set_read_timeout(Some(Duration::from_secs_f64(read_s)))
        .ok();
    s.set_nodelay(true).ok();
    let head = format!("{method} {target} HTTP/1.1\r\nHost: castle\r\nConnection: close\r\n\r\n");
    s.write_all(head.as_bytes())
        .map_err(|e| format!("send to {host} failed: {e}"))?;
    let mut data = Vec::new();
    s.read_to_end(&mut data)
        .map_err(|e| format!("read from {host} failed: {e}"))?;
    let split = data
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| format!("malformed reply from {host}"))?;
    let head = String::from_utf8_lossy(&data[..split]);
    let code: u16 = head
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| format!("bad status line from {host}"))?;
    Ok(Reply {
        code,
        body: data[split + 4..].to_vec(),
    })
}
