//! What a track is, on disk — tools/studio_tracks.py, field for field.
//!
//! The id is the contract everywhere else in the project; which container
//! it lives in is an import detail that stops here. track_info's answers
//! are held JSON-equal to the Python's by tests/test_studio_rust.py — the
//! analysis half (duration, per-band onset counts) rides the crate's own
//! bit-exact media/onsets port, so the numbers agree by construction.

use std::path::{Path, PathBuf};

use crate::jsonio::{Json, obj_update};
use crate::{manifest, media, onsets};

pub const AUDIO_EXT: [&str; 4] = ["mp3", "wav", "flac", "opus"];
const SR: f64 = 44100.0;
pub const SRC_DIR: &str = "_src";

pub fn mime(ext: &str) -> &'static str {
    match ext {
        "mp3" => "audio/mpeg",
        "wav" => "audio/wav",
        "flac" => "audio/flac",
        "opus" => "audio/ogg",
        _ => "application/octet-stream",
    }
}

/// studio_tracks.ID_RE — `^\w{1,64}$`, ASCII.
pub fn valid_id(tid: &str) -> bool {
    !tid.is_empty()
        && tid.len() <= 64
        && tid.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

/// Every imported track, whatever container it landed in, by id.
pub fn track_files(tracks: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    for ext in AUDIO_EXT {
        let Ok(dir) = std::fs::read_dir(tracks) else {
            continue;
        };
        let mut batch: Vec<PathBuf> = dir
            .flatten()
            .map(|e| e.path())
            .filter(|p| {
                p.is_file()
                    && p.extension().and_then(|e| e.to_str()) == Some(ext)
                    && !p
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("")
                        .starts_with('.')
            })
            .collect();
        batch.sort();
        out.extend(batch);
    }
    out.sort_by_key(|p| stem_of(p));
    out
}

fn stem_of(p: &Path) -> String {
    p.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string()
}

/// Resolve a bare track id to the file that holds it, or None.
pub fn track_path(tracks: &Path, tid: &str) -> Option<PathBuf> {
    if !valid_id(tid) {
        return None;
    }
    AUDIO_EXT
        .iter()
        .map(|e| tracks.join(format!("{tid}.{e}")))
        .find(|p| p.exists())
}

/// The kept original(s) for a track — what Delete must take with it.
pub fn source_copies(tracks: &Path, tid: &str) -> Vec<PathBuf> {
    let Ok(dir) = std::fs::read_dir(tracks.join(SRC_DIR)) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = dir
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with(&format!("{tid}.")))
        })
        .collect();
    out.sort();
    out
}

/// True for a `file:` source whose file is no longer there.
pub fn source_missing(source: &str) -> bool {
    source
        .strip_prefix("file:")
        .is_some_and(|p| !Path::new(p).exists())
}

/// track_info for a whole listing, reading tracks.json once.
pub fn track_infos(tracks: &Path) -> Vec<Json> {
    let data = manifest::load(tracks);
    track_files(tracks)
        .iter()
        .map(|p| {
            let meta = data
                .iter()
                .find(|(k, _)| *k == stem_of(p))
                .map(|(_, v)| v.clone())
                .unwrap_or_else(Json::obj);
            track_info(p, &meta, tracks)
        })
        .collect()
}

