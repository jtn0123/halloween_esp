//! The decode cache — the knob-independent floor under every waveform.
//!
//! studio_media.py's `Decoded` and `_decoded`: one ffmpeg pass per (file,
//! mtime, buckets), holding the mono samples, both stereo channels, the
//! peak picture and the loudness envelope. A sensitivity nudge re-runs only
//! the onset pass on top of this (studio_media.rs), which is ~30% of the
//! work rather than all of it.
//!
//! Split out of studio_media.rs when the cache grew the tests the Python
//! suite had for it and the pair went over the 500-line rule. The seam is
//! real: everything here is about DECODING one file once, and nothing here
//! knows what JSON the desk gets back.

use std::path::Path;
use std::sync::{Arc, Condvar, Mutex, OnceLock};

use crate::scene::round3;
use crate::{atmos, media, onsets};

/// The decode cache's budget, in FLOATS, not entries — studio_media.py's
/// KEEP_SAMPLES, the same number for the same reason. An entry is a whole
/// song in f64: the mono buffer plus both stereo channels, three buffers
/// of 8 bytes a frame, ~318 MB for five minutes. Bounding eight of THOSE
/// was bounding 2.5 GB. 50M floats × 8 bytes ≈ 400 MB (grade report 2026-08-31 B3).
/// The newest entry is never evicted, however big it is — dropping it the
/// instant it was built would re-decode for the next sensitivity nudge.
const KEEP_SAMPLES: usize = 50_000_000;

/// CPython's round(v, 4), the way round3 already is: format and parse.
fn round4(v: f64) -> f64 {
    format!("{v:.4}").parse().unwrap_or(v)
}

/// One track, decoded once — what every sensitivity shares (Decoded).
pub(crate) struct Decoded {
    pub(crate) x: Vec<f64>,
    pub(crate) stereo: Option<(Vec<f64>, Vec<f64>)>,
    pub(crate) peaks: Vec<f64>,
    pub(crate) env: Vec<(f64, f64)>,
}

/// The ffmpeg boundary, and nothing else — the only part of a decode that
/// needs a machine with media tools on it.
fn build_decoded(path: &Path, buckets: usize) -> Option<Decoded> {
    #[cfg(test)]
    crate::testkit::note("decode", path.to_string_lossy().as_ref());
    let p = path.to_str()?;
    let x = media::load_audio(p)?;
    // Nothing to pan in a track with no frames: the SECOND ffmpeg run is
    // skipped rather than decoding zero samples twice.
    let stereo = if x.is_empty() {
        None
    } else {
        media::load_stereo(p)
    };
    Some(decoded_of(x, stereo, buckets))
}

