//! The studio's route handlers — what each endpoint MEANS (pass 1: the
//! read side — page, tracks, streams — plus track deletion and the relay's
//! allowlist walk). The write groups (import, jobs, scenes, publish) land
//! with their own passes; until then those paths answer 404 like any
//! unknown route, and DELETE ?scene=1 says plainly it is not here yet.

use crate::httpd::{Reply, Request};
use crate::jsonio::{self, Json};
use crate::studio::{scene_audio, scene_ids, studio_path, App, API};
use crate::{bridge, hosts, manifest, studio_media as sm, studio_tracks as st};

fn jerr(msg: &str, code: u16) -> Reply {
    Reply::Json(
        Json::Obj(vec![("error".into(), Json::Str(msg.into()))]),
        code,
    )
}

/// castle_link.KNOWN_API — every route the firmware actually serves.
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
        _ => 2.0,
    }
}

/// Who the castle is, best first — castle_link.castle_hosts minus the
/// remembered-up reordering (a cache the relay pass brings).
fn candidates(app: &App) -> Vec<String> {
    let toml_path = match std::env::var("CASTLE_DEVICES") {
        Ok(v) if !v.is_empty() => std::path::PathBuf::from(v),
        _ => app.root.join("devices.toml"),
    };
    let toml = std::fs::read_to_string(toml_path).unwrap_or_default();
    let env = std::env::var("CASTLE_HOST").ok();
    hosts::candidates(None, env.as_deref(), &toml)
}

fn with_port(h: &str) -> String {
    if h.contains(':') {
        h.to_string()
    } else {
        format!("{h}:80")
    }
}

/// bridge::request's connect-stage failures — the body never left, so the
/// next host may be tried for any verb (castle_link.Unreachable).
fn unreachable_err(e: &str) -> bool {
    e.starts_with("cannot reach") || e.starts_with("cannot resolve") || e.starts_with("no address")
}

/// castle_link.forward, the pass-1 subset: the allowlist, the host walk,
/// and the GET-retries-but-writes-don't rule. No TTL caches or native leg
/// yet — those arrive with the relay pass.
pub fn relay(app: &App, method: &str, target: &str, body: &[u8]) -> Reply {
    if !known_api(target) {
        let mut known: Vec<&str> = KNOWN_API
            .iter()
            .chain(KNOWN_PREFIX.iter())
            .copied()
            .collect();
        known.sort_unstable();
        return Reply::Json(
            Json::Obj(vec![
                ("error".into(), Json::Str("unknown castle route".into())),
                (
                    "known".into(),
                    Json::Arr(known.into_iter().map(|k| Json::Str(k.into())).collect()),
                ),
            ]),
            404,
        );
    }
    let hosts = candidates(app);
    if hosts.is_empty() {
        return jerr("no castle configured", 502);
    }
    let budget = read_budget(method, target);
    for h in &hosts {
        match bridge::request(&with_port(h), method, target, body, budget) {
            Ok(r) => {
                return Reply::Raw {
                    code: r.code,
                    body: r.body,
                    ctype: r.ctype,
                }
            }
            Err(e) if unreachable_err(&e) => continue,
            Err(_) => {
                if method == "GET" {
                    continue; // nothing changed anywhere; the next host may do
                }
                return jerr(
                    "castle took the request but did not answer in time — it may \
                     have landed; check before sending again",
                    504,
                );
            }
        }
    }
    jerr("castle not reachable", 502)
}

/// The desk's mode probe: the castle's status when one answers (marked
/// `bridged`), else the studio's own marker naming who it tried.
pub fn status_reply(app: &App) -> Reply {
    let hosts = candidates(app);
    if hosts.is_empty() {
        return Reply::Json(Json::Obj(vec![("studio".into(), Json::Bool(true))]), 200);
    }
    for h in &hosts {
        if let Ok(r) = bridge::request(&with_port(h), "GET", "/api/status", b"", 2.0) {
            if r.code == 200 {
                if let Ok(Json::Obj(mut o)) = jsonio::parse(&String::from_utf8_lossy(&r.body)) {
                    o.push(("bridged".into(), Json::Str(h.clone())));
                    return Reply::Json(Json::Obj(o), 200);
                }
            }
        }
    }
    Reply::Json(
        Json::Obj(vec![
            ("studio".into(), Json::Bool(true)),
            ("castle".into(), Json::Str(hosts[0].clone())),
        ]),
        200,
    )
}

/// Path(...).name — the traversal-stripping last segment.
fn last_segment(s: &str) -> String {
    s.trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_string()
}

pub fn handle(app: &App, req: &Request) -> Reply {
    match req.method.as_str() {
        "GET" => get(app, req),
        "DELETE" => delete(app, req),
        "POST" => post(app, req),
        "PUT" => put(app, req),
        _ => jerr("not found", 404),
    }
}

