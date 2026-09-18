//! The studio's route handlers — what each endpoint MEANS. The whole
//! surface is here now: the read side (page, tracks, streams, waveforms),
//! the write side (import and its jobs, scene edits, rebuild, publish),
//! deletion, and the relay's allowlist walk out to the castle.
//!
//! This file is the dispatcher and the small answers; the work each verb
//! stands for lives next door (`studio_import`, `studio_jobs`,
//! `studio_media`, `studio_probe`, `studio_relay`, `studio_scenes`,
//! `studio_tracks`). Route for route it answers what `tools/studio_routes.py`
//! answers — docs/API.md is the table, and tests/test_studio_rust.py and
//! its siblings hold the two servers' replies equal.

use crate::httpd::{Reply, Request};
use crate::jsonio::{self, Json};
use crate::studio::{API, App};
use crate::studio_alias::studio_path;
use std::sync::{Arc, PoisonError};

use crate::studio_import as si;
use crate::studio_relay as rl;
use crate::studio_scenes as ssc;
use crate::{manifest, studio_tracks as st};

pub(crate) fn jerr(msg: &str, code: u16) -> Reply {
    Reply::Json(
        Json::Obj(vec![("error".into(), Json::Str(msg.into()))]),
        code,
    )
}

/// The relay itself (allowlist, host walk, TTL caches) lives in
/// studio_relay — castle_link's own seam; this shim keeps the route
/// bodies reading like the Python's.
pub(crate) fn relay(app: &App, method: &str, target: &str, body: &[u8]) -> Reply {
    rl::relay_reply(app, method, target, body)
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

/// Path(...).name — the traversal-stripping last segment.
pub(crate) fn last_segment(s: &str) -> String {
    s.trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_string()
}

pub fn handle(app: &Arc<App>, req: &Request) -> Reply {
    match req.method.as_str() {
        "GET" => crate::studio_routes_get::get(app, req),
        "DELETE" => delete(app, req),
        "POST" => post(app, req),
        "PUT" => put(app, req),
        _ => jerr("not found", 404),
    }
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

/// A POST group: Some(reply) when one of its paths matched.
type Post = fn(&Arc<App>, &Request, &str) -> Option<Reply>;

fn post(app: &Arc<App>, req: &Request) -> Reply {
    let groups: [Post; 4] = [imports, probes, server, show];
    let path = studio_path(&req.target);
    for group in groups {
        if let Some(r) = group(app, req, &path) {
            return r;
        }
    }
    if path.starts_with(API) {
        return relay(app, "POST", &req.target, &req.body);
    }
    jerr("not found", 404)
}

/// Everything that brings audio into the library.
fn imports(app: &Arc<App>, req: &Request, path: &str) -> Option<Reply> {
    match path {
        "/studio/import" => Some(si::do_import(app, req)),
        "/studio/import/async" => Some(si::import_async(app, req)),
        "/studio/stems" => Some(si::stems_post(app, req)),
        "/studio/refresh" => Some(si::refresh(app, req)),
        _ => None,
    }
}

/// The two that ask ffmpeg about something before it is a track.
fn probes(app: &Arc<App>, req: &Request, path: &str) -> Option<Reply> {
    if path == "/studio/compare" {
        let body = match json_body(&req.body) {
            Ok(v) => v,
            Err(e) => return Some(bad_request(&e)),
        };
        // ffmpeg four times over; serialise with every other encode job.
        let (out, code) = {
            let _g = app.oplock.lock().unwrap_or_else(PoisonError::into_inner);
            crate::studio_probe::compare(app, &body)
        };
        return Some(Reply::Json(out, code));
    }
    if path == "/studio/probe" {
        let body = match json_body(&req.body) {
            Ok(v) => v,
            Err(e) => return Some(bad_request(&e)),
        };
        let url = body.str_or("url", "").trim().to_string();
        if let Some(why) = crate::netguard::refuse_reason(&url, &req.client_ip) {
            return Some(jerr(&why, 400));
        }
        // A bad or unreadable link is the caller's problem: 400, not 200.
        let (out, ok) = crate::studio_probe::probe(&url);
        return Some(Reply::Json(out, if ok { 200 } else { 400 }));
    }
    None
}

/// Stop and restart: answer first, then act — the page must see the
/// verdict, not a dead socket.
fn server(_app: &Arc<App>, _req: &Request, path: &str) -> Option<Reply> {
    let (action, key) = match path {
        "/studio/server/stop" => (crate::studio::Action::Stop, "stopping"),
        "/studio/server/restart" => (crate::studio::Action::Restart, "restarting"),
        _ => return None,
    };
    crate::studio::schedule(action);
    Some(Reply::Json(
        Json::Obj(vec![
            ("ok".into(), Json::Bool(true)),
            (key.into(), Json::Bool(true)),
        ]),
        200,
    ))
}

/// The show: a scene edit, the rebuild it needs, the push to the castle.
fn show(app: &Arc<App>, req: &Request, path: &str) -> Option<Reply> {
    if path == "/studio/scene" {
        // The studio's scenes.yaml editor (JSON body); /api/scene?s=<id>
        // is the castle's fire-a-scene and stayed on the relay above.
        let body = match json_body(&req.body) {
            Ok(v) => v,
            Err(e) => return Some(bad_request(&e)),
        };
        let (mut out, code) = ssc::splice(app, &body);
        add_reason(&mut out);
        return Some(Reply::Json(out, code));
    }
    if path == "/studio/rebuild" {
        let (ok, log) = ssc::rebuild(app);
        return Some(Reply::Json(
            Json::Obj(vec![
                ("ok".into(), Json::Bool(ok)),
                ("log".into(), Json::Str(log)),
            ]),
            if ok { 200 } else { 500 },
        ));
    }
    if path == "/studio/publish" {
        // The last mile: sd_sync scenes (audio + cue files + show.man) +
        // lean site, and what still needs a reboot; rebuild() runs it too
        // when a castle answers. No oplock: the push only reads the build
        // tree and talks to the castle, and holding the gate across a
        // network round-trip queued every import and scene write behind it
        // (grade report 2026-09-17 pm G2; `a_publish_does_not_want_the_oplock`
        // in studio_publish.rs pins it).
        let (out, code) = crate::studio_publish::publish_body(app);
        return Some(Reply::Json(out, code));
    }
    None
}

/// A failed scene splice carries its log; the desk shows the one-line
/// verdict beside it.
fn add_reason(out: &mut Json) {
    let Json::Obj(o) = out else {
        return;
    };
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

fn put(app: &App, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    if !path.starts_with(API) {
        return jerr("not found", 404);
    }
    relay(app, "PUT", &req.target, &req.body)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn code_of(r: &Reply) -> u16 {
        match r {
            Reply::Json(_, c) | Reply::JsonShared(_, c) | Reply::Raw { code: c, .. } => *c,
            _ => 0,
        }
    }

    /// Every id in a path goes through this before it reaches the disk:
    /// Path(...).name is what stops `../` from meaning anything.
    #[test]
    fn the_last_segment_is_all_a_route_ever_trusts() {
        assert_eq!(last_segment("vigil"), "vigil");
        assert_eq!(last_segment("/studio/track/vigil"), "vigil");
        assert_eq!(last_segment("../../etc/passwd"), "passwd");
        assert_eq!(last_segment("/a/b/"), "b");
        assert_eq!(last_segment("/a/b///"), "b");
        assert_eq!(last_segment(""), "");
        assert_eq!(last_segment("/"), "");
        // Only the separator is structural — a dotted name survives whole.
        assert_eq!(last_segment("a/b.c.mp3"), "b.c.mp3");
    }

    #[test]
    fn a_body_is_an_object_an_empty_body_or_the_callers_mistake() {
        assert_eq!(json_body(b""), Ok(Json::obj()));
        assert_eq!(json_body(b"{}"), Ok(Json::obj()));
        assert_eq!(
            json_body(b"{\"id\": \"vigil\"}"),
            Ok(Json::Obj(vec![("id".into(), Json::Str("vigil".into()))]))
        );
        // A list, a number or a bare string is valid JSON and still not a
        // request body — the routes all index it by key.
        for not_an_object in [&b"[1, 2]"[..], b"7", b"\"vigil\"", b"null"] {
            assert_eq!(
                json_body(not_an_object),
                Err("request body must be a JSON object".to_string())
            );
        }
        let e = json_body(b"{oops}").expect_err("not JSON at all");
        assert!(e.starts_with("request body is not valid JSON:"), "{e}");
    }

    #[test]
    fn the_two_error_shapes_are_the_ones_the_desk_reads() {
        let r = jerr("not found", 404);
        assert_eq!(code_of(&r), 404);
        let Reply::Json(body, _) = &r else {
            panic!("jerr answers JSON")
        };
        assert_eq!(body.get("error").and_then(Json::as_str), Some("not found"));
        // bad_request carries the ok:false flag the desk switches on.
        let r = bad_request("Content-Length is not a number");
        assert_eq!(code_of(&r), 400);
        let Reply::Json(body, _) = &r else {
            panic!("bad_request answers JSON")
        };
        assert_eq!(body.get("ok"), Some(&Json::Bool(false)));
        assert_eq!(
            body.get("error").and_then(Json::as_str),
            Some("Content-Length is not a number")
        );
    }
}
