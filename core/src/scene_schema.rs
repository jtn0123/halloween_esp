//! What a scene block may say — `tools/scene_schema.py`, ported.
//!
//! One function, [`validate`]: an empty list means the scene is
//! well-formed; otherwise every problem found, each as one sentence the
//! desk shows next to the field. The studio calls it before splicing a
//! block into the show (a 400 with the list, instead of a clean write
//! followed by a failed render).
//!
//! There are two copies of these rules, and that is deliberate:
//! `tools/scene_schema.py` is what `gen_esphome.py` runs before it emits,
//! this is what the studio runs before it writes, and
//! `tests/test_scene_schema_rust.py` drives both over one corpus and
//! compares the sentences. The delegation through `tools/scene_check.py`
//! that used to make them one implementation went with the Python server
//! (docs/RETIREMENT.md phase 2); the parity gate is what replaced it.
//!
//! Deliberately NOT checked, exactly as in the Python: that `audio_file`
//! exists (render_audio does, against the configured library), and
//! anything a synth or a pulse stream interprets for itself — this is the
//! shape of the block, not the show.

use crate::scene_cues::{check_cue, check_pulse};
use crate::vocab;
use crate::yaml::{Yaml, format_g};

const REQUIRED: [&str; 5] = ["id", "name", "kind", "duration_ms", "base"];
pub(crate) const CUE_OPS: [&str; 2] = ["set", "strike"];

