//! Cues and pulse streams — the timed half of `tools/scene_schema.py`.
//!
//! Split from [`crate::scene_schema`] at the repo's 500-line cap along the
//! Python's own seam (`_check_cue` / `_check_pulse` and their helpers):
//! that module is the block's shape, this one is what happens during it.
//! The shared primitives — `unit`, `color`, `zone`, `one_of`, `effect` —
//! stay there and are used from here.

use crate::scene_schema::{CUE_OPS, color, effect, ms_fields, one_of, unit, zone};
use crate::vocab;
use crate::yaml::{Yaml, format_g};

pub(crate) fn check_cue(
    i: usize,
    c: &Yaml,
    length: Option<i64>,
    zones: Option<&[String]>,
    errs: &mut Vec<String>,
) {
    let w = format!("cues[{i}]");
    if c.as_map().is_none() {
        errs.push(format!("{w}: must be a mapping"));
        return;
    }
    let t = c.get("t");
    match t.and_then(Yaml::finite_num) {
        None => errs.push(format!(
            "{w}: t must be a time in ms >= 0, got {}",
            t.map(Yaml::repr).unwrap_or_else(|| "None".into())
        )),
        Some(n) if n < 0.0 => errs.push(format!(
            "{w}: t must be a time in ms >= 0, got {}",
            t.map(Yaml::repr).unwrap_or_default()
        )),
        Some(n) => {
            if let Some(len) = length {
                if n > len as f64 {
                    errs.push(format!(
                        "{w}: t={} is past the scene's duration_ms={len}",
                        format_g(n)
                    ));
                }
            }
        }
    }
    let op = c.get("op");
    let op_name = op.and_then(Yaml::as_str).unwrap_or("");
    if !CUE_OPS.contains(&op_name) {
        errs.push(format!(
            "{w}: op must be one of {}, got {}",
            CUE_OPS.join(", "),
            op.map(Yaml::repr).unwrap_or_else(|| "None".into())
        ));
        return;
    }
    if op_name == "set" {
        match c.get("zone") {
            None => errs.push(format!("{w}: a set cue needs a zone")),
            Some(z) => zone(&format!("{w}.zone"), z, zones, errs),
        }
        match c.get("effect") {
            None => errs.push(format!("{w}: a set cue needs an effect")),
            Some(e) => effect(&format!("{w}.effect"), e, errs),
        }
        if c.has("level") {
            unit(&format!("{w}.level"), c.get("level"), errs, 0.0, 1.0);
        }
        return;
    }
    if let Some(z) = c.get("zone") {
        zone(&format!("{w}.zone"), z, zones, errs);
    }
    if let Some(t) = c.get("targets") {
        match t.as_list() {
            None => errs.push(format!("{w}.targets: must be a list of zones")),
            Some(items) => {
                for z in items {
                    zone(&format!("{w}.targets"), z, zones, errs);
                }
            }
        }
    }
    if let Some(p) = c.get("pixels") {
        one_of(
            &format!("{w}.pixels"),
            p,
            "pixels",
            &vocab::FLASH_MODES,
            errs,
        );
    }
    if c.has("intensity") {
        unit(
            &format!("{w}.intensity"),
            c.get("intensity"),
            errs,
            0.0,
            4.0,
        );
    }
    if c.has("decay") {
        unit(&format!("{w}.decay"), c.get("decay"), errs, 0.0, 1.0);
    }
    ms_fields(&w, c, &["ms", "attack"], errs);
    if let Some(col) = c.get("color") {
        color(&format!("{w}.color"), col, errs);
    }
}

/// scene_schema._pulse_zones — where a pulse lands.
fn pulse_zones(w: &str, p: &Yaml, zones: Option<&[String]>, errs: &mut Vec<String>) {
    if let Some(z) = p.get("zone") {
        zone(&format!("{w}.zone"), z, zones, errs);
    }
    for k in ["zones", "boost_targets"] {
        let Some(v) = p.get(k) else { continue };
        match v.as_list() {
            None => errs.push(format!("{w}.{k}: must be a list of zones")),
            Some(items) => {
                for z in items {
                    zone(&format!("{w}.{k}"), z, zones, errs);
                }
            }
        }
    }
}

/// scene_schema._pulse_colors. An empty `colors:` is a mistake worth a
/// message — the render would silently fall back to white.
fn pulse_colors(w: &str, p: &Yaml, errs: &mut Vec<String>) {
    for k in ["color", "color_hot"] {
        if let Some(v) = p.get(k) {
            color(&format!("{w}.{k}"), v, errs);
        }
    }
    let Some(v) = p.get("colors") else { return };
    match v.as_list() {
        None => errs.push(format!("{w}.colors: must be a non-empty list of colours")),
        Some([]) => errs.push(format!("{w}.colors: must be a non-empty list of colours")),
        Some(items) => {
            for c in items {
                color(&format!("{w}.colors"), c, errs);
            }
        }
    }
}

/// scene_schema._pulse_shape — how hard it hits and how it falls.
fn pulse_shape(w: &str, p: &Yaml, errs: &mut Vec<String>) {
    if let Some(v) = p.get("pixels") {
        one_of(
            &format!("{w}.pixels"),
            v,
            "pixels",
            &vocab::FLASH_MODES,
            errs,
        );
    }
    if p.has("intensity") {
        unit(
            &format!("{w}.intensity"),
            p.get("intensity"),
            errs,
            0.0,
            4.0,
        );
    }
    if p.has("decay") {
        unit(&format!("{w}.decay"), p.get("decay"), errs, 0.0, 1.0);
    }
    ms_fields(w, p, &["ms", "attack_ms"], errs);
}

pub(crate) fn check_pulse(i: usize, p: &Yaml, zones: Option<&[String]>, errs: &mut Vec<String>) {
    let w = format!("pulse[{i}]");
    if p.as_map().is_none() {
        errs.push(format!("{w}: must be a mapping"));
        return;
    }
    if p.get("synth")
        .and_then(Yaml::as_str)
        .is_none_or(str::is_empty)
    {
        errs.push(format!("{w}: needs a synth (the marker stream it follows)"));
    }
    pulse_zones(&w, p, zones, errs);
    pulse_shape(&w, p, errs);
    pulse_colors(&w, p, errs);
}
