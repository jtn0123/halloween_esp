//! The scenes.yaml editor and the rebuild chain — tools/studio_scenes.py.
//!
//! Validation is NOT ported: tools/scene_check.py answers with
//! studio_scenes.check()'s own strings, so the desk sees identical
//! messages whichever server runs (scene_schema stays the single
//! implementation, per the plan's stays-Python list). The splice itself —
//! the block scanner, the .bak + atomic replace — and the rebuild
//! orchestration are ported; the generators and the publish push remain
//! the same spawned venv tools, exactly as the plan intended.

use std::path::Path;
use std::process::{Command, Stdio};

use crate::jsonio::{self, Json};
use crate::studio::{scene_ids, App};
use crate::studio_routes::castle_status;

/// The interpreter the studio's children run under — the project venv
/// when it exists (which is sys.executable for the Python twin).
pub fn py(root: &Path) -> String {
    let v = root.join(".venv").join("bin").join("python");
    if v.exists() {
        v.to_string_lossy().into_owned()
    } else {
        "python3".to_string()
    }
}

/// Python's `s[-4000:]` — the last 4000 characters, not bytes.
fn tail4000(s: &str) -> String {
    let n = s.chars().count();
    if n <= 4000 {
        s.to_string()
    } else {
        s.chars().skip(n - 4000).collect()
    }
}

/// studio.run(): capture a child completely, under the 900 s ceiling that
/// keeps one hung tool from wedging every later rebuild.
pub fn run(mut cmd: Command, timeout_s: u64) -> (bool, String) {
    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return (false, e.to_string()),
    };
    let mut out_pipe = child.stdout.take().expect("piped");
    let mut err_pipe = child.stderr.take().expect("piped");
    let out_t = std::thread::spawn(move || {
        use std::io::Read;
        let mut b = Vec::new();
        let _ = out_pipe.read_to_end(&mut b);
        b
    });
    let err_t = std::thread::spawn(move || {
        use std::io::Read;
        let mut b = Vec::new();
        let _ = err_pipe.read_to_end(&mut b);
        b
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(timeout_s);
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break Some(st),
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(_) => break None,
        }
    };
    let out = out_t.join().unwrap_or_default();
    let err = err_t.join().unwrap_or_default();
    match status {
        None => (
            false,
            format!("gave up after {timeout_s}s — the job stalled"),
        ),
        Some(st) => {
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&out),
                String::from_utf8_lossy(&err)
            );
            (st.success(), tail4000(&text))
        }
    }
}

/// studio_scenes.block_pattern: one scene's block, from its `  - id: `
/// line to the next one (or EOF); a final unterminated line stays outside,
/// exactly like the regex's per-line `\n` requirement.
fn find_block(text: &str, sid: &str) -> Option<(usize, usize)> {
    let header = format!("  - id: {sid}\n");
    let bytes = text.as_bytes();
    let mut at = 0usize;
    loop {
        let rel = text[at..].find(&header)?;
        let start = at + rel;
        if start > 0 && bytes[start - 1] != b'\n' {
            at = start + 1;
            continue;
        }
        let mut end = start + header.len();
        while end < text.len() && !text[end..].starts_with("  - id: ") {
            match text[end..].find('\n') {
                Some(n) => end += n + 1,
                None => break,
            }
        }
        return Some((start, end));
    }
}

/// studio_scenes._write: keep the pre-edit text, then replace atomically —
/// a crash mid-write must never be able to truncate the show.
fn write_scenes(scenes: &Path, before: &str, raw: &str) -> std::io::Result<()> {
    std::fs::write(scenes.with_extension("yaml.bak"), before)?;
    let tmp = scenes.with_extension("yaml.tmp");
    std::fs::write(&tmp, format!("{}\n", raw.trim_end()))?;
    std::fs::rename(&tmp, scenes)
}

fn unavailable() -> (Json, u16) {
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(false)),
            (
                "error".into(),
                Json::Str("scene validation unavailable".into()),
            ),
        ]),
        500,
    )
}