fn get(app: &App, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    if path == "/" || path == "/index.html" {
        let (page, _) = app.served();
        if !page.exists() {
            return jerr("previewer not built", 404);
        }
        return match app.lean_page(&page) {
            Ok((body, etag)) => Reply::Page {
                body,
                ctype: "text/html; charset=utf-8",
                etag,
            },
            Err(e) => Reply::Json(
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    ("error".into(), Json::Str(format!("OSError: {e}"))),
                ]),
                500,
            ),
        };
    }
    if let Some(rest) = path.strip_prefix("/studio/scene-audio/") {
        let name = last_segment(rest);
        let (_, audio) = app.served();
        return match scene_audio(&audio, &name) {
            None => jerr("no such scene audio", 404),
            Some(p) => Reply::FileRange {
                path: p,
                ctype: "audio/mpeg".into(),
            },
        };
    }
    if path == "/remote" {
        return relay(app, "GET", &req.target, b"");
    }
    if path == "/api/status" {
        return status_reply(app);
    }
    if path == "/studio/tracks" {
        let _ = std::fs::create_dir(&app.tracks);
        return Reply::Json(
            Json::Obj(vec![
                ("tracks".into(), Json::Arr(st::track_infos(&app.tracks))),
                (
                    "scenes".into(),
                    Json::Arr(scene_ids(&app.scenes).into_iter().map(Json::Str).collect()),
                ),
            ]),
            200,
        );
    }
    if let Some(rest) = path.strip_prefix("/studio/waveform/") {
        let sens = sm::parse_sensitivity(&req.query());
        let name = last_segment(rest);
        let Some(p) = st::track_path(&app.tracks, &name) else {
            return jerr("no such track", 404);
        };
        return match sm::waveform(&p, sens) {
            Some(obj) => Reply::Json(obj, 200),
            None => Reply::Json(
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    (
                        "error".into(),
                        Json::Str(format!("could not decode {}", p.display())),
                    ),
                ]),
                500,
            ),
        };
    }
    if let Some(rest) = path.strip_prefix("/studio/stems/") {
        let (obj, code) = sm::stems_analysis(&app.tracks, &last_segment(rest));
        return Reply::Json(obj, code);
    }
    if path.starts_with("/studio/stem/") {
        let parts: Vec<&str> = path.split('/').collect();
        let hit = (parts.len() >= 5)
            .then(|| sm::stem_file(&app.tracks, parts[parts.len() - 2], parts[parts.len() - 1]))
            .flatten();
        return match hit {
            None => jerr("no such stem", 404),
            Some(p) => Reply::FileRange {
                path: p,
                ctype: "audio/mpeg".into(),
            },
        };
    }
    if path.starts_with("/studio/compare/") {
        let parts: Vec<&str> = path.split('/').collect();
        let hit = (parts.len() >= 5)
            .then(|| sm::compare_file(parts[parts.len() - 2], parts[parts.len() - 1]))
            .flatten();
        return match hit {
            None => jerr("no such comparison", 404),
            Some(p) => {
                let ctype = st::mime(p.extension().and_then(|e| e.to_str()).unwrap_or(""));
                Reply::FileRange {
                    path: p,
                    ctype: ctype.into(),
                }
            }
        };
    }
    if let Some(rest) = path.strip_prefix("/studio/track/") {
        let name = last_segment(rest);
        // rpartition("."): a known audio extension is stripped, anything
        // else is part of the id it will fail to be.
        let tid = match name.rfind('.') {
            Some(at) if at > 0 && st::AUDIO_EXT.contains(&&name[at + 1..]) => &name[..at],
            _ => name.as_str(),
        };
        return match st::track_path(&app.tracks, tid) {
            None => jerr("not found", 404),
            Some(p) => {
                let ctype = st::mime(p.extension().and_then(|e| e.to_str()).unwrap_or(""));
                Reply::FileRange {
                    path: p,
                    ctype: ctype.into(),
                }
            }
        };
    }
    if let Some(rest) = path.strip_prefix("/studio/card/") {
        let name = last_segment(rest);
        if name.is_empty() {
            return jerr("no file name", 400);
        }
        return relay(app, "GET", &format!("/sd/{name}"), b"");
    }
    if path.starts_with(API) {
        return relay(app, "GET", &req.target, b"");
    }
    jerr("not found", 404)
}

fn delete(app: &App, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    if let Some(rest) = path.strip_prefix("/studio/tracks/") {
        let tid = last_segment(rest);
        if req.query().iter().any(|(k, _)| k == "scene") {
            // Needs studio_scenes.remove + the rebuild chain.
            return Reply::Json(
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    (
                        "error".into(),
                        Json::Str("scene removal arrives with the scenes pass".into()),
                    ),
                ]),
                500,
            );
        }
        let Some(p) = st::track_path(&app.tracks, &tid) else {
            return jerr("not found", 404);
        };
        let _ = std::fs::remove_file(&p);
        for kept in st::source_copies(&app.tracks, &tid) {
            let _ = std::fs::remove_file(kept);
        }
        let _ = manifest::forget(&app.tracks, &tid);
        return Reply::Json(
            Json::Obj(vec![
                ("ok".into(), Json::Bool(true)),
                ("removed".into(), Json::Str(tid)),
                ("file_missing".into(), Json::Bool(false)),
            ]),
            200,
        );
    }
    if path.starts_with(API) {
        return relay(app, "DELETE", &req.target, &req.body);
    }
    jerr("not found", 404)
}

fn post(app: &App, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    if path.starts_with(API) {
        return relay(app, "POST", &req.target, &req.body);
    }
    jerr("not found", 404)
}

fn put(app: &App, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    if !path.starts_with(API) {
        return jerr("not found", 404);
    }
    relay(app, "PUT", &req.target, &req.body)
}
