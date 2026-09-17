//! The publish push — tools/studio_publish.py.
//!
//! Split out of studio_scenes at the 500-line cap along the seam the
//! Python already had: this knows how to get the rebuilt show onto the
//! card, and studio_scenes knows when to ask. `tools/sd_sync.py` does the
//! pushing (its repo-glob conveniences stay Python by design); what is
//! here is the decision to push, the log, and the one thing a push cannot
//! fix — scenes the RUNNING firmware was not built with.

use crate::jsonio::Json;
use crate::studio::{App, scene_ids};
use crate::studio_proc::{py, run, tail4000};
use crate::studio_relay;
use std::process::Command;

/// studio_publish.publish — push scene tracks and the lean page to the
/// castle, and report the scenes the running firmware does not know.
pub fn publish_body(app: &App) -> (Json, u16) {
    let Some(st) = studio_relay::status(app) else {
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
    };
    let host = st
        .get("bridged")
        .and_then(Json::as_str)
        .map(str::to_string)
        .or_else(|| studio_relay::castle_host(app))
        .unwrap_or_default();
    let mut log = String::new();
    for cmd in ["scenes", "site"] {
        let mut c = Command::new(py(&app.root));
        c.arg(app.root.join("tools").join("sd_sync.py"))
            .arg(&host)
            .arg(cmd);
        let (ok, out) = run(c, 900);
        log.push_str(&out);
        if !ok {
            return (
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    ("pushed".into(), Json::Bool(false)),
                    ("log".into(), Json::Str(tail4000(&log))),
                    ("error".into(), Json::Str(format!("sd_sync {cmd} failed"))),
                ]),
                500,
            );
        }
    }
    let stale = needs_firmware(app, &st);
    let note = if stale.is_empty() {
        String::new()
    } else {
        format!(
            "{} scene(s) missing from the running firmware — make sd-build, stop audio, then OTA",
            stale.len()
        )
    };
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(true)),
            ("pushed".into(), Json::Bool(true)),
            ("log".into(), Json::Str(tail4000(&log))),
            (
                "needs_firmware".into(),
                Json::Arr(stale.into_iter().map(Json::Str).collect()),
            ),
            ("note".into(), Json::Str(note)),
        ]),
        200,
    )
}

/// studio_publish.needs_firmware — scene ids in scenes.yaml that the
/// castle's firmware does not know; empty too when the firmware predates
/// the `scenes` field, because guessing would be worse than silence.
fn needs_firmware(app: &App, st: &Json) -> Vec<String> {
    let fw: Vec<String> = st
        .get("scenes")
        .and_then(Json::as_str)
        .unwrap_or("")
        .split(',')
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect();
    if fw.is_empty() {
        return Vec::new();
    }
    scene_ids(&app.scenes)
        .into_iter()
        .filter(|s| !fw.contains(s))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHOW: &str = "scenes:\n  - id: vigil\n    len: 30\n  - id: storm\n    len: 40\n";

    #[test]
    fn only_scenes_the_running_firmware_lacks_are_named() {
        let d = tmpdir("fw");
        let mut app = App::new(d.clone());
        app.scenes = d.join("scenes.yaml");
        std::fs::write(&app.scenes, SHOW).expect("seed");
        let st = |v: &str| Json::Obj(vec![("scenes".into(), Json::Str(v.into()))]);
        assert_eq!(
            needs_firmware(&app, &st("vigil,storm")),
            Vec::<String>::new()
        );
        assert_eq!(
            needs_firmware(&app, &st("vigil")),
            vec!["storm".to_string()]
        );
        // A firmware that predates the field says nothing rather than
        // guessing that every scene is missing.
        assert_eq!(needs_firmware(&app, &st("")), Vec::<String>::new());
        assert_eq!(needs_firmware(&app, &Json::obj()), Vec::<String>::new());
        let _ = std::fs::remove_dir_all(&d);
    }

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "castle-publish-{tag}-{:?}",
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).expect("temp dir");
        d
    }
}
