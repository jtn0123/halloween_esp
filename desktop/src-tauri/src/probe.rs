//! "Is our server already up on this port?" — for each of the two servers
//! the app supervises, one GET whose answer only that server gives:
//!
//! * Castle Radio: `GET /radio/tools` answers `{"service": "castle-radio",
//!   "protocol": 1}` — the question `Open Castle Studio.command` asks.
//! * The cue desk studio (castle-core's `studio` bin): `GET /studio/tracks`
//!   answers `{"tracks": [...], "scenes": [...]}` (docs/API.md).
//!
//! Anything else listening there is someone else's server, which we neither
//! use nor kill. A raw HTTP/1.0 request over std's TcpStream: one loopback
//! GET does not justify an HTTP client in the supervisor. `get_json` is the
//! same GET for a question Castle Radio answers on the app's behalf (the
//! updater's pre-release channel, channel.rs).

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::time::Duration;

/// Which server a port is expected to hold.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Identity {
    CastleRadio,
    Studio,
}

impl Identity {
    fn path(self) -> &'static str {
        match self {
            Identity::CastleRadio => "/radio/tools",
            Identity::Studio => "/studio/tracks",
        }
    }

    fn matches(self, body: &serde_json::Value) -> bool {
        match self {
            Identity::CastleRadio => body["service"] == "castle-radio" && body["protocol"] == 1,
            Identity::Studio => body["tracks"].is_array() && body["scenes"].is_array(),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Probe {
    /// The server we asked for, answering with its identity.
    Ours,
    /// Something answers on the port, and it is not that server.
    Other,
    /// Nothing is listening.
    Nothing,
}

const TIMEOUT: Duration = Duration::from_secs(3);
/// The studio's track list grows with the library; the identity is in the
/// first bytes, but a cut-off JSON body does not parse, so allow plenty.
const LIMIT: u64 = 4 * 1024 * 1024;

pub fn identify(port: u16, who: Identity) -> Probe {
    match get(port, who.path(), TIMEOUT) {
        Ok(raw) => classify(&raw, who),
        Err(probe) => probe,
    }
}

/// A 200's JSON body from our own server on `port`, or None. `wait` is
/// how long the answer may take: one that asks GitHub first is slower than
/// an identity.
pub fn get_json(port: u16, path: &str, wait: Duration) -> Option<serde_json::Value> {
    body(&get(port, path, wait).ok()?)
}

fn get(port: u16, path: &str, wait: Duration) -> Result<Vec<u8>, Probe> {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return Err(Probe::Nothing);
    };
    let _ = stream.set_read_timeout(Some(wait));
    let _ = stream.set_write_timeout(Some(TIMEOUT));
    let request = format!(
        "GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return Err(Probe::Other);
    }
    let mut raw = Vec::new();
    if stream.take(LIMIT).read_to_end(&mut raw).is_err() && raw.is_empty() {
        return Err(Probe::Other);
    }
    Ok(raw)
}

fn body(raw: &[u8]) -> Option<serde_json::Value> {
    let text = String::from_utf8_lossy(raw);
    let (head, body) = text.split_once("\r\n\r\n")?;
    if !head.starts_with("HTTP/1.") || head.split_whitespace().nth(1) != Some("200") {
        return None;
    }
    serde_json::from_str(body).ok()
}

fn classify(raw: &[u8], who: Identity) -> Probe {
    match body(raw) {
        Some(v) if who.matches(&v) => Probe::Ours,
        _ => Probe::Other,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    const RADIO: &[u8] =
        b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{\"service\":\"castle-radio\",\"protocol\":1}";
    const STUDIO: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"tracks\":[],\"scenes\":[\"a\"]}";

    #[test]
    fn classifies_radio_answers() {
        assert_eq!(classify(RADIO, Identity::CastleRadio), Probe::Ours);
        let v2 = b"HTTP/1.0 200 OK\r\n\r\n{\"service\":\"castle-radio\",\"protocol\":2}";
        assert_eq!(classify(v2, Identity::CastleRadio), Probe::Other);
        let html = b"HTTP/1.1 404 Not Found\r\n\r\n<html>";
        assert_eq!(classify(html, Identity::CastleRadio), Probe::Other);
        assert_eq!(classify(b"garbage", Identity::CastleRadio), Probe::Other);
    }

    #[test]
    fn classifies_studio_answers() {
        assert_eq!(classify(STUDIO, Identity::Studio), Probe::Ours);
        // Each server is "someone else" on the other's port.
        assert_eq!(classify(RADIO, Identity::Studio), Probe::Other);
        assert_eq!(classify(STUDIO, Identity::CastleRadio), Probe::Other);
        let half = b"HTTP/1.1 200 OK\r\n\r\n{\"tracks\":[]}";
        assert_eq!(classify(half, Identity::Studio), Probe::Other);
        let error = b"HTTP/1.1 500 Internal\r\n\r\n{\"tracks\":[],\"scenes\":[]}";
        assert_eq!(classify(error, Identity::Studio), Probe::Other);
    }

    #[test]
    fn talks_to_a_real_socket() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            let mut buf = [0u8; 512];
            let n = conn.read(&mut buf).unwrap();
            assert!(String::from_utf8_lossy(&buf[..n]).starts_with("GET /studio/tracks "));
            conn.write_all(STUDIO).unwrap();
        });
        assert_eq!(identify(port, Identity::Studio), Probe::Ours);
        server.join().unwrap();
    }

    #[test]
    fn nothing_listening_is_nothing() {
        // Port 0 can never be connected to, on any OS. The port the test
        // above just freed was the first try here, and it flaked: the next
        // test to bind port 0 is free to be handed that same port, and then
        // someone answers.
        assert_eq!(identify(0, Identity::Studio), Probe::Nothing);
    }
}
