//! Media queries the Tracks panel needs — tools/studio_media.py's waveform
//! (peaks, onsets, level envelope) plus the stems read routes, all riding
//! the crate's bit-exact decode/analysis so the JSON matches the Python's
//! byte for byte. The subprocess-driven halves (probe, compare encodes,
//! the Demucs split itself) arrive with the jobs and publish passes.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use crate::jsonio::{self, obj_update, Json};
use crate::scene::round3;
use crate::studio_tracks::AUDIO_EXT;
use crate::{atmos, media, onsets};

pub const PEAKS: usize = 1000;
const SR: f64 = 44100.0;
const KEEP_WAVES: usize = 32;
const KEEP_DECODED: usize = 8;

/// CPython's round(v, 4), the way round3 already is: format and parse.
fn round4(v: f64) -> f64 {
    format!("{v:.4}").parse().unwrap_or(v)
}

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

/// One track, decoded once — what every sensitivity shares (Decoded).
struct Decoded {
    x: Vec<f64>,
    stereo: Option<(Vec<f64>, Vec<f64>)>,
    peaks: Vec<f64>,
    env: Vec<(f64, f64)>,
}

fn build_decoded(path: &Path, buckets: usize) -> Option<Decoded> {
    let x = media::load_audio(path.to_str()?)?;
    if x.is_empty() {
        return Some(Decoded {
            x,
            stereo: None,
            peaks: Vec::new(),
            env: Vec::new(),
        });
    }
    let n = buckets.min(x.len());
    // np.linspace(0, len, n+1).astype(int): abs-max per bucket, normalised.
    let e = atmos::edges(x.len(), n);
    let mut peaks: Vec<f64> = e
        .windows(2)
        .map(|w| {
            if w[1] > w[0] {
                x[w[0]..w[1]].iter().fold(0.0f64, |a, v| a.max(v.abs()))
            } else {
                0.0
            }
        })
        .collect();
    let top = peaks.iter().fold(0.0f64, |a, v| a.max(*v));
    let top = if top == 0.0 { 1.0 } else { top };
    for p in &mut peaks {
        *p = round4(*p / top);
    }
    let stereo = media::load_stereo(path.to_str()?);
    let env_pts = onsets::envelope(&x, &[("onset_full", 20.0, 16000.0, 0.0)]);
    let env: Vec<(f64, f64)> = env_pts
        .iter()
        .find(|(name, _)| name == "level_full")
        .map(|(_, pts)| pts.iter().map(|(t, v)| (round3(*t), *v)).collect())
        .unwrap_or_default();
    Some(Decoded {
        x,
        stereo,
        peaks,
        env,
    })
}

fn mtime_ns(path: &Path) -> Option<u128> {
    std::fs::metadata(path)
        .ok()?
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()
        .map(|d| d.as_nanos())
}

type DecKey = (String, u128, usize);
type DecCache = Mutex<Vec<(DecKey, Arc<Decoded>)>>;

fn decoded_cache() -> &'static DecCache {
    static C: OnceLock<DecCache> = OnceLock::new();
    C.get_or_init(|| Mutex::new(Vec::new()))
}

fn decoded(path: &Path, buckets: usize) -> Option<Arc<Decoded>> {
    let key = (
        path.to_string_lossy().into_owned(),
        mtime_ns(path)?,
        buckets,
    );
    {
        let mut c = decoded_cache().lock().unwrap_or_else(|e| e.into_inner());
        if let Some(at) = c.iter().position(|(k, _)| *k == key) {
            let hit = c.remove(at);
            let out = Arc::clone(&hit.1);
            c.push(hit);
            return Some(out);
        }
    }
    let dec = Arc::new(build_decoded(path, buckets)?);
    let mut c = decoded_cache().lock().unwrap_or_else(|e| e.into_inner());
    c.push((key, Arc::clone(&dec)));
    while c.len() > KEEP_DECODED {
        c.remove(0);
    }
    Some(dec)
}

type WaveKey = (String, u128, String, usize);
type WaveCache = Mutex<Vec<(WaveKey, Json)>>;

fn wave_cache() -> &'static WaveCache {
    static C: OnceLock<WaveCache> = OnceLock::new();
    C.get_or_init(|| Mutex::new(Vec::new()))
}

/// studio_media.waveform: peak envelope plus detected onsets, cached by
/// (path, mtime, sensitivity, buckets). None = the decode failed.
pub fn waveform(path: &Path, sens: [f64; 3]) -> Option<Json> {
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
            let out = hit.1.clone();
            c.push(hit);
            return Some(out);
        }
    }
    let dec = decoded(path, PEAKS)?;
    let id = path.file_stem().and_then(|s| s.to_str()).unwrap_or("");
    let out = waveform_of(id, &dec, sens);
    let mut c = wave_cache().lock().unwrap_or_else(|e| e.into_inner());
    c.push((key, out.clone()));
    while c.len() > KEEP_WAVES {
        c.remove(0);
    }
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
