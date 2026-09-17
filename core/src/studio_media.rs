//! Media queries the Tracks panel needs — tools/studio_media.py's waveform
//! (peaks, onsets, level envelope) plus the stems read routes, all riding
//! the crate's bit-exact decode/analysis so the JSON matches the Python's
//! byte for byte. The subprocess-driven halves (probe, compare encodes,
//! the Demucs split itself) arrive with the jobs and publish passes.
//!
//! The floor under the waveform — one decode per (file, mtime, buckets),
//! shared by every sensitivity — is `studio_wave`. This module is the
//! answer: what the knob changes, and what the desk gets back.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use crate::jsonio::{self, Json, obj_update};
use crate::onsets;
use crate::scene::round3;
use crate::studio_tracks::AUDIO_EXT;
use crate::studio_wave::{Decoded, decoded, mtime_ns};

pub const PEAKS: usize = 1000;
const SR: f64 = 44100.0;
const KEEP_WAVES: usize = 32;

/// studio_tracks.parse_sensitivity — `?sensitivity=` plus any per-band
/// `?sens_low=` overrides, as [low, mid, high] in BANDS order (a band the
/// caller did not name keeps the shared value; unparsable text keeps the
/// default, like the Python's swallowed ValueError).
pub fn parse_sensitivity(q: &[(String, String)]) -> [f64; 3] {
    let mut base = 1.1;
    if let Some((_, v)) = q.iter().find(|(k, _)| k == "sensitivity") {
        if let Ok(f) = v.parse() {
            base = f;
        }
    }
    let mut out = [base; 3];
    for (i, short) in ["low", "mid", "high"].iter().enumerate() {
        let key = format!("sens_{short}");
        if let Some((_, v)) = q.iter().find(|(k, _)| *k == key) {
            if let Ok(f) = v.parse::<f64>() {
                out[i] = f;
            }
        }
    }
    out
}

type WaveKey = (String, u128, String, usize);
/// Shared, not owned: a waveform is a thousand peaks plus every onset the
/// track has, and a hit used to deep-clone all of it WHILE HOLDING the
/// cache's mutex — the Tracks panel's hottest route, slower in Rust than
/// in Python, which hands the dict back by reference. Same shape as the
/// lean page's Arc body (grade report 2026-09-01 G2).
type WaveCache = Mutex<Vec<(WaveKey, Arc<Json>)>>;

fn wave_cache() -> &'static WaveCache {
    static C: OnceLock<WaveCache> = OnceLock::new();
    C.get_or_init(|| Mutex::new(Vec::new()))
}

/// studio_media.waveform: peak envelope plus detected onsets, cached by
/// (path, mtime, sensitivity, buckets). None = the decode failed.
pub fn waveform(path: &Path, sens: [f64; 3]) -> Option<Arc<Json>> {
    let key = (
        path.to_string_lossy().into_owned(),
        mtime_ns(path)?,
        format!("{sens:?}"),
        PEAKS,
    );
    {
        let mut c = wave_cache().lock().unwrap_or_else(|e| e.into_inner());
        if let Some(at) = c.iter().position(|(k, _)| *k == key) {
            let hit = c.remove(at);
            let out = Arc::clone(&hit.1);
            c.push(hit);
            drop(c); // before the caller does anything with the answer
            return Some(out);
        }
    }
    let dec = decoded(path, PEAKS)?;
    let id = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    let out = Arc::new(waveform_of(id, &dec, sens));
    let mut c = wave_cache().lock().unwrap_or_else(|e| e.into_inner());
    c.push((key, Arc::clone(&out)));
    while c.len() > KEEP_WAVES {
        c.remove(0);
    }
    drop(c);
    Some(out)
}