/// scene_schema.ID_RE — `^\w+$` with `re.ASCII`.
fn is_ident(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn joined(names: &[&str]) -> String {
    let mut v: Vec<&str> = names.to_vec();
    v.sort_unstable();
    v.join(", ")
}

/// scene_schema._unit. `lo`/`hi` arrive as the text Python's `:g` prints.
pub(crate) fn unit(w: &str, v: Option<&Yaml>, errs: &mut Vec<String>, lo: f64, hi: f64) {
    let ok = v
        .and_then(Yaml::finite_num)
        .is_some_and(|n| lo <= n && n <= hi);
    if !ok {
        let got = v.map(Yaml::repr).unwrap_or_else(|| "None".into());
        errs.push(format!(
            "{w}: must be a number from {} to {}, got {got}",
            format_g(lo),
            format_g(hi)
        ));
    }
}

/// scene_schema._color.
pub(crate) fn color(w: &str, v: &Yaml, errs: &mut Vec<String>) {
    let ok = match v.as_list() {
        Some(items) if items.len() == 3 || items.len() == 4 => items
            .iter()
            .all(|c| c.finite_num().is_some_and(|n| (0.0..=1.0).contains(&n))),
        _ => false,
    };
    if !ok {
        errs.push(format!("{w}: must be [r, g, b(, w)] with each from 0 to 1"));
    }
}

/// scene_schema._zone. `zones` is None when the show's zone list is unknown.
pub(crate) fn zone(w: &str, z: &Yaml, zones: Option<&[String]>, errs: &mut Vec<String>) {
    match z.as_str() {
        None => errs.push(format!("{w}: zone must be a name")),
        Some("") => errs.push(format!("{w}: zone must be a name")),
        Some(name) => {
            if let Some(known) = zones {
                if !known.iter().any(|k| k == name) {
                    let mut have: Vec<&str> = known.iter().map(String::as_str).collect();
                    have.sort_unstable();
                    errs.push(format!(
                        "{w}: no zone {} (have {})",
                        z.repr(),
                        have.join(", ")
                    ));
                }
            }
        }
    }
}

/// scene_schema._zone for a mapping KEY, which is text by the time it is here.
fn zone_key(w: &str, k: &str, zones: Option<&[String]>, errs: &mut Vec<String>) {
    zone(w, &Yaml::Str(k.to_string()), zones, errs);
}

/// scene_schema._effect.
pub(crate) fn effect(w: &str, e: &Yaml, errs: &mut Vec<String>) {
    if !e.as_str().is_some_and(|s| vocab::EFFECTS.contains(&s)) {
        errs.push(format!(
            "{w}: unknown effect {} (one of {})",
            e.repr(),
            joined(&vocab::EFFECTS)
        ));
    }
}

/// scene_schema._in.
pub(crate) fn one_of(w: &str, v: &Yaml, kind: &str, names: &[&str], errs: &mut Vec<String>) {
    if !v.as_str().is_some_and(|s| names.contains(&s)) {
        errs.push(format!(
            "{w}: unknown {kind} {} (one of {})",
            v.repr(),
            joined(names)
        ));
    }
}

/// `ms` / `attack` / `attack_ms`: a number of milliseconds, never negative.
pub(crate) fn ms_fields(w: &str, m: &Yaml, keys: &[&str], errs: &mut Vec<String>) {
    for k in keys {
        if let Some(v) = m.get(k) {
            if !v.finite_num().is_some_and(|n| n >= 0.0) {
                errs.push(format!(
                    "{w}.{k}: must be a number of ms >= 0, got {}",
                    v.repr()
                ));
            }
        }
    }
}

/// scene_schema._scene_head — the scalars, and the scene's length in ms
/// when it has a usable one.
fn scene_head(scene: &Yaml, errs: &mut Vec<String>) -> Option<i64> {
    for k in REQUIRED {
        if !scene.has(k) {
            errs.push(format!("missing required key '{k}'"));
        }
    }
    if let Some(sid) = scene.get("id") {
        if !sid.as_str().is_some_and(is_ident) {
            errs.push(format!(
                "id: letters, digits and _ only, got {}",
                sid.repr()
            ));
        }
    }
    for k in ["name", "kind"] {
        if let Some(v) = scene.get(k) {
            if v.as_str().is_none_or(|s| s.trim().is_empty()) {
                errs.push(format!("{k}: must be a non-empty string"));
            }
        }
    }
    if scene.has("volume") {
        unit("volume", scene.get("volume"), errs, 0.0, 1.0);
    }
    if let Some(l) = scene.get("loop") {
        if !matches!(l, Yaml::Bool(_)) {
            errs.push(format!("loop: must be true or false, got {}", l.repr()));
        }
    }
    if let Some(af) = scene.get("audio_file") {
        let ok = af.as_str().is_some_and(|s| {
            !s.is_empty() && !s.starts_with('/') && !s.split('/').any(|p| p == "..")
        });
        if !ok {
            errs.push(format!(
                "audio_file: must be a relative path, got {}",
                af.repr()
            ));
        }
    }
    let d = scene.get("duration_ms")?;
    match d.finite_num() {
        Some(n) if n > 0.0 && n.fract() == 0.0 => Some(n as i64),
        _ => {
            errs.push(format!(
                "duration_ms: must be a whole number of ms > 0, got {}",
                d.repr()
            ));
            None
        }
    }
}

/// scene_schema._mapping — `value` is a mapping, or one error saying what
/// it should have been.
fn mapping<'a>(
    name: &str,
    v: &'a Yaml,
    complaint: &str,
    errs: &mut Vec<String>,
) -> Option<&'a [(String, Yaml)]> {
    match v.as_map() {
        Some(kv) => Some(kv),
        None => {
            errs.push(format!("{name}: {complaint}"));
            None
        }
    }
}

/// scene_schema._zone_texture — one zone's entry under `zones:`.
fn zone_texture(z: &str, d: &Yaml, errs: &mut Vec<String>) {
    if mapping(&format!("zones.{z}"), d, "must be a mapping", errs).is_none() {
        return;
    }
    if let Some(c) = d.get("center") {
        effect(&format!("zones.{z}.center"), c, errs);
    }
    for k in ["overlay", "palette"] {
        if let Some(v) = d.get(k) {
            let names: &[&str] = if k == "overlay" {
                &vocab::OVERLAYS
            } else {
                &vocab::PALETTES
            };
            one_of(&format!("zones.{z}.{k}"), v, k, names, errs);
        }
    }
    if let Some(p) = d.get("phase") {
        if p.finite_num().is_none() {
            errs.push(format!("zones.{z}.phase: must be a number"));
        }
    }
}

