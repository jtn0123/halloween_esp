//! The studio's route handlers — what each endpoint MEANS (pass 1: the
//! read side — page, tracks, streams — plus track deletion and the relay's
//! allowlist walk). The write groups (import, jobs, scenes, publish) land
//! with their own passes; until then those paths answer 404 like any
//! unknown route, and DELETE ?scene=1 says plainly it is not here yet.

use crate::httpd::{Reply, Request};
use crate::jsonio::{self, Json};
use crate::studio::{scene_audio, scene_ids, studio_path, App, API};
use std::sync::Arc;

use crate::studio_import as si;
use crate::studio_scenes as ssc;
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
pub fn castle_status(app: &App) -> Option<Vec<(String, Json)>> {
    for h in &candidates(app) {
        if let Ok(r) = bridge::request(&with_port(h), "GET", "/api/status", b"", 2.0) {
            if r.code == 200 {
                if let Ok(Json::Obj(mut o)) = jsonio::parse(&String::from_utf8_lossy(&r.body)) {
                    o.push(("bridged".into(), Json::Str(h.clone())));
                    return Some(o);
                }
            }
        }
    }
    None
}

/// studio_http.json_body — the request body as an object, or the client's
/// mistake (a 400, never a dead socket). The parse-error text is this
/// parser's, not CPython's; the shape is the contract.
pub(crate) fn json_body(body: &[u8]) -> Result<Json, String> {
    if body.is_empty() {
        return Ok(Json::obj());
    }
    let text = String::from_utf8_lossy(body);
    let parsed =
        jsonio::parse(&text).map_err(|e| format!("request body is not valid JSON: {e}"))?;
    if !matches!(parsed, Json::Obj(_)) {
        return Err("request body must be a JSON object".to_string());
    }
    Ok(parsed)
}

pub(crate) fn bad_request(msg: &str) -> Reply {
    Reply::Json(
        Json::Obj(vec![
            ("ok".into(), Json::Bool(false)),
            ("error".into(), Json::Str(msg.to_string())),
        ]),
        400,
    )
}

pub fn status_reply(app: &App) -> Reply {
    let hosts = candidates(app);
    if hosts.is_empty() {
        return Reply::Json(Json::Obj(vec![("studio".into(), Json::Bool(true))]), 200);
    }
    if let Some(o) = castle_status(app) {
        return Reply::Json(Json::Obj(o), 200);
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

pub fn handle(app: &Arc<App>, req: &Request) -> Reply {
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
    if let Some(rest) = path.strip_prefix("/studio/job/") {
        return si::job_get(app, &last_segment(rest));
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
        let scene = req.query().iter().any(|(k, _)| k == "scene");
        let p = st::track_path(&app.tracks, &tid);
        // ?scene=1 with the file already gone: the scene is an orphan and
        // taking it out is the whole point.
        if p.is_none() && !scene {
            return jerr("not found", 404);
        }
        if let Some(p) = &p {
            let _ = std::fs::remove_file(p);
        }
        for kept in st::source_copies(&app.tracks, &tid) {
            let _ = std::fs::remove_file(kept);
        }
        let _ = manifest::forget(&app.tracks, &tid);
        let mut body = vec![
            ("ok".into(), Json::Bool(true)),
            ("removed".into(), Json::Str(tid.clone())),
            ("file_missing".into(), Json::Bool(p.is_none())),
        ];
        let mut code = 200;
        if scene {
            let (res, _c) = ssc::remove(app, &tid);
            let ok = matches!(res.get("ok"), Some(Json::Bool(true)));
            jsonio::obj_update(
                &mut body,
                vec![
                    (
                        "scene_removed".into(),
                        res.get("removed").cloned().unwrap_or(Json::Bool(false)),
                    ),
                    (
                        "scenes".into(),
                        res.get("scenes").cloned().unwrap_or(Json::Arr(Vec::new())),
                    ),
                    (
                        "log".into(),
                        res.get("log").cloned().unwrap_or(Json::Str(String::new())),
                    ),
                ],
            );
            if !ok {
                // app.failed()'s ok/log keys; its one-line `reason` rides
                // in with the jobs pass.
                jsonio::obj_update(
                    &mut body,
                    vec![
                        ("ok".into(), Json::Bool(false)),
                        (
                            "log".into(),
                            res.get("log").cloned().unwrap_or(Json::Str(String::new())),
                        ),
                    ],
                );
                code = 500;
            }
        }
        return Reply::Json(Json::Obj(body), code);
    }
    if path.starts_with(API) {
        return relay(app, "DELETE", &req.target, &req.body);
    }
    jerr("not found", 404)
}

fn post(app: &Arc<App>, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    if path == "/studio/import" {
        return si::do_import(app, req);
    }
    if path == "/studio/import/async" {
        return si::import_async(app, req);
    }
    if path == "/studio/stems" {
        return si::stems_post(app, req);
    }
    if path == "/studio/refresh" {
        return si::refresh(app, req);
    }
    if path == "/studio/scene" {
        // The studio's scenes.yaml editor (JSON body); /api/scene?s=<id>
        // is the castle's fire-a-scene and stayed on the relay above.
        let body = match json_body(&req.body) {
            Ok(v) => v,
            Err(e) => return bad_request(&e),
        };
        let (mut out, code) = ssc::splice(app, &body);
        if let Json::Obj(o) = &mut out {
            let not_ok = !matches!(
                o.iter().find(|(k, _)| k == "ok"),
                Some((_, Json::Bool(true)))
            );
            let log = o
                .iter()
                .find(|(k, _)| k == "log")
                .and_then(|(_, v)| v.as_str())
                .unwrap_or("")
                .to_string();
            if not_ok && !log.is_empty() {
                o.push((
                    "reason".into(),
                    Json::Str(crate::studio_reason::reason(&log)),
                ));
            }
        }
        return Reply::Json(out, code);
    }
    if path == "/studio/rebuild" {
        let (ok, log) = ssc::rebuild(app);
        return Reply::Json(
            Json::Obj(vec![
                ("ok".into(), Json::Bool(ok)),
                ("log".into(), Json::Str(log)),
            ]),
            if ok { 200 } else { 500 },
        );
    }
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
