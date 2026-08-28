//! render_audio.render_scene, spoken as a process — the B3 swap's last
//! mile. One JSON scene spec on stdin, the mixed WAV written where `out`
//! says, the beat markers as one JSON object on stdout.
//! tools/render_audio.py spawns this: the crate IS the renderer now, and
//! the Python body remains only as the parity reference
//! (tests/test_scene_render_rust.py holds the two byte-equal).
//!
//! Spec (the caller applies every scenes.yaml default first):
//!
//! ```json
//! {"id": "vigil", "duration_ms": 2000, "sample_rate": 44100,
//!  "wet": 0.42, "loop": true, "out": "/path/01_vigil.wav",
//!  "score": [{"synth": "toll", "t": 0.5, "gain": 0.8,
//!             "dur": 4.0, "take": 2.0}],
//!  "track": {"path": "…", "gain": 1.0, "at": 0.0, "sensitivity": 1.1},
//!  "modes": "10121", "umode": "fma"}
//! ```
//!
//! `modes`/`umode` default to the CANONICAL render profile so production
//! renders are machine-independent; the parity tests probe the host's
//! numpy and override them so the Python comparison is exact everywhere.
//! `sensitivity` is a number or the clip editor's per-band map.

use castle_core::filters::Modes;
use castle_core::jsonio::{self, Json};
use castle_core::master;
use castle_core::onsets::sens3;
use castle_core::scene::{render_scene_full, Ev, RenderErr, Track};
use std::io::Read;

fn die(msg: &str, code: i32) -> ! {
    eprintln!("{msg}");
    std::process::exit(code);
}

fn events(spec: &Json) -> Result<Vec<Ev>, String> {
    let Some(Json::Arr(rows)) = spec.get("score") else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for row in rows {
        let synth = row
            .get("synth")
            .and_then(|j| j.as_str())
            .ok_or("score event without a synth")?
            .to_string();
        let t = row
            .get("t")
            .and_then(|j| j.as_f64())
            .ok_or("score event without a time")?;
        out.push(Ev {
            synth,
            t,
            gain: row.get("gain").and_then(|j| j.as_f64()).unwrap_or(1.0),
            dur: row.get("dur").and_then(|j| j.as_f64()),
            take: row.get("take").and_then(|j| j.as_f64()),
        });
    }
    Ok(out)
}

/// The wave module's exact 44-byte header (mono, 16-bit PCM) + frames.
fn write_wav(path: &str, pcm: &[i16], sr: u32) -> std::io::Result<()> {
    let n = pcm.len() * 2;
    let mut b = Vec::with_capacity(44 + n);
    b.extend_from_slice(b"RIFF");
    b.extend_from_slice(&((36 + n) as u32).to_le_bytes());
    b.extend_from_slice(b"WAVEfmt ");
    b.extend_from_slice(&16u32.to_le_bytes());
    b.extend_from_slice(&1u16.to_le_bytes()); // PCM
    b.extend_from_slice(&1u16.to_le_bytes()); // mono
    b.extend_from_slice(&sr.to_le_bytes());
    b.extend_from_slice(&(sr * 2).to_le_bytes());
    b.extend_from_slice(&2u16.to_le_bytes());
    b.extend_from_slice(&16u16.to_le_bytes());
    b.extend_from_slice(b"data");
    b.extend_from_slice(&(n as u32).to_le_bytes());
    for v in pcm {
        b.extend_from_slice(&v.to_le_bytes());
    }
    std::fs::write(path, b)
}

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        die("scene_render: cannot read stdin", 2);
    }
    let spec = match jsonio::parse(&raw) {
        Ok(j) => j,
        Err(e) => die(&format!("scene_render: bad spec: {e}"), 2),
    };
    let id = spec.str_or("id", "");
    let dur_ms = spec
        .get("duration_ms")
        .and_then(|j| j.as_f64())
        .unwrap_or(0.0);
    let sr = spec
        .get("sample_rate")
        .and_then(|j| j.as_f64())
        .unwrap_or(44100.0);
    if sr != 44100.0 {
        die("scene_render: the crate renders at 44100 Hz only", 2);
    }
    let wet = spec.get("wet").and_then(|j| j.as_f64()).unwrap_or(0.42);
    let looped = matches!(spec.get("loop"), Some(Json::Bool(true)));
    let out = spec.str_or("out", "");
    if out.is_empty() {
        die("scene_render: no `out` path for the WAV", 2);
    }
    let m = match spec.get("modes").and_then(|j| j.as_str()) {
        Some(s) => Modes::parse(s),
        None => Modes::CANONICAL,
    };
    let uni_fused = match spec.get("umode").and_then(|j| j.as_str()) {
        Some(s) => s == "fma",
        None => Modes::CANONICAL_UNI_FUSED,
    };
    let track = spec
        .get("track")
        .filter(|t| !matches!(t, Json::Null))
        .map(|t| Track {
            path: t.str_or("path", ""),
            gain: t.get("gain").and_then(|j| j.as_f64()).unwrap_or(1.0),
            at: t.get("at").and_then(|j| j.as_f64()).unwrap_or(0.0),
            sens: sens3(t.get("sensitivity")),
        });
    let score = match events(&spec) {
        Ok(evs) => evs,
        Err(e) => die(&format!("scene_render: {e}"), 2),
    };
    let (buf, marks) = match render_scene_full(
        &id,
        dur_ms,
        track.as_ref(),
        &score,
        wet,
        looped,
        uni_fused,
        &m,
    ) {
        Ok(v) => v,
        // The Python raised SystemExit with exactly this sentence.
        Err(RenderErr::UnknownSynth(name)) => {
            die(&format!("scene {id}: unknown synth '{name}'"), 1)
        }
        Err(RenderErr::Undecodable(path)) => die(&format!("scene_render: cannot decode {path}"), 1),
    };
    if let Err(e) = write_wav(&out, &master::quantize16(&buf), 44100) {
        die(&format!("scene_render: cannot write {out}: {e}"), 1);
    }
    let obj = Json::Obj(
        marks
            .into_iter()
            .map(|(band, rows)| {
                let arr = rows
                    .into_iter()
                    .map(|(ms, fs)| {
                        let mut row = vec![Json::Int(ms)];
                        row.extend(fs.into_iter().map(Json::Num));
                        Json::Arr(row)
                    })
                    .collect();
                (band, Json::Arr(arr))
            })
            .collect(),
    );
    println!("{}", jsonio::dumps(&obj));
}
