//! The castle relay — tools/castle_link.py, caches and all: the desk
//! polls status continuously and the castle should not pay for every
//! poll; a dead castle stays presumed dead for a moment so a castle-less
//! desk's polls don't each pay the connect timeout; whichever host last
//! answered is tried first. The native-API leg (aioesphomeapi, flash
//! build only) is deliberately not ported — the SD build's HTTP is the
//! desk's transport, and the plan's esphome-native-api crate swap owns
//! that story later.

use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use crate::bridge::{self, CallFault};
use crate::jsonio::{self, Json};
use crate::studio::App;
use crate::{hosts, httpd::Reply};

pub const TIMEOUT_S: f64 = 2.0;
pub const PROBE_CONNECT_S: f64 = 1.0;
const STATUS_TTL_S: f64 = 1.5;
const DOWN_TTL_S: f64 = 3.0;

/// Every route the firmware actually serves — castle_link.KNOWN_API.
pub const KNOWN_API: [&str; 15] = [
    "/api/status",
    "/api/health",
    "/api/files",
    "/api/play",
    "/api/stop",
    "/api/scene",
    "/api/volume",
    "/api/light",
    "/api/pir",
    "/api/show/start",
    "/api/show/stop",
    "/api/blackout",
    "/api/bootlog",
    "/api/ota",
    "/remote",
];
pub const KNOWN_PREFIX: [&str; 4] = ["/api/files/", "/api/site/", "/api/scenes/", "/sd/"];

pub fn known_api(target: &str) -> bool {
    let p = target.split('?').next().unwrap_or("");
    KNOWN_API.contains(&p) || KNOWN_PREFIX.iter().any(|pre| p.starts_with(pre))
}

/// castle_link's per-verb read budgets (READ_BUDGET_S / TIMEOUT_S).
fn read_budget(method: &str, target: &str) -> f64 {
    let p = target.split('?').next().unwrap_or("");
    match method {
        "PUT" => 60.0,
        "DELETE" => 30.0,
        "GET" if p.starts_with("/sd/") => 60.0,
        "GET" if p.starts_with("/api/files") => 5.0,
        _ => TIMEOUT_S,
    }
}

struct Caches {
    status: Option<(Instant, Json)>,
    down: Option<Instant>,
    up: Option<String>,
}

fn caches() -> &'static Mutex<Caches> {
    static C: OnceLock<Mutex<Caches>> = OnceLock::new();
    C.get_or_init(|| {
        Mutex::new(Caches {
            status: None,
            down: None,
            up: None,
        })
    })
}

fn with_port(h: &str) -> String {
    if h.contains(':') {
        h.to_string()
    } else {
        format!("{h}:80")
    }
}

fn candidates(app: &App) -> Vec<String> {
    let toml_path = match std::env::var("CASTLE_DEVICES") {
        Ok(v) if !v.is_empty() => std::path::PathBuf::from(v),
        _ => app.root.join("devices.toml"),
    };
    let toml = std::fs::read_to_string(toml_path).unwrap_or_default();
    let env = std::env::var("CASTLE_HOST").ok();
    hosts::candidates(None, env.as_deref(), &toml)
}

/// castle_link.castle_hosts — every address worth trying, best first;
/// whichever host last answered is remembered and tried first.
pub fn castle_hosts(app: &App) -> Vec<String> {
    let mut hosts = candidates(app);
    let up = caches()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .up
        .clone();
    if let Some(h) = up {
        if let Some(at) = hosts.iter().position(|c| *c == h) {
            let h = hosts.remove(at);
            hosts.insert(0, h);
        }
    }
    hosts
}

/// The castle's current best address, or None when none is configured.
pub fn castle_host(app: &App) -> Option<String> {
    castle_hosts(app).into_iter().next()
}

