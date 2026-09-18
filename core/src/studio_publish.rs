//! The publish push — tools/studio_publish.py.
//!
//! Split out of studio_scenes at the 500-line cap along the seam the
//! Python already had: this knows how to get the rebuilt show onto the
//! card, and studio_scenes knows when to ask. `tools/sd_sync.py` does the
//! pushing (its repo-glob conveniences stay Python by design); what is
//! here is the decision to push, the log, and the one thing a push cannot
//! fix by itself — scenes the RUNNING castle has not read yet.
//!
//! That last part shrank in v5.67. A scene used to be compiled in, so a
//! scene the firmware lacked needed a build and an OTA; now it is card data
//! (show.man plus `<id>.cue`, tools/gen_scene_cards.py) and the castle seeds
//! its id list from the manifest ONCE at boot — because nothing may touch
//! the card while a song is playing (docs/ISSUE-ring-flicker.md). So the
//! remaining gap is a reboot, not a flash.

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
    let stale = needs_reboot(app, &st);
    let note = if stale.is_empty() {
        String::new()
    } else {
        format!(
            "{} scene(s) the castle has not read yet — reboot it to re-read show.man",
            stale.len()
        )
    };
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(true)),
            ("pushed".into(), Json::Bool(true)),
            ("log".into(), Json::Str(tail4000(&log))),
            (
                "needs_reboot".into(),
                Json::Arr(stale.into_iter().map(Json::Str).collect()),
            ),
            ("note".into(), Json::Str(note)),
        ]),
        200,
    )
}

/// Scene ids in scenes.yaml that the castle does not know — read from the
/// `scenes` field, which since v5.67 is what the CARD's manifest said at
/// boot. The push we just made is on the card; the castle learns it at its
/// next start, so these are the ids a reboot would add. Empty too when the
/// firmware predates the field, because guessing would be worse than silence.
fn needs_reboot(app: &App, st: &Json) -> Vec<String> {
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
    fn only_scenes_the_running_castle_has_not_read_are_named() {
        let d = tmpdir("fw");
        let mut app = App::new(d.clone());
        app.scenes = d.join("scenes.yaml");
        std::fs::write(&app.scenes, SHOW).expect("seed");
        let st = |v: &str| Json::Obj(vec![("scenes".into(), Json::Str(v.into()))]);
        assert_eq!(needs_reboot(&app, &st("vigil,storm")), Vec::<String>::new());
        assert_eq!(needs_reboot(&app, &st("vigil")), vec!["storm".to_string()]);
        // A firmware that predates the field says nothing rather than
        // guessing that every scene is missing.
        assert_eq!(needs_reboot(&app, &st("")), Vec::<String>::new());
        assert_eq!(needs_reboot(&app, &Json::obj()), Vec::<String>::new());
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The push takes no gate of its own — which is what lets
    /// `studio_scenes::rebuild` drop the oplock before calling it (grade
    /// report 2026-09-17 pm G2). Everything here is either the network or a
    /// READ of the tree the generators have already finished writing; the one
    /// local file the push touches is `sd_sync`'s own per-host publish record
    /// under the build root. If a lock ever creeps back in, this deadlocks
    /// rather than merely slowing down, so it is pinned with a deadline.
    #[test]
    fn a_publish_does_not_want_the_oplock() {
        // A configured castle would be a real push from a unit test; the
        // temp root has no devices.toml, so unset means "nothing to talk to".
        if std::env::var_os("CASTLE_HOST").is_some_and(|v| !v.is_empty()) {
            return;
        }
        let d = tmpdir("nolock");
        let mut app = App::new(d.clone());
        app.scenes = d.join("scenes.yaml");
        std::fs::write(&app.scenes, SHOW).expect("seed");
        let app = std::sync::Arc::new(app);
        let held = app
            .oplock
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let pushing = std::sync::Arc::clone(&app);
        let (tx, rx) = std::sync::mpsc::channel();
        let h = std::thread::spawn(move || {
            let (_body, code) = publish_body(&pushing);
            let _ = tx.send(code);
        });
        let code = rx
            .recv_timeout(std::time::Duration::from_secs(60))
            .expect("the push ran while the gate was held");
        drop(held);
        h.join().expect("push thread");
        // No castle is configured for a temp root, so the push has nothing to
        // talk to: 502 without a byte on the wire. The point is that it got
        // that far at all.
        assert_eq!(code, 502);
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