fn waveform_of(id: &str, dec: &Decoded, sens: [f64; 3]) -> Json {
    if dec.x.is_empty() {
        return Json::Obj(vec![
            ("id".into(), Json::Str(id.to_string())),
            ("duration".into(), Json::Num(0.0)),
            ("peaks".into(), Json::Arr(Vec::new())),
            ("onsets".into(), Json::obj()),
        ]);
    }
    #[cfg(test)]
    crate::testkit::note("analyze", id);
    let stereo = dec
        .stereo
        .as_ref()
        .map(|(l, r)| (l.as_slice(), r.as_slice()));
    let marks = onsets::analyze_full3(&dec.x, sens, stereo);
    let onsets_obj: Vec<(String, Json)> = marks
        .iter()
        .map(|(k, rows)| {
            let arr = rows
                .iter()
                .map(|row| {
                    let mut vals = vec![Json::Num(round3(row[0]))];
                    vals.extend(row[1..].iter().map(|v| Json::Num(*v)));
                    Json::Arr(vals)
                })
                .collect();
            (k.clone(), Json::Arr(arr))
        })
        .collect();
    Json::Obj(vec![
        ("id".into(), Json::Str(id.to_string())),
        (
            "duration".into(),
            Json::Num(round3(dec.x.len() as f64 / SR)),
        ),
        (
            "peaks".into(),
            Json::Arr(dec.peaks.iter().map(|p| Json::Num(*p)).collect()),
        ),
        ("onsets".into(), Json::Obj(onsets_obj)),
        (
            "env".into(),
            Json::Arr(
                dec.env
                    .iter()
                    .map(|(t, v)| Json::Arr(vec![Json::Num(*t), Json::Num(*v)]))
                    .collect(),
            ),
        ),
    ])
}

/// stems.track_file — name-stripped, any container.
fn stems_track_file(tracks: &Path, tid: &str) -> Option<PathBuf> {
    let tid = tid.rsplit('/').next().unwrap_or("");
    AUDIO_EXT
        .iter()
        .map(|e| tracks.join(format!("{tid}.{e}")))
        .find(|p| p.exists())
}

/// stems.fresh — do the stems on disk still describe the current audio?
fn stems_fresh(tracks: &Path, tid: &str, meta: &Json) -> bool {
    let Some(src) = stems_track_file(tracks, tid) else {
        return false;
    };
    let Ok(md) = std::fs::metadata(&src) else {
        return false;
    };
    let mtime = md
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64() as i64)
        .unwrap_or(-1);
    meta.get("src_bytes").and_then(Json::as_f64) == Some(md.len() as f64)
        && meta.get("src_mtime").and_then(Json::as_f64) == Some(mtime as f64)
}

fn fail(msg: &str) -> Json {
    Json::Obj(vec![
        ("ok".into(), Json::Bool(false)),
        ("error".into(), Json::Str(msg.to_string())),
    ])
}

/// stems.analysis — the cached nine-way analysis, served straight from
/// disk, with ok/stale stamped on.
pub fn stems_analysis(tracks: &Path, tid: &str) -> (Json, u16) {
    let tid = tid.rsplit('/').next().unwrap_or("");
    if stems_track_file(tracks, tid).is_none() {
        return (fail("no such track"), 404);
    }
    let p = tracks.join("stems").join(tid).join("analysis.json");
    if !p.exists() {
        return (fail("not split yet"), 404);
    }
    let Ok(text) = std::fs::read_to_string(&p) else {
        return (fail("stems analysis unreadable: read failed"), 404);
    };
    let Ok(Json::Obj(mut o)) = jsonio::parse(&text) else {
        return (fail("stems analysis unreadable: parse failed"), 404);
    };
    let fresh = stems_fresh(tracks, tid, &Json::Obj(o.clone()));
    obj_update(
        &mut o,
        vec![
            ("ok".into(), Json::Bool(true)),
            ("stale".into(), Json::Bool(!fresh)),
        ],
    );
    (Json::Obj(o), 200)
}

/// stems.stem_file — a stem's mp3 for serving, or None. `combined` is not
/// a file here; the original track already streams via /studio/track.
pub fn stem_file(tracks: &Path, tid: &str, layer: &str) -> Option<PathBuf> {
    if layer != "vocals" && layer != "backing" {
        return None;
    }
    let tid = tid.rsplit('/').next().unwrap_or("");
    let p = tracks.join("stems").join(tid).join(format!("{layer}.mp3"));
    p.exists().then_some(p)
}

pub(crate) type Compares = Mutex<Vec<(String, PathBuf)>>;

pub(crate) fn compares() -> &'static Compares {
    static C: OnceLock<Compares> = OnceLock::new();
    C.get_or_init(|| Mutex::new(Vec::new()))
}