/// castle_link.status — the castle's /api/status, briefly cached, marked
/// `bridged`; None if unreachable (which is itself cached briefly).
pub fn status(app: &App) -> Option<Json> {
    let now = Instant::now();
    {
        let c = caches().lock().unwrap_or_else(|e| e.into_inner());
        if let Some((t, v)) = &c.status {
            if now.duration_since(*t).as_secs_f64() < STATUS_TTL_S {
                return Some(v.clone());
            }
        }
        if let Some(t) = c.down {
            if now.duration_since(t).as_secs_f64() < DOWN_TTL_S {
                return None;
            }
        }
    }
    let hosts = castle_hosts(app);
    if hosts.is_empty() {
        return None;
    }
    let mut found: Option<(Vec<(String, Json)>, String)> = None;
    for h in &hosts {
        if let Ok(r) = bridge::call(
            &with_port(h),
            "GET",
            "/api/status",
            b"",
            PROBE_CONNECT_S,
            TIMEOUT_S,
        ) {
            if r.code == 200 {
                if let Ok(Json::Obj(o)) = jsonio::parse(&String::from_utf8_lossy(&r.body)) {
                    found = Some((o, h.clone()));
                    break;
                }
            }
        }
    }
    let mut c = caches().lock().unwrap_or_else(|e| e.into_inner());
    match found {
        None => {
            c.down = Some(now);
            None
        }
        Some((mut o, good)) => {
            c.down = None;
            c.up = Some(good.clone());
            o.push(("bridged".into(), Json::Str(good)));
            let v = Json::Obj(o);
            c.status = Some((now, v.clone()));
            Some(v)
        }
    }
}

/// castle_link.forward — relay one request verbatim: the allowlist, the
/// host walk, the GET-retries-but-writes-don't rule, and the cache pokes
/// (an answer of any kind proves the castle is up; a non-GET success
/// invalidates the status the desk re-polls a second later).
pub fn forward(app: &App, method: &str, target: &str, body: &[u8]) -> (u16, Vec<u8>, String) {
    let json = "application/json".to_string();
    if !known_api(target) {
        let mut known: Vec<&str> = KNOWN_API
            .iter()
            .chain(KNOWN_PREFIX.iter())
            .copied()
            .collect();
        known.sort_unstable();
        let out = Json::Obj(vec![
            ("error".into(), Json::Str("unknown castle route".into())),
            (
                "known".into(),
                Json::Arr(known.into_iter().map(|k| Json::Str(k.into())).collect()),
            ),
        ]);
        return (404, jsonio::dumps(&out).into_bytes(), json);
    }
    let hosts = castle_hosts(app);
    if hosts.is_empty() {
        return (502, b"{\"error\": \"no castle configured\"}".to_vec(), json);
    }
    let read_s = read_budget(method, target);
    for h in &hosts {
        match bridge::call(&with_port(h), method, target, body, TIMEOUT_S, read_s) {
            Err(CallFault::Unreachable(_)) => continue,
            Err(CallFault::Stalled(_)) => {
                if method == "GET" {
                    continue; // nothing changed anywhere; the next host may do
                }
                let out = Json::Obj(vec![(
                    "error".into(),
                    Json::Str(
                        "castle took the request but did not answer in time — it may \
                         have landed; check before sending again"
                            .into(),
                    ),
                )]);
                return (504, jsonio::dumps(&out).into_bytes(), json);
            }
            Ok(r) => {
                let mut c = caches().lock().unwrap_or_else(|e| e.into_inner());
                c.down = None;
                if (200..300).contains(&r.code) {
                    c.up = Some(h.clone());
                    if method != "GET" {
                        c.status = None;
                    }
                }
                return (r.code, r.body, r.ctype);
            }
        }
    }
    (502, b"{\"error\": \"castle not reachable\"}".to_vec(), json)
}

/// The route-level relay: forward, answered as the castle answered.
pub fn relay_reply(app: &App, method: &str, target: &str, body: &[u8]) -> Reply {
    let (code, body, ctype) = forward(app, method, target, body);
    Reply::Raw { code, body, ctype }
}

/// The desk's mode probe: the castle's status when one answers, else the
/// studio's own marker naming who it tried (no castle key = none
/// configured, a simulator on purpose).
pub fn status_reply(app: &App) -> Reply {
    if let Some(v) = status(app) {
        return Reply::Json(v, 200);
    }
    let mut o = vec![("studio".into(), Json::Bool(true))];
    if let Some(c) = castle_host(app) {
        o.push(("castle".into(), Json::Str(c)));
    }
    Reply::Json(Json::Obj(o), 200)
}