/// tools/scene_check.py's verdict on a splice request; None = may splice.
fn check(app: &App, req: &Json) -> Option<(Json, u16)> {
    let payload = jsonio::dumps(&Json::Obj(vec![
        ("id".into(), req.get("id").cloned().unwrap_or(Json::Null)),
        (
            "yaml".into(),
            req.get("yaml").cloned().unwrap_or(Json::Null),
        ),
        (
            "scenes".into(),
            Json::Str(app.scenes.to_string_lossy().into_owned()),
        ),
    ]));
    let mut cmd = Command::new(py(&app.root));
    cmd.arg(app.root.join("tools").join("scene_check.py"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(_) => return Some(unavailable()),
    };
    {
        use std::io::Write;
        let Some(stdin) = child.stdin.as_mut() else {
            return Some(unavailable());
        };
        if stdin.write_all(payload.as_bytes()).is_err() {
            let _ = child.kill();
            return Some(unavailable());
        }
    }
    drop(child.stdin.take());
    let out = match child.wait_with_output() {
        Ok(o) => o,
        Err(_) => return Some(unavailable()),
    };
    let Ok(v) = jsonio::parse(String::from_utf8_lossy(&out.stdout).trim()) else {
        return Some(unavailable());
    };
    if v.get("ok").is_some() {
        return None;
    }
    let code = v.get("code").and_then(Json::as_f64).unwrap_or(400.0) as u16;
    match v.get("body").cloned() {
        Some(body) => Some((body, code)),
        None => Some(unavailable()),
    }
}

/// studio_scenes.splice — insert or replace one scene block, then rebuild.
/// Text splicing, not a YAML round-trip: the hand-authored comments carry
/// the show's reasoning and must survive.
pub fn splice(app: &App, req: &Json) -> (Json, u16) {
    if let Some(bad) = check(app, req) {
        return bad;
    }
    let block_owned = req.str_or("yaml", "");
    let block = block_owned.trim_end();
    let sid_owned = req.str_or("id", "");
    let sid = sid_owned.trim();
    let replaced;
    {
        let _g = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
        let before = std::fs::read_to_string(&app.scenes).unwrap_or_default();
        let span = find_block(&before, sid);
        replaced = span.is_some();
        let raw = match span {
            Some((s, e)) => format!("{}{}\n\n{}", &before[..s], block, &before[e..]),
            None => format!("{}\n\n{}\n", before.trim_end(), block),
        };
        if write_scenes(&app.scenes, &before, &raw).is_err() {
            return (
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    (
                        "error".into(),
                        Json::Str("could not write scenes.yaml".into()),
                    ),
                ]),
                500,
            );
        }
    }
    let (ok, log) = rebuild(app);
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(ok)),
            ("id".into(), Json::Str(sid.to_string())),
            ("replaced".into(), Json::Bool(replaced)),
            (
                "scenes".into(),
                Json::Arr(scene_ids(&app.scenes).into_iter().map(Json::Str).collect()),
            ),
            ("log".into(), Json::Str(log)),
        ]),
        if ok { 200 } else { 500 },
    )
}

/// studio_scenes.remove — take one scene out and re-render.
pub fn remove(app: &App, sid: &str) -> (Json, u16) {
    {
        let _g = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
        let before = std::fs::read_to_string(&app.scenes).unwrap_or_default();
        let Some((s, e)) = find_block(&before, sid) else {
            return (
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(true)),
                    ("id".into(), Json::Str(sid.to_string())),
                    ("removed".into(), Json::Bool(false)),
                    (
                        "scenes".into(),
                        Json::Arr(scene_ids(&app.scenes).into_iter().map(Json::Str).collect()),
                    ),
                    ("log".into(), Json::Str(String::new())),
                ]),
                200,
            );
        };
        let raw = format!("{}{}", &before[..s], &before[e..]);
        if write_scenes(&app.scenes, &before, &raw).is_err() {
            return (
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    (
                        "error".into(),
                        Json::Str("could not write scenes.yaml".into()),
                    ),
                ]),
                500,
            );
        }
    }
    let (ok, log) = rebuild(app);
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(ok)),
            ("id".into(), Json::Str(sid.to_string())),
            ("removed".into(), Json::Bool(true)),
            (
                "scenes".into(),
                Json::Arr(scene_ids(&app.scenes).into_iter().map(Json::Str).collect()),
            ),
            ("log".into(), Json::Str(log)),
        ]),
        if ok { 200 } else { 500 },
    )
}

/// studio_scenes.rebuild: audio → firmware cues → previewer → publish,
/// serialised with the encode jobs, stopping at the first failing step.
pub fn rebuild(app: &App) -> (bool, String) {
    let mut log = if app.sandboxed() {
        format!(
            "sandbox: rendered under {} — the repo's audio/, \
             firmware/generated/ and previewer are untouched\n",
            app.build_root().display()
        )
    } else {
        String::new()
    };
    let _g = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
    for tool in ["render_audio.py", "gen_esphome.py", "gen_previewer.py"] {
        let mut cmd = Command::new(py(&app.root));
        cmd.arg(app.root.join("tools").join(tool));
        let (ok, out) = run(cmd, 900);
        log.push_str(&out);
        if !ok {
            log.push_str(&format!("\n{tool} failed — the later steps were not run\n"));
            return (false, tail4000(&log));
        }
    }
    let (body, _code) = publish_body(app);
    let extra = ["log", "error"]
        .iter()
        .find_map(|k| body.get(k).and_then(Json::as_str).filter(|s| !s.is_empty()))
        .unwrap_or("")
        .to_string();
    log.push('\n');
    log.push_str(&extra);
    if let Some(n) = body.get("note").and_then(Json::as_str) {
        if !n.is_empty() {
            log.push('\n');
            log.push_str(n);
        }
    }
    (true, tail4000(&log))
}

/// studio_publish.publish, the castle-less arm — the sd_sync legs arrive
/// with the publish pass.
pub fn publish_body(app: &App) -> (Json, u16) {
    if castle_status(app).is_none() {
        return (
            Json::Obj(vec![
                ("ok".into(), Json::Bool(false)),
                ("pushed".into(), Json::Bool(false)),
                (
                    "error".into(),
                    Json::Str("no castle answered — nothing pushed".into()),
                ),
            ]),
            502,
        );
    }
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(false)),
            ("pushed".into(), Json::Bool(false)),
            (
                "error".into(),
                Json::Str("the publish legs arrive with their pass".into()),
            ),
        ]),
        500,
    )
}
