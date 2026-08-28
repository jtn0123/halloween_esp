//! analyze.analyze_full for one audio file, spoken as a process — the
//! importer's ears. One JSON request on stdin, one JSON answer on stdout:
//!
//!     {"path": "tracks/x.mp3", "sensitivity": 1.1, "stereo": true}
//!  →  {"samples": 92610, "bands": {"onset_low": [[t, vel, pan], …], …}}
//!
//! tools/import_track.py spawns this instead of running analyze.py; the
//! Python stays as the parity reference and
//! tests/test_analyze_track_rust.py holds the two value-for-value.
//! `samples` is the mono decode's length — the caller derives duration
//! and the (nearly)-empty verdict from it, keeping those sentences in
//! one home. `sensitivity` is a number or the per-band map. No kernel
//! modes here: the onset path's arithmetic is pinned unconditionally,
//! so the answer is the same on every machine.

use castle_core::jsonio::{self, Json};
use castle_core::media;
use castle_core::onsets::{analyze_full3, sens3};
use std::io::Read;

fn die(msg: &str, code: i32) -> ! {
    eprintln!("{msg}");
    std::process::exit(code);
}

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        die("analyze_track: cannot read stdin", 2);
    }
    let spec = match jsonio::parse(&raw) {
        Ok(j) => j,
        Err(e) => die(&format!("analyze_track: bad request: {e}"), 2),
    };
    let path = spec.str_or("path", "");
    if path.is_empty() {
        die("analyze_track: no `path`", 2);
    }
    let sens = sens3(spec.get("sensitivity"));
    let want_stereo = matches!(spec.get("stereo"), Some(Json::Bool(true)));
    let Some(x) = media::load_audio(&path) else {
        die(&format!("analyze_track: cannot decode {path}"), 1);
    };
    let stereo_data = if want_stereo {
        media::load_stereo(&path)
    } else {
        None
    };
    let stereo = stereo_data
        .as_ref()
        .map(|(l, r)| (l.as_slice(), r.as_slice()));
    let bands = Json::Obj(
        analyze_full3(&x, sens, stereo)
            .into_iter()
            .map(|(name, rows)| {
                let arr = rows
                    .into_iter()
                    .map(|r| Json::Arr(r.into_iter().map(Json::Num).collect()))
                    .collect();
                (name, Json::Arr(arr))
            })
            .collect(),
    );
    let out = Json::Obj(vec![
        ("samples".into(), Json::Int(x.len() as i64)),
        ("bands".into(), bands),
    ]);
    println!("{}", jsonio::dumps(&out));
}