/// studio_media.compare_file — the encode behind one comparison row. The
/// map is filled by POST /studio/compare (the encode pass); until then
/// every token is unknown, which is also what a restarted Python answers.
pub fn compare_file(token: &str, codec: &str) -> Option<PathBuf> {
    let c = compares().lock().unwrap_or_else(|e| e.into_inner());
    let root = c.iter().find(|(t, _)| t == token)?.1.clone();
    let p = root.join(format!("{codec}.{codec}"));
    p.exists().then_some(p)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::studio_wave::{decoded_of, seed};
    use crate::testkit;

    // Far enough apart that this fixture's onsets really do differ: at 0.4
    // the hats' shoulders pass the threshold, at 2.5 only the kicks do.
    const SOFT: [f64; 3] = [0.4, 0.4, 0.4];
    const LOUD: [f64; 3] = [2.5, 2.5, 2.5];
    const BAND: [f64; 3] = [0.9, 1.1, 1.6];

    /// A track the waveform cache already holds. The file is real, because
    /// the key is built from its mtime; the decode is put in by hand,
    /// because the crate's tests run where there is no ffmpeg — so a
    /// `decode` note appearing at all means the cache was missed, which is
    /// the assertion the Python gets from `mock.patch(ana.load_audio)`.
    fn primed(tag: &str, seconds: f64) -> PathBuf {
        let track = testkit::tmpdir(tag).join(format!("_t_{tag}.wav"));
        let x = testkit::click_samples(seconds);
        testkit::write_wav(&track, &x);
        let stereo = Some((x.clone(), x.clone()));
        seed(&track, PEAKS, decoded_of(x, stereo, PEAKS)).expect("seeded");
        track
    }

    fn field<'a>(d: &'a Json, key: &str) -> &'a Json {
        d.get(key)
            .unwrap_or_else(|| panic!("no {key} in the answer"))
    }

    fn rows(d: &Json, key: &str) -> Vec<Json> {
        match field(d, key) {
            Json::Arr(a) => a.clone(),
            other => panic!("{key} is not an array: {other:?}"),
        }
    }

    fn keys(d: &Json) -> Vec<String> {
        match d {
            Json::Obj(o) => o.iter().map(|(k, _)| k.clone()).collect(),
            other => panic!("not an object: {other:?}"),
        }
    }

    fn decodes(path: &Path) -> usize {
        testkit::count("decode", path.to_string_lossy().as_ref())
    }

    fn wave_entries(path: &Path) -> usize {
        let want = path.to_string_lossy().into_owned();
        let c = wave_cache().lock().unwrap_or_else(|e| e.into_inner());
        c.iter().filter(|((p, ..), _)| *p == want).count()
    }

    /// The waveform store is one cache for the process and its bound is
    /// global, so a test that fills it would quietly evict another test's
    /// entry mid-assertion. These cases take turns instead.
    fn cache_turn() -> std::sync::MutexGuard<'static, ()> {
        static TURN: OnceLock<Mutex<()>> = OnceLock::new();
        TURN.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }

    /// The audition nudges sensitivity a dozen times per track, and each
    /// nudge used to pay for the decode, the peaks and the envelope again
    /// — ~70% of the work — although only the onset pass depends on the
    /// knob. Three knobs, one decode, three onset passes; and asking again
    /// for a knob already answered is neither.
    #[test]
    fn a_sensitivity_change_reanalyses_but_does_not_redecode() {
        let _turn = cache_turn();
        let track = primed("sens", 2.0);
        for sens in [[1.1; 3], SOFT, BAND] {
            waveform(&track, sens).expect("a waveform");
        }
        assert_eq!(decodes(&track), 0, "the knob re-decoded");
        assert_eq!(testkit::count("analyze", "_t_sens"), 3);
        waveform(&track, SOFT).expect("a waveform");
        assert_eq!(testkit::count("analyze", "_t_sens"), 3, "a hit re-analysed");
    }

    /// What the knob does not touch must be identical between two answers
    /// — the Python asserts that by identity (`assertIs`), since its dict
    /// hands the same list back; here the two answers are built from the
    /// same `Arc<Decoded>` and the values are copied in, so equality is
    /// the assertion and the onsets are what must differ.
    #[test]
    fn the_knob_independent_parts_are_shared_between_two_answers() {
        let _turn = cache_turn();
        let track = primed("shared", 2.0);
        let a = waveform(&track, SOFT).expect("a waveform");
        let b = waveform(&track, LOUD).expect("a waveform");
        assert_eq!(field(&a, "peaks"), field(&b, "peaks"));
        assert_eq!(field(&a, "env"), field(&b, "env"));
        assert_eq!(field(&a, "duration"), field(&b, "duration"));
        assert_ne!(
            field(&a, "onsets"),
            field(&b, "onsets"),
            "the knob changed nothing"
        );
        assert_eq!(decodes(&track), 0);
    }

    /// The desk draws exactly these five fields, and each has a shape it
    /// relies on: peaks normalised so the tallest is 1.0 and one per
    /// bucket, an onset hit of [t, strength, pan] (the pan is how the
    /// audition routes a hit between the towers), and an envelope with
    /// real points in it — that is what lets a generated scene dim for a
    /// verse instead of holding one level for three minutes.
    #[test]
    fn the_answer_is_id_duration_peaks_onsets_and_env() {
        let _turn = cache_turn();
        let track = primed("shape", 2.0);
        let w = waveform(&track, [1.1; 3]).expect("a waveform");
        assert_eq!(keys(&w), ["id", "duration", "peaks", "onsets", "env"]);
        assert_eq!(field(&w, "id"), &Json::Str("_t_shape".into()));
        assert_eq!(field(&w, "duration").as_f64(), Some(2.0));
        let peaks = rows(&w, "peaks");
        assert_eq!(peaks.len(), PEAKS);
        let top = peaks.iter().filter_map(Json::as_f64).fold(0.0f64, f64::max);
        assert_eq!(top, 1.0, "the peaks are not normalised");
        let low = rows(field(&w, "onsets"), "onset_low");
        assert!(low.len() > 2, "the clicks were not heard: {}", low.len());
        match &low[0] {
            Json::Arr(hit) => assert_eq!(hit.len(), 3, "t, strength, pan"),
            other => panic!("an onset hit is not a row: {other:?}"),
        }
        assert!(rows(&w, "env").len() > 5);
    }

    /// A track with no frames still answers — four keys, no `env` at all,
    /// which is the Python's early return and what the panel checks for
    /// when it decides there is nothing to draw. Nothing is analysed, and
    /// the decode it came from holds no peaks, no envelope and no stereo:
    /// there is nothing to pan, so the second ffmpeg run never happens.
    #[test]
    fn an_empty_track_is_answered_without_an_envelope() {
        let _turn = cache_turn();
        let track = testkit::tmpdir("silent").join("_t_silent.wav");
        testkit::write_wav(&track, &[]);
        let dec = decoded_of(Vec::new(), None, PEAKS);
        assert!(dec.x.is_empty() && dec.peaks.is_empty() && dec.env.is_empty());
        assert!(dec.stereo.is_none());
        seed(&track, PEAKS, dec).expect("seeded");
        let w = waveform(&track, [1.1; 3]).expect("an empty file still answers");
        assert_eq!(keys(&w), ["id", "duration", "peaks", "onsets"]);
        assert_eq!(field(&w, "duration").as_f64(), Some(0.0));
        assert!(rows(&w, "peaks").is_empty());
        assert_eq!(keys(field(&w, "onsets")), Vec::<String>::new());
        assert_eq!(testkit::count("analyze", "_t_silent"), 0);
    }

    /// The waveform store is bounded as well: an evening spent auditioning
    /// a big library must not grow it without limit. KEEP_WAVES is a
    /// constant here where the Python patches it, so the cache is filled
    /// for real — one seeded decode under thirty-three knobs — and the
    /// oldest of them has to be gone, which shows as a fresh onset pass.
    #[test]
    fn the_waveform_cache_is_bounded() {
        let _turn = cache_turn();
        let track = primed("waves", 0.4);
        let first = [0.5; 3];
        waveform(&track, first).expect("a waveform");
        for i in 1..=KEEP_WAVES {
            waveform(&track, [0.5 + i as f64 / 100.0; 3]).expect("a waveform");
        }
        assert_eq!(wave_entries(&track), KEEP_WAVES);
        let before = testkit::count("analyze", "_t_waves");
        waveform(&track, first).expect("a waveform");
        assert_eq!(
            testkit::count("analyze", "_t_waves"),
            before + 1,
            "the oldest knob was still cached"
        );
    }
}