/// scene_schema._scene_zones — base, levels and zones.
fn scene_zones(scene: &Yaml, zs: Option<&[String]>, errs: &mut Vec<String>) {
    if let Some(base) = scene.get("base") {
        if let Some(kv) = mapping("base", base, "must map each zone to an effect", errs) {
            for (z, e) in kv {
                zone_key("base", z, zs, errs);
                effect(&format!("base.{z}"), e, errs);
            }
        }
    }
    if let Some(levels) = scene.get("levels").filter(|v| **v != Yaml::Null) {
        if let Some(kv) = mapping("levels", levels, "must map zones to a level", errs) {
            for (z, v) in kv {
                zone_key("levels", z, zs, errs);
                unit(&format!("levels.{z}"), Some(v), errs, 0.0, 1.0);
            }
        }
    }
    if let Some(zd) = scene.get("zones").filter(|v| **v != Yaml::Null) {
        if let Some(kv) = mapping("zones", zd, "must map zones to their texture", errs) {
            for (z, d) in kv {
                zone_key("zones", z, zs, errs);
                zone_texture(z, d, errs);
            }
        }
    }
}

/// Every way `scene` is not a scene — empty when it is one.
///
/// `zones` is the show's zone list when the caller knows it; without it,
/// zone names are only checked for shape.
pub fn validate(scene: &Yaml, zones: Option<&[String]>) -> Vec<String> {
    if scene.as_map().is_none() {
        return vec!["scene must be a mapping".into()];
    }
    let mut errs = Vec::new();
    let length = scene_head(scene, &mut errs);
    scene_zones(scene, zones, &mut errs);
    if let Some(cues) = scene.get("cues").filter(|v| **v != Yaml::Null) {
        match cues.as_list() {
            Some(items) => {
                for (i, c) in items.iter().enumerate() {
                    check_cue(i, c, length, zones, &mut errs);
                }
            }
            None => errs.push("cues: must be a list".into()),
        }
    }
    if let Some(pulse) = scene.get("pulse").filter(|v| **v != Yaml::Null) {
        match pulse.as_list() {
            Some(items) => {
                for (i, p) in items.iter().enumerate() {
                    check_pulse(i, p, zones, &mut errs);
                }
            }
            None => errs.push("pulse: must be a list".into()),
        }
    }
    errs
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::yaml::parse;

    const ZONES: [&str; 3] = ["towerL", "towerR", "door"];

    fn errs(text: &str) -> Vec<String> {
        let zones: Vec<String> = ZONES.iter().map(|z| (*z).to_string()).collect();
        validate(&parse(text).expect("parses"), Some(&zones))
    }

    /// Every problem, in the order the fields are read — the desk shows
    /// the first in its headline and the rest beneath it. The corpus-wide
    /// gates are tests/test_scene_schema_rust.py (against the Python) and
    /// tests/golden/scene_errors.json (against the recorded answers); this
    /// is the shape of one answer, readable in the crate.
    #[test]
    fn a_scene_full_of_mistakes_names_each_one_in_reading_order() {
        assert_eq!(
            errs(
                "id: trial\nkind: seance\nvolume: 4\nduration_ms: -3\n\
                  base: {moat: disco}\ncues: [{t: 5, op: wobble}]\n"
            ),
            vec![
                "missing required key 'name'",
                "volume: must be a number from 0 to 1, got 4",
                "duration_ms: must be a whole number of ms > 0, got -3",
                "base: no zone 'moat' (have door, towerL, towerR)",
                "base.moat: unknown effect 'disco' (one of blood, candle, chill, \
                 ember, eyes, furnace, mansion, off, seance, spirit, strobe, throb, wisp)",
                "cues[0]: op must be one of set, strike, got 'wobble'",
            ]
        );
        assert_eq!(
            validate(&parse("- a\n").expect("parses"), None),
            ["scene must be a mapping"]
        );
    }

    /// A minimal scene is clean, and a cue exactly at the end is inside it.
    #[test]
    fn a_well_formed_scene_says_nothing() {
        assert_eq!(
            errs(
                "id: probe\nname: Probe\nkind: triggered\nduration_ms: 5000\n\
                  base: {towerL: candle, towerR: candle, door: ember}\n\
                  cues: [{t: 5000, op: strike, ms: 70, pixels: scatter}]\n"
            ),
            Vec::<String>::new()
        );
    }
}
