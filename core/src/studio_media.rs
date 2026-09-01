//! Media queries the Tracks panel needs — tools/studio_media.py's waveform
//! (peaks, onsets, level envelope) plus the stems read routes, all riding
//! the crate's bit-exact decode/analysis so the JSON matches the Python's
//! byte for byte. The subprocess-driven halves (probe, compare encodes,
//! the Demucs split itself) arrive with the jobs and publish passes.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex, OnceLock};

use crate::jsonio::{self, obj_update, Json};
use crate::scene::round3;
use crate::studio_tracks::AUDIO_EXT;
use crate::{atmos, media, onsets};

pub const PEAKS: usize = 1000;
const SR: f64 = 44100.0;
const KEEP_WAVES: usize = 32;
/// The decode cache's budget, in FLOATS, not entries — studio_media.py's
/// KEEP_SAMPLES, the same number for the same reason. An entry is a whole
/// song in f64: the mono buffer plus both stereo channels, three buffers
/// of 8 bytes a frame, ~318 MB for five minutes. Bounding eight of THOSE
/// was bounding 2.5 GB. 50M floats × 8 bytes ≈ 400 MB (grade report B3).
/// The newest entry is never evicted, however big it is — dropping it the
/// instant it was built would re-decode for the next sensitivity nudge.
const KEEP_SAMPLES: usize = 50_000_000;

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

/// The cache and the set of keys currently being decoded, under one lock:
/// a thread that finds neither an entry nor a marker does the work, and a
/// thread that finds a marker waits for that answer instead of starting a
/// second decode of the same track (the Python's _DEC_INFLIGHT).
struct DecState {
    cache: Vec<(DecKey, Arc<Decoded>)>,
    busy: Vec<DecKey>,
}

fn decoded_cache() -> &'static (Mutex<DecState>, Condvar) {
    static C: OnceLock<(Mutex<DecState>, Condvar)> = OnceLock::new();
    C.get_or_init(|| {
        (
            Mutex::new(DecState {
                cache: Vec::new(),
                busy: Vec::new(),
            }),
            Condvar::new(),
        )
    })
}

/// Every float this entry holds — mono plus, when the track was read in
/// stereo, both channels. studio_media._samples counts the same three.
fn samples(d: &Decoded) -> usize {
    d.x.len() + d.stereo.as_ref().map_or(0, |(l, r)| l.len() + r.len())
}

/// The in-flight marker, held by the thread doing the decode and given
/// back on the way out — returned, failed, or PANICKED.
///
/// The Python twin puts the same two lines in a `finally`. Here the
/// cleanup used to sit on the success path only, so a panic inside
/// `build_decoded` (which now unwinds to a clean 500 rather than aborting
/// the process) left the key marked busy forever: every later request for
/// that track waited on a condvar nobody would ever notify, one pinned
/// thread each. A guard says it once, for every way out (grade report
/// 2026-09-01 E1).
struct Busy(DecKey);

impl Busy {
    /// Claim the key — the caller has already checked nobody else holds it.
    fn claim(st: &mut DecState, key: DecKey) -> Busy {
        st.busy.push(key.clone());
        Busy(key)
    }
}

impl Drop for Busy {
    fn drop(&mut self) {
        let (lock, cv) = decoded_cache();
        let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(at) = st.busy.iter().position(|k| *k == self.0) {
            st.busy.remove(at);
        }
        drop(st);
        // A failed decode wakes the waiters with neither entry nor marker,
        // and one of them takes the work — the same shape as the Python.
        cv.notify_all();
    }
}

fn decoded(path: &Path, buckets: usize) -> Option<Arc<Decoded>> {
    let key = (
        path.to_string_lossy().into_owned(),
        mtime_ns(path)?,
        buckets,
    );
    let (lock, cv) = decoded_cache();
    let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
    let marker = loop {
        if let Some(at) = st.cache.iter().position(|(k, _)| *k == key) {
            let hit = st.cache.remove(at);
            let out = Arc::clone(&hit.1);
            st.cache.push(hit);
            return Some(out);
        }
        if !st.busy.contains(&key) {
            break Busy::claim(&mut st, key.clone());
        }
        st = cv.wait(st).unwrap_or_else(|e| e.into_inner());
    };
    drop(st);
    // The decode itself runs outside the lock — it is most of a second.
    let built = build_decoded(path, buckets).map(Arc::new);
    let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(dec) = &built {
        st.cache.push((key, Arc::clone(dec)));
        let mut total: usize = st.cache.iter().map(|(_, d)| samples(d)).sum();
        while total > KEEP_SAMPLES && st.cache.len() > 1 {
            let (_, gone) = st.cache.remove(0);
            total -= samples(&gone);
        }
    }
    // The entry has to be in the cache before the marker comes off, or a
    // woken waiter finds neither and decodes the same track again.
    drop(st);
    drop(marker);
    built
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
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    fn busy_holds(key: &DecKey) -> bool {
        let (lock, _) = decoded_cache();
        let st = lock.lock().unwrap_or_else(|e| e.into_inner());
        st.busy.contains(key)
    }

    /// E1: `build_decoded` panicking used to leave the key marked busy for
    /// the life of the process, and every later request for that track sat
    /// on the condvar forever. The decode itself is not what is under test
    /// — the marker's lifetime is — so the panic is raised where the
    /// decode would be, inside the guard's scope. (The panic message on
    /// stderr during this test is the test working.)
    #[test]
    fn a_poisoned_decode_does_not_wedge_the_next_caller() {
        let key: DecKey = ("/nowhere/_t_poison.wav".to_string(), 7, PEAKS);
        let (claimed_tx, claimed_rx) = mpsc::channel::<()>();
        let (go_tx, go_rx) = mpsc::channel::<()>();
        let k = key.clone();
        let doomed = std::thread::spawn(move || {
            let (lock, _) = decoded_cache();
            let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
            let _marker = Busy::claim(&mut st, k);
            drop(st);
            claimed_tx.send(()).expect("the waiter is listening");
            go_rx.recv().expect("the waiter says when");
            panic!("the decode blew up");
        });
        claimed_rx.recv().expect("claimed");
        assert!(busy_holds(&key), "the marker was never taken");
        go_tx.send(()).expect("the doomed thread is waiting");
        assert!(doomed.join().is_err(), "that thread was meant to panic");

        // The next caller's wait, with a deadline where the server has
        // none: before the guard this loop never ended.
        let (lock, cv) = decoded_cache();
        let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
        let deadline = Instant::now() + Duration::from_secs(5);
        while st.busy.contains(&key) {
            assert!(Instant::now() < deadline, "the marker outlived the panic");
            let (next, _) = cv
                .wait_timeout(st, Duration::from_millis(50))
                .unwrap_or_else(|e| e.into_inner());
            st = next;
        }
    }

    /// And the guard gives the key back on the ordinary path too — a
    /// decode that simply failed (no such file) leaves nothing behind.
    #[test]
    fn a_failed_decode_clears_its_marker() {
        let path = Path::new("/nowhere/_t_missing.wav");
        assert!(decoded(path, PEAKS).is_none()); // no mtime: never claimed
        let key: DecKey = ("/nowhere/_t_failed.wav".to_string(), 9, PEAKS);
        {
            let (lock, _) = decoded_cache();
            let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
            let _marker = Busy::claim(&mut st, key.clone());
            drop(st);
            assert!(busy_holds(&key));
        }
        assert!(!busy_holds(&key));
    }
}