/// Everything above that boundary: the peak picture and the loudness
/// envelope, from samples already in hand. Its own function so the cache's
/// tests can hold a decode in their hands without ffmpeg — the crate's unit
/// tests run in a CI job with no media tools, and the file-to-answer path
/// is covered where ffmpeg lives, in tests/test_studio_media_rs.py.
pub(crate) fn decoded_of(
    x: Vec<f64>,
    stereo: Option<(Vec<f64>, Vec<f64>)>,
    buckets: usize,
) -> Decoded {
    if x.is_empty() {
        return Decoded {
            x,
            stereo: None,
            peaks: Vec::new(),
            env: Vec::new(),
        };
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
    let env_pts = onsets::envelope(&x, &[("onset_full", 20.0, 16000.0, 0.0)]);
    let env: Vec<(f64, f64)> = env_pts
        .iter()
        .find(|(name, _)| name == "level_full")
        .map(|(_, pts)| pts.iter().map(|(t, v)| (round3(*t), *v)).collect())
        .unwrap_or_default();
    Decoded {
        x,
        stereo,
        peaks,
        env,
    }
}

pub(crate) fn mtime_ns(path: &Path) -> Option<u128> {
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

/// Oldest-first until the resident float count is inside the budget, always
/// keeping the newest entry — studio_media._evict_decoded. Its own function
/// because the budget is the thing worth testing and 50M floats of fixture
/// is not a unit test: pass a small budget instead of a big cache.
fn evict(cache: &mut Vec<(DecKey, Arc<Decoded>)>, budget: usize) {
    let mut total: usize = cache.iter().map(|(_, d)| samples(d)).sum();
    while total > budget && cache.len() > 1 {
        let (_, gone) = cache.remove(0);
        total -= samples(&gone);
    }
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

pub(crate) fn decoded(path: &Path, buckets: usize) -> Option<Arc<Decoded>> {
    decoded_with(path, buckets, build_decoded)
}

/// Hold a decode the cache never ran — the seam the Python gets by
/// patching `ana.load_audio` on the module. Tests only; the server has
/// exactly one builder.
#[cfg(test)]
pub(crate) fn seed(path: &Path, buckets: usize, dec: Decoded) -> Option<Arc<Decoded>> {
    decoded_with(path, buckets, move |_, _| Some(dec))
}

/// The cache itself, over whatever builds an entry: find, wait, or claim
/// and build. `decoded` is this with the ffmpeg builder.
fn decoded_with<F>(path: &Path, buckets: usize, build: F) -> Option<Arc<Decoded>>
where
    F: FnOnce(&Path, usize) -> Option<Decoded>,
{
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
    let built = build(path, buckets).map(Arc::new);
    let mut st = lock.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(dec) = &built {
        st.cache.push((key, Arc::clone(dec)));
        evict(&mut st.cache, KEEP_SAMPLES);
    }
    // The entry has to be in the cache before the marker comes off, or a
    // woken waiter finds neither and decodes the same track again.
    drop(st);
    drop(marker);
    built
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::studio_media::PEAKS;
    use crate::testkit;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    fn busy_holds(key: &DecKey) -> bool {
        let (lock, _) = decoded_cache();
        let st = lock.lock().unwrap_or_else(|e| e.into_inner());
        st.busy.contains(key)
    }

    /// How many of this path's entries the cache is holding. Counted per
    /// path because every test in this binary shares the one cache.
    fn entries_for(path: &Path) -> usize {
        let want = path.to_string_lossy().into_owned();
        let (lock, _) = decoded_cache();
        let st = lock.lock().unwrap_or_else(|e| e.into_inner());
        st.cache.iter().filter(|((p, _, _), _)| *p == want).count()
    }

    /// A build that never shells out: it notes itself the way the real one
    /// does and answers with as many samples as the file has bytes, so a
    /// rewritten file really is a different decode. The Python mocks
    /// `ana.load_audio` for exactly this.
    fn stub(path: &Path, buckets: usize) -> Option<Decoded> {
        testkit::note("decode", path.to_string_lossy().as_ref());
        let n = std::fs::metadata(path).ok()?.len() as usize;
        Some(decoded_of(vec![0.5; n], None, buckets))
    }

    fn decodes(path: &Path) -> usize {
        testkit::count("decode", path.to_string_lossy().as_ref())
    }

    /// A fabricated entry of a known size — for the budget, where what
    /// matters is the float count and not what the floats are.
    fn sized(mono: usize, stereo: bool) -> Arc<Decoded> {
        Arc::new(Decoded {
            x: vec![0.0; mono],
            stereo: stereo.then(|| (vec![0.0; mono], vec![0.0; mono])),
            peaks: Vec::new(),
            env: Vec::new(),
        })
    }

    /// The bucket count is in the key, not only in the answer: two panels
    /// asking one file for different resolutions must not hand each other
    /// a peak list of the wrong length.
    #[test]
    fn the_bucket_count_is_part_of_the_decode_key() {
        let d = testkit::tmpdir("buckets");
        let track = d.join("_t_buckets.wav");
        testkit::click_track(&track, 0.5);
        let coarse = decoded_with(&track, 50, stub).expect("decoded");
        let fine = decoded_with(&track, 200, stub).expect("decoded");
        assert_eq!((coarse.peaks.len(), fine.peaks.len()), (50, 200));
        assert_eq!(decodes(&track), 2, "one decode answered both");
        assert_eq!(entries_for(&track), 2);
    }

    /// The mtime is the staleness rule — a re-import writes the same id,
    /// and without it the panel would draw the old song forever.
    #[test]
    fn a_rewritten_file_is_decoded_afresh() {
        let d = testkit::tmpdir("rewrite");
        let track = d.join("_t_rewrite.wav");
        testkit::click_track(&track, 0.5);
        let first = decoded_with(&track, PEAKS, stub).expect("decoded");
        let was = mtime_ns(&track).expect("mtime");
        // APFS timestamps are nanoseconds, but a coarser clock could land
        // the rewrite in the same tick — write until it moves (the
        // Python's os.utime, by hand).
        for _ in 0..100 {
            testkit::click_track(&track, 1.0);
            if mtime_ns(&track) != Some(was) {
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        let second = decoded_with(&track, PEAKS, stub).expect("decoded");
        assert!(
            second.x.len() > first.x.len(),
            "the cached decode came back"
        );
        assert_eq!(decodes(&track), 2);
        assert_eq!(entries_for(&track), 2);
    }

    /// Counted in floats, not entries: an entry IS a whole song in f64
    /// (mono + both stereo channels), so bounding eight of them was
    /// bounding gigabytes (grade report 2026-08-31 B3). The budget is met
    /// in SAMPLES, which is why the stereo channels are in the sum.
    #[test]
    fn the_decoded_store_is_bounded_by_total_samples() {
        let mut cache: Vec<(DecKey, Arc<Decoded>)> = (0..4)
            .map(|i| ((format!("/t/{i}.wav"), 1, PEAKS), sized(1000, true)))
            .collect();
        assert_eq!(samples(&cache[0].1), 3000, "mono plus both channels");
        evict(&mut cache, 6000);
        assert_eq!(cache.len(), 2);
        assert_eq!(cache[0].0.0, "/t/2.wav", "the oldest went first");
        // One fat entry can evict several thin ones: the budget is not a
        // count of songs.
        let mut mixed: Vec<(DecKey, Arc<Decoded>)> = vec![
            (("/t/a.wav".into(), 1, PEAKS), sized(500, false)),
            (("/t/b.wav".into(), 1, PEAKS), sized(500, false)),
            (("/t/c.wav".into(), 1, PEAKS), sized(4000, false)),
        ];
        evict(&mut mixed, 4200);
        assert_eq!(mixed.len(), 1);
        assert_eq!(mixed[0].0.0, "/t/c.wav");
    }

    /// Evicting the newest entry the instant it was built would mean
    /// decoding it again for the very next sensitivity nudge — so the
    /// budget yields rather than the cache emptying itself.
    #[test]
    fn one_track_bigger_than_the_whole_budget_still_caches() {
        let mut cache: Vec<(DecKey, Arc<Decoded>)> =
            vec![(("/t/huge.wav".into(), 1, PEAKS), sized(9_000, true))];
        evict(&mut cache, 1);
        assert_eq!(cache.len(), 1, "the last entry is never thrown away");
        // And on the real path: a second ask for a cached track is the
        // same allocation, not a second decode.
        let d = testkit::tmpdir("keep");
        let track = d.join("_t_keep.wav");
        testkit::click_track(&track, 0.5);
        let a = decoded_with(&track, PEAKS, stub).expect("decoded");
        let b = decoded_with(&track, PEAKS, stub).expect("decoded");
        assert!(Arc::ptr_eq(&a, &b), "the second ask re-decoded");
        assert_eq!(decodes(&track), 1);
    }

    /// Two panels asking for one cold track used to run two decodes and
    /// store the second over the first — double the ffmpeg and double the
    /// peak RAM for one answer. The first build is held open while the
    /// second caller arrives: the Python's slow mock, without the sleep.
    #[test]
    fn two_threads_wanting_one_cold_track_decode_it_once() {
        let d = testkit::tmpdir("threads");
        let track = d.join("_t_threads.wav");
        testkit::click_track(&track, 0.5);
        let (building_tx, building_rx) = mpsc::channel::<()>();
        let (release_tx, release_rx) = mpsc::channel::<()>();
        let first = {
            let p = track.clone();
            std::thread::spawn(move || {
                decoded_with(&p, PEAKS, |path, buckets| {
                    building_tx.send(()).expect("the test is listening");
                    release_rx.recv().expect("the test says when");
                    stub(path, buckets)
                })
                .expect("decoded")
            })
        };
        building_rx.recv().expect("the first thread is building");
        let second = {
            let p = track.clone();
            std::thread::spawn(move || {
                decoded_with(&p, PEAKS, |_, _| panic!("a second decode of one track"))
                    .expect("decoded")
            })
        };
        // The second caller cannot answer yet: it is parked on the marker,
        // and its builder would panic if it took the work itself.
        std::thread::sleep(Duration::from_millis(50));
        release_tx.send(()).expect("the first thread is waiting");
        let a = first.join().expect("joined");
        let b = second.join().expect("the waiter was wedged");
        assert!(Arc::ptr_eq(&a, &b), "two decodes, one track");
        assert_eq!(decodes(&track), 1);
        assert_eq!(entries_for(&track), 1);
    }

    /// E1: `build_decoded` panicking used to leave the key marked busy for
    /// the life of the process, and every later request for that track sat
    /// on the condvar forever. The marker's lifetime is what is under test,
    /// so the panic is raised where the decode would be, inside the guard's
    /// scope. (The panic on stderr during this test is the test working.)
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
