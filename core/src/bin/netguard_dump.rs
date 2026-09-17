//! What castle-core's netguard answers for a corpus of (url, caller) —
//! the *_dump pattern (parity_dump, pulse_dump, synth_dump) applied to
//! the one port that had no cross-language gate: the SSRF guard.
//!
//! stdin is one JSON object; stdout is one JSON array, an entry per case,
//! `null` where the fetch is allowed and the refusal sentence where it is
//! not:
//!
//!     {"dns": {"router.lan": ["192.168.1.1"]},
//!      "cases": [{"url": "http://router.lan/", "ip": "192.168.1.20"}]}
//!
//! DNS is the input tests/test_netguard.py mocks and a binary cannot, so
//! it is supplied: the table stands in for getaddrinfo (a name that is not
//! in it does not resolve, exactly like the Python's fake), while an IP
//! literal still answers as itself. No socket is opened.

use std::io::Read;
use std::net::IpAddr;

use castle_core::jsonio::{Json, dumps, parse};
use castle_core::netguard::refuse_reason_with;

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        eprintln!("netguard_dump: could not read stdin");
        std::process::exit(1);
    }
    let doc = match parse(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("netguard_dump: {e}");
            std::process::exit(1);
        }
    };
    let dns: Vec<(String, Vec<IpAddr>)> = match doc.get("dns") {
        Some(Json::Obj(pairs)) => pairs
            .iter()
            .map(|(host, v)| {
                let ips = match v {
                    Json::Arr(items) => items
                        .iter()
                        .filter_map(|i| i.as_str()?.parse().ok())
                        .collect(),
                    _ => Vec::new(),
                };
                (host.to_lowercase(), ips)
            })
            .collect(),
        _ => Vec::new(),
    };
    // netguard::resolve's own first rule: a literal is itself, and only a
    // NAME goes to the table.
    let resolver = |host: &str| -> Vec<IpAddr> {
        if let Ok(a) = host.parse() {
            return vec![a];
        }
        dns.iter()
            .find(|(h, _)| h == &host.to_lowercase())
            .map(|(_, ips)| ips.clone())
            .unwrap_or_default()
    };
    let cases = match doc.get("cases") {
        Some(Json::Arr(items)) => items.clone(),
        _ => Vec::new(),
    };
    let out: Vec<Json> = cases
        .iter()
        .map(|c| {
            let url = c.str_or("url", "");
            let ip = c.str_or("ip", "");
            match refuse_reason_with(&url, &ip, &resolver) {
                None => Json::Null,
                Some(why) => Json::Str(why),
            }
        })
        .collect();
    println!("{}", dumps(&Json::Arr(out)));
}
