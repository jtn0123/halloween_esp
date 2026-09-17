//! What castle-core's scene validator says about a corpus of scene blocks
//! — the *_dump pattern (parity_dump, pulse_dump, synth_dump,
//! netguard_dump) applied to the rules that used to have only one
//! implementation.
//!
//! `tools/scene_schema.py` is still what `gen_esphome.py` runs before it
//! emits; `core/src/scene_schema.rs` is what the studio runs before it
//! writes. They have to agree sentence for sentence, and after the Python
//! server's retirement (docs/RETIREMENT.md) nothing compares them at run
//! time. This bin is the seam `tests/test_scene_schema_rust.py` drives.
//!
//! stdin is one JSON object; stdout is one JSON array, an entry per case —
//! `{"errors": [...]}` when the block parsed, `{"parse_error": "..."}`
//! when it did not. The YAML TEXT is the input rather than a parsed value,
//! because the parser is half of what must agree:
//!
//!     {"cases": [{"yaml": "  - id: x\n", "zones": ["towerL"]}]}
//!
//! `zones` may be absent or null, which is "the show's zone list is
//! unknown" — the same thing `scene_schema.validate(scene, None)` means.

use std::io::Read;

use castle_core::jsonio::{Json, dumps, parse};
use castle_core::scene_schema::validate;
use castle_core::yaml;

fn zones_of(case: &Json) -> Option<Vec<String>> {
    match case.get("zones") {
        Some(Json::Arr(items)) => Some(
            items
                .iter()
                .filter_map(|z| z.as_str().map(str::to_string))
                .collect(),
        ),
        _ => None,
    }
}

/// One case's verdict. The block is a whole document, exactly as the
/// splice route receives it, so a case may be a bare scene mapping, a
/// one-item list, or something that is not a scene at all.
fn verdict(case: &Json) -> Json {
    let text = case.str_or("yaml", "");
    let doc = match yaml::parse(&text) {
        Ok(v) => v,
        Err(e) => {
            return Json::Obj(vec![("parse_error".into(), Json::Str(e.to_string()))]);
        }
    };
    // A list of one is the splice route's shape; anything else is handed
    // to validate() as it stands, which is what the generators do.
    let scene = match doc.as_list() {
        Some([only]) => only,
        _ => &doc,
    };
    let zones = zones_of(case);
    Json::Obj(vec![(
        "errors".into(),
        Json::Arr(
            validate(scene, zones.as_deref())
                .into_iter()
                .map(Json::Str)
                .collect(),
        ),
    )])
}

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        eprintln!("scene_dump: could not read stdin");
        std::process::exit(1);
    }
    let doc = match parse(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("scene_dump: {e}");
            std::process::exit(1);
        }
    };
    let Some(Json::Arr(cases)) = doc.get("cases") else {
        eprintln!("scene_dump: expected {{\"cases\": [...]}}");
        std::process::exit(1);
    };
    println!("{}", dumps(&Json::Arr(cases.iter().map(verdict).collect())));
}
