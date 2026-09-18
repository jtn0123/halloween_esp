//! The studio's read side — every GET the desk makes.
//!
//! Split from `studio_routes.rs` when the one `get` function was both the
//! whole read surface and a single 33-branch chain: the seam the module doc
//! next door already names (read side / write side) is the one taken here.
//! The order of the tries is the order the Python's `if` chain has them in,
//! group by group, so a path that two routes could claim still goes to the
//! same one.

use crate::httpd::{Reply, Request};
use crate::jsonio::Json;
use crate::studio::{API, App, scene_audio, scene_ids};
use crate::studio_alias::studio_path;
use crate::studio_routes::{jerr, last_segment, relay};
use crate::{studio_media as sm, studio_tracks as st};

/// A route group: Some(reply) when one of its paths matched.
type Probe = fn(&App, &Request, &str) -> Option<Reply>;

pub(crate) fn get(app: &App, req: &Request) -> Reply {
    let path = studio_path(&req.target);
    let probes: [Probe; 4] = [page, small, analysis, files];
    for probe in probes {
        if let Some(r) = probe(app, req, &path) {
            return r;
        }
    }
    if path.starts_with(API) {
        return relay(app, "GET", &req.target, b"");
    }
    jerr("not found", 404)
}

/// The desk itself, rewritten lean at serve time.
fn page(app: &App, _req: &Request, path: &str) -> Option<Reply> {
    if path != "/" && path != "/index.html" {
        return None;
    }
    let (page, _) = app.served();
    if !page.exists() {
        return Some(jerr("previewer not built", 404));
    }
    Some(match app.lean_page(&page) {
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
    })
}

/// The short answers: a scene's audio, the remote page, a job's progress,
/// the castle's status, the library listing.
fn small(app: &App, req: &Request, path: &str) -> Option<Reply> {
    if let Some(rest) = path.strip_prefix("/studio/scene-audio/") {
        let name = last_segment(rest);
        let (_, audio) = app.served();
        return Some(match scene_audio(&audio, &name) {
            None => jerr("no such scene audio", 404),
            Some(p) => Reply::FileRange {
                path: p,
                ctype: "audio/mpeg".into(),
            },
        });
    }
    if path == "/remote" {
        return Some(relay(app, "GET", &req.target, b""));
    }
    if let Some(rest) = path.strip_prefix("/studio/job/") {
        return Some(crate::studio_import::job_get(app, &last_segment(rest)));
    }
    if path == "/api/status" {
        return Some(crate::studio_relay::status_reply(app));
    }
    if path == "/studio/tracks" {
        let _ = std::fs::create_dir(&app.tracks);
        return Some(Reply::Json(
            Json::Obj(vec![
                ("tracks".into(), Json::Arr(st::track_infos(&app.tracks))),
                (
                    "scenes".into(),
                    Json::Arr(scene_ids(&app.scenes).into_iter().map(Json::Str).collect()),
                ),
            ]),
            200,
        ));
    }
    None
}

/// What the decoders produce: a waveform, a stems report.
fn analysis(app: &App, req: &Request, path: &str) -> Option<Reply> {
    if let Some(rest) = path.strip_prefix("/studio/waveform/") {
        let sens = sm::parse_sensitivity(&req.query());
        let name = last_segment(rest);
        let Some(p) = st::track_path(&app.tracks, &name) else {
            return Some(jerr("no such track", 404));
        };
        return Some(match sm::waveform(&p, sens) {
            Some(obj) => Reply::JsonShared(obj, 200),
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
        });
    }
    if let Some(rest) = path.strip_prefix("/studio/stems/") {
        let (obj, code) = sm::stems_analysis(&app.tracks, &last_segment(rest));
        return Some(Reply::Json(obj, code));
    }
    None
}

/// The bytes on disk (or on the card): a stem, a comparison, a track, a
/// file the castle holds.
fn files(app: &App, _req: &Request, path: &str) -> Option<Reply> {
    if path.starts_with("/studio/stem/") {
        let parts: Vec<&str> = path.split('/').collect();
        let hit = (parts.len() >= 5)
            .then(|| sm::stem_file(&app.tracks, parts[parts.len() - 2], parts[parts.len() - 1]))
            .flatten();
        return Some(match hit {
            None => jerr("no such stem", 404),
            Some(p) => Reply::FileRange {
                path: p,
                ctype: "audio/mpeg".into(),
            },
        });
    }
    if path.starts_with("/studio/compare/") {
        let parts: Vec<&str> = path.split('/').collect();
        let hit = (parts.len() >= 5)
            .then(|| sm::compare_file(parts[parts.len() - 2], parts[parts.len() - 1]))
            .flatten();
        return Some(match hit {
            None => jerr("no such comparison", 404),
            Some(p) => file_reply(p),
        });
    }
    if let Some(rest) = path.strip_prefix("/studio/track/") {
        let name = last_segment(rest);
        // rpartition("."): a known audio extension is stripped, anything
        // else is part of the id it will fail to be.
        let tid = match name.rfind('.') {
            Some(at) if at > 0 && st::AUDIO_EXT.contains(&&name[at + 1..]) => &name[..at],
            _ => name.as_str(),
        };
        return Some(match st::track_path(&app.tracks, tid) {
            None => jerr("not found", 404),
            Some(p) => file_reply(p),
        });
    }
    if let Some(rest) = path.strip_prefix("/studio/card/") {
        let name = last_segment(rest);
        if name.is_empty() {
            return Some(jerr("no file name", 400));
        }
        return Some(relay(app, "GET", &format!("/sd/{name}"), b""));
    }
    None
}

/// A file on disk, its type from its extension — Range and all.
fn file_reply(p: std::path::PathBuf) -> Reply {
    let ctype = st::mime(
        p.extension()
            .and_then(std::ffi::OsStr::to_str)
            .unwrap_or(""),
    );
    Reply::FileRange {
        path: p,
        ctype: ctype.into(),
    }
}
