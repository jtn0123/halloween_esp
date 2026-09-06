//! The splice route's validation phase — "may this scene block be
//! written?", answered here since docs/RETIREMENT.md phase 2 replaced the
//! `tools/scene_check.py` shell-out with a native validator.
//!
//! Split from [`crate::studio_scenes`] at the repo's 500-line cap along
//! the seam the Python already had (`studio_scenes.check`): this module
//! decides, that one writes. The rules themselves are
//! [`crate::scene_schema`]; the parser is [`crate::yaml`]; the sentences
//! are frozen in `tests/golden/scene_errors.json`, which is the desk's UX
//! contract and not a thing to regenerate lightly.

use std::path::Path;

use crate::jsonio::Json;
use crate::scene_schema;
use crate::studio::{App, scene_ids};
use crate::vocab;
use crate::yaml::{self, Yaml, repr_str};

/// studio_scenes.zone_ids — the show's zone names, or None when the file
/// cannot say (a scenes file without a zones block, or one that does not
/// parse). Without them a scene's zone names are only checked for shape.
pub fn zone_ids(scenes: &Path) -> Option<Vec<String>> {
    let text = std::fs::read_to_string(scenes).ok()?;
    let doc = yaml::parse(&text).ok()?;
    let zones = doc.get("zones")?.as_list()?;
    if zones.is_empty() {
        return None;
    }
    zones
        .iter()
        .map(|z| z.get("id").and_then(Yaml::as_str).map(str::to_string))
        .collect()
}

fn refusal(error: String, errors: Vec<String>) -> (Json, u16) {
    (
        Json::Obj(vec![
            ("error".into(), Json::Str(error)),
            (
                "errors".into(),
                Json::Arr(errors.into_iter().map(Json::Str).collect()),
            ),
        ]),
        400,
    )
}

fn need(msg: &str) -> (Json, u16) {
    (
        Json::Obj(vec![("error".into(), Json::Str(msg.into()))]),
        400,
    )
}

