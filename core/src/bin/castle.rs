//! The bridge CLI — the desk's transport verbs, spoken from a terminal.
//!
//!     castle --host 10.27.27.7:80 status
//!     castle --host … scene seance | play 10_ballad.mp3 | stop | volume 60
//!     castle --host … show start|stop · blackout · files [subdir] · bootlog
//!     castle --host … put local.mp3 [name] · rm name
//!
//! Host resolution is tools/hosts.py's, ported: --host (an address or a
//! devices.toml name), then CASTLE_HOST (a comma list, names looked up),
//! then the devices.toml inventory (CASTLE_DEVICES overrides the path);
//! several candidates are probed and the first that answers wins. The
//! `hosts` verb prints the walk. Answers print as the castle's own JSON;
//! exit 0 on 2xx, 2 when
//! the castle refuses or the card's answer disagrees, 1 for transport.
//! tests/test_bridge_rust.py round-trips every verb against castle_emu.

use castle_core::bridge::{UploadFault, encode_query, list_entries, probe, request, upload};
use castle_core::hosts;

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
    let mut args = args;
    let mut route = "/api/files";
    if args.first().map(String::as_str) == Some("--to") {
        route = match args.get(1).map(String::as_str) {
            Some("site") => "/api/site",
            Some("scenes") => "/api/scenes",
            _ => fail("--to takes site or scenes (plain put goes to the card root)"),
        };
        args = &args[2..];
    }
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
    match upload(host, route, &name, &data) {
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

/// `purge`: delete every FILE in the card root — directories (site/,
/// scenes/, logs/) stay, exactly sd_sync's "clear the music, not the card".
fn do_purge(host: &str) -> ! {
    let listing = match request(host, "GET", "/api/files", b"", 10.0) {
        Err(e) => fail(&e),
        Ok(r) if !(200..300).contains(&r.code) => refuse(&format!(
            "{host} cannot list the card: {} {}",
            r.code,
            String::from_utf8_lossy(&r.body).trim_end()
        )),
        Ok(r) => String::from_utf8_lossy(&r.body).into_owned(),
    };
    let victims: Vec<String> = list_entries(&listing)
        .into_iter()
        .filter(|(_, dir)| !dir)
        .map(|(n, _)| n)
        .collect();
    if victims.is_empty() {
        println!("card root has no files");
        std::process::exit(0)
    }
    for name in victims {
        let target = format!("/api/files/{}", encode_query(&name));
        match request(host, "DELETE", &target, b"", 10.0) {
            Err(e) => fail(&e),
            Ok(r) if !(200..300).contains(&r.code) => refuse(&format!(
                "{host} kept {name}: {} {}",
                r.code,
                String::from_utf8_lossy(&r.body).trim_end()
            )),
            Ok(_) => println!("deleted {name}"),
        }
    }
    std::process::exit(0)
}

/// `ota FILE`: flash a firmware image over plain HTTP — sd_sync.cmd_ota's
/// choreography. Audio stops first (the standing rule: a decode mid-flash
/// competes for the same starved heap), the reply race is survivable (the
/// device reboots moments after the last byte lands), and the status poll
/// afterwards is the real verdict. CASTLE_OTA_WAIT_S bounds the poll.
fn do_ota(host: &str, args: &[String]) -> ! {
    let Some(path) = args.first() else {
        fail("ota needs a firmware .bin")
    };
    let data = std::fs::read(path).unwrap_or_else(|e| fail(&format!("cannot read {path}: {e}")));
    if data.first() != Some(&0xE9) {
        fail(&format!(
            "{path} does not look like an app image (no 0xE9 magic)"
        ));
    }
    let _ = request(host, "POST", "/api/stop", b"", 5.0);
    match request(host, "PUT", "/api/ota", &data, 180.0) {
        Ok(r) if (200..300).contains(&r.code) => {
            println!("{}", String::from_utf8_lossy(&r.body).trim_end())
        }
        Ok(r) => refuse(&format!(
            "{host} refused the image: {} {}",
            r.code,
            String::from_utf8_lossy(&r.body).trim_end()
        )),
        Err(_) => println!("(no reply — device likely rebooting)"),
    }
    let wait_s: f64 = std::env::var("CASTLE_OTA_WAIT_S")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(90.0);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(wait_s);
    loop {
        if let Ok(r) = request(host, "GET", "/api/status", b"", 3.0) {
            if (200..300).contains(&r.code) {
                let body = String::from_utf8_lossy(&r.body).into_owned();
                let v = castle_core::bridge::json_str(&body, "version").unwrap_or_default();
                println!("up — v{v}");
                println!(
                    "now CONFIRM it (connect once with tools/device.py or HA) — \
                     an unconfirmed image rolls back on its next reboot"
                );
                std::process::exit(0)
            }
        }
        if std::time::Instant::now() >= deadline {
            eprintln!(
                "castle: no answer after {wait_s} s — the bootloader rolls back \
                 to the previous image on the next power cycle"
            );
            std::process::exit(1)
        }
        std::thread::sleep(std::time::Duration::from_secs_f64(
            3.0_f64.min(wait_s / 3.0),
        ));
    }
}

fn main() {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let mut arg_host: Option<String> = None;
    if args.first().map(String::as_str) == Some("--host") {
        args.remove(0);
        if args.is_empty() {
            fail("--host needs a value")
        }
        arg_host = Some(args.remove(0));
    }
    let env_host = std::env::var("CASTLE_HOST").ok();
    let toml_path = std::env::var("CASTLE_DEVICES").unwrap_or_else(|_| "devices.toml".to_string());
    let toml = std::fs::read_to_string(&toml_path).unwrap_or_default();
    let verb = args.first().cloned().unwrap_or_default();
    if verb == "hosts" {
        // The candidate walk, best first — hosts.py's answer on the same
        // inputs; the parity test holds the two together.
        let arg = args.get(1).map(String::as_str).or(arg_host.as_deref());
        for c in hosts::candidates(arg, env_host.as_deref(), &toml) {
            println!("{c}");
        }
        std::process::exit(0)
    }
    let mut cands = hosts::candidates(arg_host.as_deref(), env_host.as_deref(), &toml);
    if cands.is_empty() {
        // resolve()'s floor: a CLI with no castle has nothing to do, so an
        // empty CASTLE_HOST falls through to the inventory here.
        cands = hosts::from_table(&toml);
    }
    if cands.is_empty() {
        fail("no castle named: pass --host, set CASTLE_HOST, or add a devices.toml entry");
    }
    let mut host = if cands.len() == 1 {
        cands[0].clone()
    } else {
        probe(&cands)
    };
    if !host.contains(':') {
        host.push_str(":80");
    }
    if verb == "put" {
        do_put(&host, &args[1..]);
    }
    if verb == "purge" {
        do_purge(&host);
    }
    if verb == "ota" {
        do_ota(&host, &args[1..]);
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
             put [--to site|scenes] LOCAL [NAME]|rm NAME|purge|ota BIN|hosts [ARG]",
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
