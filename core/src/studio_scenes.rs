//! The scenes.yaml editor and the rebuild chain — tools/studio_scenes.py.
//!
//! Validation is native now (docs/RETIREMENT.md phase 2): `check()` below
//! parses the block with [`crate::yaml`], runs [`crate::scene_schema`] and
//! counts the show against [`vocab::SCENE_LIMIT`], where it used to pipe
//! the request through `tools/scene_check.py` so that two servers could
//! answer the desk in one voice. There is one server now, so the second
//! voice went with the Python one; what holds the sentences steady is
//! `tests/golden/scene_errors.json` (every refusal the desk shows, frozen)
//! and `tests/test_scene_schema_rust.py` (this validator against
//! `tools/scene_schema.py`, which gen_esphome still runs). The SCENE
//! CEILING is still answered HERE, before any splice: the thirteenth scene
//! must not be discovered as a red pre-commit hook with the show already
//! edited (grade report 2026-08-31 A8). The splice itself — the block
//! scanner, the .bak + atomic replace — and the rebuild orchestration were
//! ported earlier; the generators and the publish push remain the same
//! spawned venv tools, exactly as the plan intended.

use std::path::Path;
use std::process::Command;

use crate::jsonio::Json;
use crate::studio::{App, scene_ids};
use crate::studio_check::check;

/// Which python the children run under, and how a child is captured —
/// [`studio_proc`](crate::studio_proc), split off at the 500-line cap.
/// Re-exported so every caller still says `studio_scenes::run`.
pub use crate::studio_proc::{Timed, check_py, py, run, run_split, tail4000};

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
    let (body, _code) = crate::studio_publish::publish_body(app);
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

#[cfg(test)]
mod tests {
    use super::*;

    const SHOW: &str = "scenes:\n  - id: vigil\n    len: 30\n  - id: storm\n    len: 40\n";

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "castle-scenes-{tag}-{:?}",
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).expect("temp dir");
        d
    }

    #[test]
    fn a_block_runs_from_its_own_header_to_the_next() {
        let (s, e) = find_block(SHOW, "vigil").expect("vigil is in there");
        assert_eq!(&SHOW[s..e], "  - id: vigil\n    len: 30\n");
        let (s, e) = find_block(SHOW, "storm").expect("storm is in there");
        assert_eq!(&SHOW[s..e], "  - id: storm\n    len: 40\n");
        assert_eq!(find_block(SHOW, "crypt"), None);
        // The last block runs to EOF — but a final line with no newline
        // stays outside it, which is the regex's per-line `\n` in the
        // Python twin, not an accident of this scanner.
        let ragged = "scenes:\n  - id: only\n    len: 1";
        let (s, e) = find_block(ragged, "only").expect("found");
        assert_eq!(&ragged[s..e], "  - id: only\n");
        let ended = "scenes:\n  - id: only\n    len: 1\n";
        let (s, e) = find_block(ended, "only").expect("found");
        assert_eq!(&ended[s..e], "  - id: only\n    len: 1\n");
    }

    #[test]
    fn a_header_only_counts_at_the_start_of_a_line() {
        // The id appears inside a comment first; the block is the real one.
        let text = "scenes:\n  # not   - id: vigil\n here\n  - id: vigil\n    len: 3\n";
        let (s, e) = find_block(text, "vigil").expect("the real header");
        assert_eq!(&text[s..e], "  - id: vigil\n    len: 3\n");
        // An id that only ever appears mid-line is not a block at all.
        assert_eq!(
            find_block("scenes:\n    note: - id: ghost\n", "ghost"),
            None
        );
        // A longer id is not matched by a shorter one's header.
        assert_eq!(find_block("scenes:\n  - id: vigilante\n", "vigil"), None);
    }

    #[test]
    fn a_write_keeps_the_previous_show_beside_it() {
        let d = tmpdir("write");
        let scenes = d.join("scenes.yaml");
        std::fs::write(&scenes, SHOW).expect("seed");
        write_scenes(&scenes, SHOW, "scenes:\n  - id: crypt\n").expect("write");
        assert_eq!(
            std::fs::read_to_string(&scenes).expect("read"),
            "scenes:\n  - id: crypt\n"
        );
        assert_eq!(
            std::fs::read_to_string(d.join("scenes.yaml.bak")).expect("bak"),
            SHOW
        );
        // The .tmp is renamed, never left behind.
        assert!(!d.join("scenes.yaml.tmp").exists());
        let _ = std::fs::remove_dir_all(&d);
    }
}
