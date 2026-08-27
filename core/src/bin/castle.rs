//! The bridge CLI — the desk's transport verbs, spoken from a terminal.
//!
//!     castle --host 10.27.27.7:80 status
//!     castle --host … scene seance | play 10_ballad.mp3 | stop | volume 60
//!     castle --host … show start|stop · blackout · files [subdir] · bootlog
//!     castle --host … put local.mp3 [name] · rm name
//!
//! Host comes from --host or CASTLE_HOST (host[:port], first entry of a
//! comma list). Prints the castle's own JSON answer; exit 0 on 2xx, 2 when
//! the castle refuses or the card's answer disagrees, 1 for transport.
//! tests/test_bridge_rust.py round-trips every verb against castle_emu.

use castle_core::bridge::{encode_query, request, upload, UploadFault};

fn fail(msg: &str) -> ! {
    eprintln!("castle: {msg}");
    std::process::exit(1)
}

fn refuse(msg: &str) -> ! {
    eprintln!("castle: {msg}");
    std::process::exit(2)
}

/// `put LOCAL [NAME]`: one file onto the card, held to the byte count and
/// (v5.42+) the CRC32 the castle answers with — sd_sync.upload's checks.
fn do_put(host: &str, args: &[String]) -> ! {
    let Some(path) = args.first() else {
        fail("put needs a local file")
    };
    let data = std::fs::read(path).unwrap_or_else(|e| fail(&format!("cannot read {path}: {e}")));
    let name = args.get(1).cloned().unwrap_or_else(|| {
        std::path::Path::new(path)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    });
    match upload(host, "/api/files", &name, &data) {
        Ok(body) => {
            println!("{body}");
            std::process::exit(0)
        }
        Err(UploadFault::Transport(e)) => fail(&e),
        Err(UploadFault::Refused(code, body)) => {
            refuse(&format!("{host} refused {name}: {code} {body}"))
        }
        Err(UploadFault::Mismatch(why)) => refuse(&why),
    }
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut host = std::env::var("CASTLE_HOST")
        .ok()
        .and_then(|h| h.split(',').next().map(str::to_string))
        .unwrap_or_default();
    if args.first().map(String::as_str) == Some("--host") {
        args.remove(0);
        host = if args.is_empty() {
            fail("--host needs a value")
        } else {
            args.remove(0)
        };
    }
    if host.is_empty() {
        fail("no castle named: pass --host or set CASTLE_HOST");
    }
    if !host.contains(':') {
        host.push_str(":80");
    }
    let verb = args.first().cloned().unwrap_or_default();
    if verb == "put" {
        do_put(&host, &args[1..]);
    }
    let arg = args.get(1);
    let (method, target, read_s) = match (verb.as_str(), arg) {
        ("status", None) => ("GET", "/api/status".to_string(), 5.0),
        ("health", None) => ("GET", "/api/health".to_string(), 5.0),
        ("stop", None) => ("POST", "/api/stop".to_string(), 5.0),
        ("scene", Some(id)) => ("POST", format!("/api/scene?s={}", encode_query(id)), 5.0),
        ("play", Some(f)) => ("POST", format!("/api/play?f={}", encode_query(f)), 5.0),
        ("volume", Some(v)) => ("POST", format!("/api/volume?v={}", encode_query(v)), 5.0),
        ("show", Some(w)) if w == "start" || w == "stop" => ("POST", format!("/api/show/{w}"), 5.0),
        ("blackout", None) => ("POST", "/api/blackout".to_string(), 5.0),
        ("bootlog", None) => ("GET", "/api/bootlog".to_string(), 5.0),
        ("files", None) => ("GET", "/api/files".to_string(), 5.0),
        ("files", Some(d)) => ("GET", format!("/api/files?d={}", encode_query(d)), 5.0),
        ("rm", Some(n)) => ("DELETE", format!("/api/files/{}", encode_query(n)), 10.0),
        _ => fail(
            "usage: castle [--host H:P] status|health|stop|scene ID|play FILE|\
             volume N|show start|show stop|blackout|files [DIR]|bootlog|\
             put LOCAL [NAME]|rm NAME",
        ),
    };
    match request(&host, method, &target, b"", read_s) {
        Err(e) => fail(&e),
        Ok(r) => {
            println!("{}", String::from_utf8_lossy(&r.body).trim_end());
            if !(200..300).contains(&r.code) {
                std::process::exit(2);
            }
        }
    }
}