/// Everything the Tracks panel needs, including where the file came from.
pub fn track_info(p: &Path, meta: &Json, tracks: &Path) -> Json {
    let stem = stem_of(p);
    let size = std::fs::metadata(p).map(|m| m.len()).unwrap_or(0) as i64;
    let source = meta.str_or("source", "");
    let mut info: Vec<(String, Json)> = vec![
        ("id".into(), Json::Str(stem.clone())),
        (
            "ext".into(),
            Json::Str(
                p.extension()
                    .and_then(|e| e.to_str())
                    .unwrap_or("")
                    .to_string(),
            ),
        ),
        ("kb".into(), Json::Int(size / 1024)),
        ("bytes".into(), Json::Int(size)),
        ("source".into(), Json::Str(source.clone())),
        ("source_missing".into(), Json::Bool(source_missing(&source))),
        ("title".into(), Json::Str(meta.str_or("title", ""))),
        ("imported".into(), Json::Str(meta.str_or("imported", ""))),
        (
            "opts".into(),
            meta.get("opts").cloned().unwrap_or_else(Json::obj),
        ),
        ("notes".into(), Json::Str(meta.str_or("notes", ""))),
    ];
    if let Some(cached) = from_manifest(meta, size) {
        info.extend(cached);
        return Json::Obj(info);
    }
    let Some(x) = p.to_str().and_then(media::load_audio) else {
        // The Python surfaces load_audio's exception text; the shape (an
        // `error` key beside the base fields) is the contract.
        info.push((
            "error".into(),
            Json::Str(format!("could not decode {}", p.display())),
        ));
        return Json::Obj(info);
    };
    let dur = onsets::round2(x.len() as f64 / SR);
    let marks = onsets::analyze(&x, 1.1);
    info.push(("dur".into(), Json::Num(dur)));
    let counts: Vec<(String, Json)> = marks
        .iter()
        .map(|(k, v)| (k.clone(), Json::Int(v.len() as i64)))
        .collect();
    info.push(("onsets".into(), Json::Obj(counts.clone())));
    // Remember the answer beside the provenance — the next listing reads it
    // instead of decoding the library again; `bytes` is the staleness check.
    let mut audio: Vec<(String, Json)> = meta
        .get("audio")
        .and_then(Json::as_obj)
        .map(<[(String, Json)]>::to_vec)
        .unwrap_or_default();
    obj_update(
        &mut audio,
        vec![
            ("duration".into(), Json::Num(dur)),
            ("bytes".into(), Json::Int(size)),
        ],
    );
    let _ = manifest::patch(
        tracks,
        &stem,
        vec![
            ("audio".into(), Json::Obj(audio)),
            ("onsets".into(), Json::Obj(counts)),
        ],
    );
    Json::Obj(info)
}

/// The decode-free answer, if the manifest has one for THIS file.
fn from_manifest(meta: &Json, size: i64) -> Option<Vec<(String, Json)>> {
    let audio = meta.get("audio")?;
    let duration = audio.get("duration")?.as_f64()?;
    let onsets_v = meta.get("onsets")?.as_obj()?;
    if audio.get("bytes")?.as_f64()? != size as f64 {
        return None;
    }
    let counts: Vec<(String, Json)> = onsets_v
        .iter()
        .filter(|(k, _)| k.starts_with("onset_"))
        .filter_map(|(k, v)| v.as_f64().map(|f| (k.clone(), Json::Int(f as i64))))
        .collect();
    Some(vec![
        ("dur".into(), Json::Num(onsets::round2(duration))),
        ("onsets".into(), Json::Obj(counts)),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `^\w{1,64}$` with re.ASCII — the id is the contract every route
    /// builds a path out of, so what it REFUSES is the load-bearing half.
    #[test]
    fn an_id_is_ascii_word_characters_and_at_most_sixty_four() {
        for ok in ["a", "_", "vigil", "storm_2", "TRACK9", "_src9"] {
            assert!(valid_id(ok), "{ok}");
        }
        assert!(valid_id(&"a".repeat(64)));
        assert!(!valid_id(&"a".repeat(65)));
        assert!(!valid_id(""));
        for no in [
            "a b", "a-b", "a.b", "a/b", "../etc", "a\0b", "a\n", ".hidden", "a%2f",
            // re.ASCII: Python's \w stops at the ASCII word set, so an
            // accented or non-Latin name is refused rather than reaching
            // the filesystem under a different spelling.
            "café", "трек", "🎃",
        ] {
            assert!(!valid_id(no), "{no}");
        }
        // 64 is a character count in Python; a multi-byte name that is
        // refused for its characters must not be accepted for its bytes.
        assert!(!valid_id(&"é".repeat(20)));
    }

    #[test]
    fn every_container_has_a_type_and_anything_else_is_a_download() {
        assert_eq!(mime("mp3"), "audio/mpeg");
        assert_eq!(mime("wav"), "audio/wav");
        assert_eq!(mime("flac"), "audio/flac");
        assert_eq!(mime("opus"), "audio/ogg");
        for ext in AUDIO_EXT {
            assert_ne!(mime(ext), "application/octet-stream", "{ext}");
        }
        assert_eq!(mime("MP3"), "application/octet-stream"); // never folded
        assert_eq!(mime(""), "application/octet-stream");
    }

    #[test]
    fn only_a_file_source_can_go_missing() {
        assert!(source_missing("file:/nowhere/_t_gone.wav"));
        assert!(!source_missing("file:/"));
        // A link is not a path: nothing about it is checkable on disk.
        assert!(!source_missing("https://example.invalid/x.mp3"));
        assert!(!source_missing(""));
        assert!(!source_missing("/nowhere/_t_gone.wav"));
    }
}
