//! The import group — studio_routes.py's POST bodies for sync import
//! (JSON url or multipart upload), async jobs, refresh and the stems
//! split, plus studio.safe_id and failed(). The importer itself stays
//! tools/import_track.py, spawned exactly as the Python spawns it.

use std::process::Command;
use std::sync::Arc;

use crate::httpd::{parse_multipart, Reply, Request};
use crate::jsonio::Json;
use crate::studio::App;
use crate::studio_reason::reason;
use crate::studio_routes::{bad_request, json_body};
use crate::studio_scenes::{py, run};
use crate::{netguard, studio_jobs as sj, studio_tracks as st};

fn jerr(msg: &str, code: u16) -> Reply {
    Reply::Json(
        Json::Obj(vec![("error".into(), Json::Str(msg.into()))]),
        code,
    )
}

/// studio.safe_id — a track id as the importer would mint it, or None.
/// Unicode alphanumerics allowed, like str.isalnum.
pub fn safe_id(raw: &str) -> Option<String> {
    let tid = raw.trim();
    (!tid.is_empty() && tid.chars().all(|c| c.is_alphanumeric() || c == '_'))
        .then(|| tid.to_string())
}

/// studio.failed — {"ok": false, "log", "reason", **extra}.
pub fn failed(log: &str, extra: Vec<(String, Json)>) -> Vec<(String, Json)> {
    let mut body = vec![
        ("ok".into(), Json::Bool(false)),
        ("log".into(), Json::Str(log.to_string())),
        ("reason".into(), Json::Str(reason(log))),
    ];
    body.extend(extra);
    body
}

/// str(value) for a truthy JSON value, None for a falsy one — the
/// `if req.get("id")` idiom.
fn id_str(v: &Json) -> Option<String> {
    match v {
        Json::Str(s) if !s.is_empty() => Some(s.clone()),
        Json::Int(i) if *i != 0 => Some(i.to_string()),
        Json::Num(f) if *f != 0.0 => Some(crate::jsonio::py_float(*f)),
        Json::Bool(true) => Some("True".to_string()),
        _ => None,
    }
}

fn truthy(v: Option<&Json>) -> bool {
    match v {
        None | Some(Json::Null) | Some(Json::Bool(false)) => false,
        Some(Json::Int(i)) => *i != 0,
        Some(Json::Num(f)) => *f != 0.0,
        Some(Json::Str(s)) => !s.is_empty(),
        Some(Json::Arr(a)) => !a.is_empty(),
        Some(Json::Obj(o)) => !o.is_empty(),
        Some(Json::Bool(true)) => true,
    }
}

fn id_refused(opts: &Json) -> Option<Reply> {
    let v = opts.get("id")?;
    let s = id_str(v)?;
    if safe_id(&s).is_none() {
        return Some(jerr("id: letters, digits and _ only", 400));
    }
    None
}

fn cmd(args: &[String]) -> Command {
    let mut c = Command::new(&args[0]);
    c.args(&args[1..]);
    c
}

fn importer(app: &App) -> Vec<String> {
    vec![
        py(&app.root),
        app.root
            .join("tools")
            .join("import_track.py")
            .to_string_lossy()
            .into_owned(),
    ]
}

/// studio_routes.do_import — blocking import: JSON url, or multipart
/// upload staged under tracks/_upload with --keep-source.
pub fn do_import(app: &Arc<App>, req: &Request) -> Reply {
    let ctype = req.header("content-type").unwrap_or("").to_string();
    let _ = std::fs::create_dir(&app.tracks);
    let mut args = importer(app);
    let opts: Json;
    if ctype.starts_with("application/json") {
        let body = match json_body(&req.body) {
            Ok(v) => v,
            Err(e) => return bad_request(&e),
        };
        let src = body.str_or("url", "").trim().to_string();
        if src.is_empty() {
            return jerr("no url", 400);
        }
        if !src.starts_with("http://") && !src.starts_with("https://") {
            return jerr("url must be http(s)", 400);
        }
        if let Some(why) = netguard::refuse_reason(&src, &req.client_ip) {
            return jerr(&why, 400);
        }
        args.push(src);
        opts = body;
    } else {
        let (fname, data) = match parse_multipart(&req.body, &ctype) {
            Ok(v) => v,
            Err(e) => return bad_request(&e),
        };
        if data.is_empty() {
            return jerr("no file in upload", 400);
        }
        let hdr = req.header("x-import-opts").unwrap_or("{}").to_string();
        opts = match json_body(hdr.as_bytes()) {
            Ok(v) => v,
            Err(e) => return bad_request(&e),
        };
        let tmp = app.tracks.join("_upload");
        let _ = std::fs::create_dir(&tmp);
        let name = if fname.is_empty() {
            "upload.bin".to_string()
        } else {
            fname
        };
        let staged = tmp.join(&name);
        if std::fs::write(&staged, &data).is_err() {
            return Reply::Json(
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    (
                        "error".into(),
                        Json::Str("could not stage the upload".into()),
                    ),
                ]),
                500,
            );
        }
        // The staging copy is gone the moment this returns; the importer
        // keeps the original beside the library (tracks/_src/).
        args.push(staged.to_string_lossy().into_owned());
        args.push("--keep-source".to_string());
    }
    if let Some(bad) = id_refused(&opts) {
        return bad;
    }
    args.extend(sj::opt_args(&opts, &sj::OPT_KEYS));
    let (ok, out) = {
        let _g = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
        run(cmd(&args), 900)
    };
    let _ = std::fs::remove_dir_all(app.tracks.join("_upload"));
    let tracks = Json::Arr(st::track_infos(&app.tracks));
    if ok {
        Reply::Json(
            Json::Obj(vec![
                ("ok".into(), Json::Bool(true)),
                ("log".into(), Json::Str(out)),
                ("tracks".into(), tracks),
            ]),
            200,
        )
    } else {
        Reply::Json(
            Json::Obj(failed(&out, vec![("tracks".into(), tracks)])),
            500,
        )
    }
}

