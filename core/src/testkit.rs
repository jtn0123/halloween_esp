//! The crate's test kit: fixtures the audio paths need, and the probe the
//! cached paths note themselves into.
//!
//! Compiled only under `cargo test` — nothing ships it. It exists because
//! the Python suite this crate is measured against counts work with
//! `mock.patch(ana.load_audio)`, and there is no mock here: a Rust test
//! that wants to say "the second answer did NOT decode again" needs the
//! decode itself to leave a mark. `note`/`count` are that mark, keyed by
//! path rather than a single tally because `cargo test` runs these cases in
//! parallel threads against one process-wide cache.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

fn log() -> &'static Mutex<Vec<(&'static str, String)>> {
    static L: OnceLock<Mutex<Vec<(&'static str, String)>>> = OnceLock::new();
    L.get_or_init(|| Mutex::new(Vec::new()))
}

/// Record that `what` happened to `key` — called from production code
/// behind `#[cfg(test)]`, where the Python patches a callable.
pub fn note(what: &'static str, key: &str) {
    log()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .push((what, key.to_string()));
}

/// How many times `what` happened to exactly this key: the twin of a
/// mock's `call_count`, narrowed to one test's own file.
pub fn count(what: &'static str, key: &str) -> usize {
    log()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .filter(|(w, k)| *w == what && k == key)
        .count()
}

/// Every `what` whose key contains `needle` — for the notes that are
/// sentences (a warning line) rather than paths.
pub fn matching(what: &'static str, needle: &str) -> Vec<String> {
    log()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .filter(|(w, k)| *w == what && k.contains(needle))
        .map(|(_, k)| k.clone())
        .collect()
}

/// A directory of this test's own. Named for the case, so a failure leaves
/// something readable behind and two cases never share a fixture — and for
/// the process, so a second `cargo test` running beside this one (another
/// worktree, another agent) cannot answer this one's decode counts.
pub fn tmpdir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("castle-testkit-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).expect("temp dir");
    d
}

/// 16-bit mono PCM at 44100, the container `tests/helpers.make_click_track`
/// writes with the `wave` module — ffmpeg reads it back as the f32 the
/// decode path expects, and an empty `x` writes a legal zero-frame file.
pub fn write_wav(path: &Path, x: &[f64]) {
    let data: Vec<u8> = x
        .iter()
        .flat_map(|v| (((v.clamp(-1.0, 1.0)) * 32767.0) as i16).to_le_bytes())
        .collect();
    let mut out: Vec<u8> = Vec::with_capacity(44 + data.len());
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&((36 + data.len()) as u32).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16u32.to_le_bytes()); // PCM header length
    out.extend_from_slice(&1u16.to_le_bytes()); // format: PCM
    out.extend_from_slice(&1u16.to_le_bytes()); // channels: mono
    out.extend_from_slice(&44100u32.to_le_bytes());
    out.extend_from_slice(&88200u32.to_le_bytes()); // bytes per second
    out.extend_from_slice(&2u16.to_le_bytes()); // bytes per frame
    out.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    out.extend_from_slice(b"data");
    out.extend_from_slice(&(data.len() as u32).to_le_bytes());
    out.extend_from_slice(&data);
    std::fs::write(path, out).expect("write the fixture");
}

/// A file with beats at known times, so onset detection has a right answer
/// — `tests/helpers.make_click_track` at 120 bpm: a decaying 55 Hz kick on
/// every beat and a noise hat on every off-beat. The hats' noise is an LCG
/// rather than numpy's generator: the two fixtures need the same SHAPE (a
/// broadband tick where a hat belongs), not the same samples, because
/// nothing compares these two files to each other.
pub fn click_samples(seconds: f64) -> Vec<f64> {
    const SR: f64 = 44100.0;
    const PERIOD: f64 = 0.5; // 120 bpm
    let n = (seconds * SR) as usize;
    let mut x = vec![0.0f64; n];
    let mut seed = 7u32;
    let mut t = 0.0;
    while t < seconds - 0.3 {
        let at = (t * SR) as usize;
        for i in 0..(0.18 * SR) as usize {
            if at + i >= n {
                break;
            }
            let tt = i as f64 / SR;
            x[at + i] += (2.0 * std::f64::consts::PI * 55.0 * tt).sin() * (-tt * 22.0).exp() * 0.9;
        }
        let hat = ((t + PERIOD / 2.0) * SR) as usize;
        for i in 0..(0.05 * SR) as usize {
            if hat + i >= n {
                break;
            }
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            let r = f64::from(seed >> 8) / 8388608.0 - 1.0;
            let tt = i as f64 / SR;
            x[hat + i] += r * (-tt * 90.0).exp() * 0.25;
        }
        t += PERIOD;
    }
    x
}

/// The same track, on disk — where a test needs a real file with a real
/// mtime under it rather than the samples themselves.
pub fn click_track(path: &Path, seconds: f64) {
    write_wav(path, &click_samples(seconds));
}