/// splice()'s validation phase: the error body and code, or None when the
/// block may be spliced.
///
/// The block must PARSE, and must be the one scene it claims to be —
/// scenes.yaml is the hand-authored source of truth for the whole show,
/// and a malformed splice used to corrupt it permanently. And it must be a
/// SCENE — known effects, cues inside its length, the keys the generators
/// read (grade report 2026-08-21 B4); each problem is one line the desk can
/// show next to the field.
///
/// And it must FIT. The board holds `SCENE_LIMIT` scenes (~9 KB of dram0
/// each); a thirteenth is a scene that cannot be compiled. `make check`
/// already refuses it, but the desk is where it gets written — and
/// discovering the ceiling as a red pre-commit hook, after the splice, is
/// discovering it with the show already edited. So the ceiling is answered
/// HERE, before `write_scenes` touches anything (grade report 2026-08-31 A8).
pub(crate) fn check(app: &App, req: &Json) -> Option<(Json, u16)> {
    let block_owned = req.str_or("yaml", "");
    let block = block_owned.trim_end();
    let sid_owned = req.str_or("id", "");
    let sid = sid_owned.trim();
    if block.is_empty() || sid.is_empty() {
        return Some(need("need id and yaml"));
    }
    let parsed = match yaml::parse(block) {
        Ok(v) => v,
        Err(e) => return Some(need(&format!("scene is not valid YAML: {e}"))),
    };
    let one = match parsed.as_list() {
        Some([only]) if only.as_map().is_some() => Some(only),
        _ => None,
    };
    let Some(scene) = one.filter(|s| s.get("id").and_then(Yaml::as_str) == Some(sid)) else {
        return Some(need(&format!(
            "expected exactly one scene with id {}",
            repr_str(sid)
        )));
    };
    let zones = zone_ids(&app.scenes);
    let errors = scene_schema::validate(scene, zones.as_deref());
    if !errors.is_empty() {
        let plural = if errors.len() > 1 { "s" } else { "" };
        return Some(refusal(
            format!(
                "scene {} has {} problem{plural}: {}",
                repr_str(sid),
                errors.len(),
                errors[0]
            ),
            errors,
        ));
    }
    // Replacing a scene that is already in the show never grows it, so only
    // a NEW id is measured against the ceiling.
    let have = scene_ids(&app.scenes);
    if !have.iter().any(|s| s == sid) && have.len() >= vocab::SCENE_LIMIT {
        let n = have.len();
        let limit = vocab::SCENE_LIMIT;
        return Some(refusal(
            format!(
                "the show is full \u{2014} {n} scenes is the {limit} this board can hold \
                 (~9 KB of dram0 each), so {} cannot be added. Remove a scene first, or \
                 move the cue timelines to the card-loaded format described in \
                 scenes/scenes.yaml's header \u{2014} a thirteenth generated script is not \
                 the next step.",
                repr_str(sid)
            ),
            vec![format!("scene ceiling: {n}/{limit} scenes")],
        ));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHOW: &str = "scenes:\n  - id: vigil\n    len: 30\n  - id: storm\n    len: 40\n";

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "castle-check-{tag}-{:?}",
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).expect("temp dir");
        d
    }

    /// The refusals the desk shows, from this side alone now. The whole
    /// corpus is frozen in tests/golden/scene_errors.json and replayed
    /// against the built binary; these are the ones worth reading in the
    /// crate, because they are the shapes the route itself makes rather
    /// than the schema's sentences.
    #[test]
    fn the_splice_refusals_read_the_way_the_desk_shows_them() {
        let d = tmpdir("check");
        let mut app = App::new(d.clone());
        app.scenes = d.join("scenes.yaml");
        std::fs::write(&app.scenes, SHOW).expect("seed");
        let req = |id: &str, y: &str| {
            Json::Obj(vec![
                ("id".into(), Json::Str(id.into())),
                ("yaml".into(), Json::Str(y.into())),
            ])
        };
        let err = |r: &Json| {
            r.get("error")
                .and_then(Json::as_str)
                .unwrap_or("")
                .to_string()
        };

        let (b, code) = check(&app, &req("", "")).expect("nothing to splice");
        assert_eq!((err(&b).as_str(), code), ("need id and yaml", 400));
        let (b, _) = check(&app, &req("x", "nonsense: [")).expect("unparseable");
        assert_eq!(
            err(&b),
            "scene is not valid YAML: line 1: unterminated flow collection"
        );
        let (b, _) = check(&app, &req("x", "id: x\nname: X")).expect("a mapping, not a scene");
        assert_eq!(err(&b), "expected exactly one scene with id 'x'");
        let (b, _) = check(&app, &req("x", "  - id: y\n")).expect("the wrong id");
        assert_eq!(err(&b), "expected exactly one scene with id 'x'");
        let (b, _) = check(&app, &req("bad", "  - id: bad\n    kind: ambient\n")).expect("thin");
        assert_eq!(
            err(&b),
            "scene 'bad' has 3 problems: missing required key 'name'"
        );
        assert!(
            matches!(b.get("errors"), Some(Json::Arr(v)) if v.len() == 3),
            "three problems, each its own line"
        );
        // And the show on disk was not touched by any of the attempts.
        assert_eq!(std::fs::read_to_string(&app.scenes).expect("read"), SHOW);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The thirteenth scene is refused before anything is written, with the
    /// sentence that explains the board's dram0 ceiling and names the way
    /// forward (grade report 2026-08-31 A8).
    #[test]
    fn the_thirteenth_scene_is_refused_with_the_reason() {
        let d = tmpdir("ceiling");
        let mut app = App::new(d.clone());
        app.scenes = d.join("scenes.yaml");
        let full: String = (0..vocab::SCENE_LIMIT)
            .map(|i| format!("  - id: s{i}\n    len: 1\n"))
            .collect();
        let show = format!("scenes:\n{full}");
        std::fs::write(&app.scenes, &show).expect("seed");
        let block = "  - id: one_too_many\n    name: Trial\n    kind: ambient\n    \
                     duration_ms: 1200\n    base: {towerL: candle}\n";
        let req = Json::Obj(vec![
            ("id".into(), Json::Str("one_too_many".into())),
            ("yaml".into(), Json::Str(block.into())),
        ]);
        let (body, code) = check(&app, &req).expect("the show is full");
        assert_eq!(code, 400);
        let msg = body.get("error").and_then(Json::as_str).unwrap_or("");
        assert!(
            msg.starts_with("the show is full \u{2014} 12 scenes is the 12"),
            "{msg}"
        );
        assert!(msg.contains("card-loaded format"), "{msg}");
        assert_eq!(
            body.get("errors"),
            Some(&Json::Arr(vec![Json::Str(
                "scene ceiling: 12/12 scenes".into()
            )]))
        );
        // Replacing a scene already in the show is never measured against it.
        let keep = Json::Obj(vec![
            ("id".into(), Json::Str("s0".into())),
            (
                "yaml".into(),
                Json::Str(block.replace("one_too_many", "s0")),
            ),
        ]);
        assert_eq!(check(&app, &keep), None);
        assert_eq!(std::fs::read_to_string(&app.scenes).expect("read"), show);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// zone_ids reads the show's rig, and says nothing rather than guessing
    /// when the file cannot tell it.
    #[test]
    fn the_zone_list_comes_from_the_show_or_not_at_all() {
        let d = tmpdir("zones");
        let f = d.join("scenes.yaml");
        std::fs::write(
            &f,
            "zones:\n  - {id: towerL, channel: 1, name: \"Left\",\n     pin: 18, rgbw: true}\n  \
             - {id: door, channel: 3, pin: 16, rgbw: false}\nscenes:\n  - id: vigil\n",
        )
        .expect("seed");
        assert_eq!(
            zone_ids(&f),
            Some(vec!["towerL".to_string(), "door".to_string()])
        );
        std::fs::write(&f, SHOW).expect("no zones block");
        assert_eq!(zone_ids(&f), None);
        std::fs::write(&f, "zones: [\n").expect("broken");
        assert_eq!(zone_ids(&f), None);
        assert_eq!(zone_ids(&d.join("nope.yaml")), None);
        let _ = std::fs::remove_dir_all(&d);
    }
}