/// POST /studio/import/async — the download as a background job.
pub fn import_async(app: &Arc<App>, req: &Request) -> Reply {
    let body = match json_body(&req.body) {
        Ok(v) => v,
        Err(e) => return bad_request(&e),
    };
    let src = body.str_or("url", "").trim().to_string();
    if !src.starts_with("http://") && !src.starts_with("https://") {
        return jerr("url must be http(s)", 400);
    }
    if let Some(why) = netguard::refuse_reason(&src, &req.client_ip) {
        return jerr(&why, 400);
    }
    if let Some(bad) = id_refused(&body) {
        return bad;
    }
    let mut argv = importer(app);
    argv.push(src);
    argv.extend(sj::opt_args(&body, &sj::OPT_KEYS));
    Reply::Json(sj::start(app, argv), 200)
}

/// POST /studio/refresh — rebuild a track from its remembered source.
pub fn refresh(app: &Arc<App>, req: &Request) -> Reply {
    let body = match json_body(&req.body) {
        Ok(v) => v,
        Err(e) => return bad_request(&e),
    };
    let raw = body.get("id").and_then(id_str).unwrap_or_default();
    let Some(tid) = safe_id(&raw) else {
        return jerr("no id", 400);
    };
    let mut args = importer(app);
    args.push("--refresh".to_string());
    args.push(tid);
    // No id, no notes — OPT_KEYS[1:-1].
    args.extend(sj::opt_args(
        &body,
        &sj::OPT_KEYS[1..sj::OPT_KEYS.len() - 1],
    ));
    let (ok, out) = {
        let _g = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
        run(cmd(&args), 900)
    };
    let tracks = Json::Arr(st::track_infos(&app.tracks));
    if ok {
        Reply::Json(
            Json::Obj(vec![
                ("ok".into(), Json::Bool(true)),
                ("log".into(), Json::Str(out)),
                ("tracks".into(), tracks),
            ]),
            200,
        )
    } else {
        Reply::Json(
            Json::Obj(failed(&out, vec![("tracks".into(), tracks)])),
            500,
        )
    }
}

/// POST /studio/stems — the Demucs split as a background job.
pub fn stems_post(app: &Arc<App>, req: &Request) -> Reply {
    let body = match json_body(&req.body) {
        Ok(v) => v,
        Err(e) => return bad_request(&e),
    };
    let raw = body.get("id").and_then(id_str).unwrap_or_default();
    let tid = safe_id(&raw);
    let ok = tid
        .as_ref()
        .is_some_and(|t| st::track_path(&app.tracks, t).is_some());
    if !ok {
        return jerr("no such track", 400);
    }
    let mut argv = vec![
        py(&app.root),
        app.root
            .join("tools")
            .join("stems.py")
            .to_string_lossy()
            .into_owned(),
        tid.expect("checked"),
    ];
    if truthy(body.get("force")) {
        argv.push("--force".to_string());
    }
    Reply::Json(sj::start(app, argv), 200)
}

/// GET /studio/job/<id> — progress; the track list rides on the last poll.
pub fn job_get(app: &App, name: &str) -> Reply {
    match sj::get(name) {
        None => jerr("no such job", 404),
        Some(Json::Obj(mut o)) => {
            let done = matches!(
                o.iter().find(|(k, _)| k == "done"),
                Some((_, Json::Bool(true)))
            );
            if done {
                let _ = std::fs::create_dir(&app.tracks);
                o.push(("tracks".into(), Json::Arr(st::track_infos(&app.tracks))));
            }
            Reply::Json(Json::Obj(o), 200)
        }
        Some(other) => Reply::Json(other, 200),
    }
}
