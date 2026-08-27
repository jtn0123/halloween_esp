//! render_audio.render_scene's synth-score path, sample for sample — B3.
//!
//! One scene: seed the dice with crc32(scene id), run the score's events
//! through the twelve voices in order (they share the rng, so order IS
//! the seed), trim `take`s with a 0.4 s fade, place at each event's time,
//! then the tail (loop crossfade or end fade), the limiter, and the peak
//! normalise. Markers come back exactly as the cue generators expect:
//! truncated milliseconds and round(vel, 3) — CPython's round is decimal
//! nearest-ties-even, which is also what Rust's {:.3} formatting does.
//!
//! Reverb draws its impulse response from the same dice AFTER the score
//! (order is the seed), then convolves through the defined-order FFT —
//! since synth_master left pocketfft, wet scenes match too.
//! tests/test_master_rust.py holds whole scenes and marker dicts to the
//! Python bit for bit.

use crate::atmos::{self, Dice};
use crate::bridge::crc32;
use crate::filters::Modes;
use crate::master;
use crate::pieces;

const SR_F: f64 = 44100.0;

/// A voice's (time, velocity) beat markers, in seconds.
pub type Marks = Vec<(f64, f64)>;
/// The scene's marker lists: per synth name, (truncated ms, round3 vel).
pub type SceneMarks = Vec<(String, Vec<(i64, f64)>)>;

/// One score event, as scenes.yaml spells it.
pub struct Ev {
    pub synth: String,
    pub t: f64,
    pub gain: f64,
    pub dur: Option<f64>,
    pub take: Option<f64>,
}

fn voice(name: &str, dur: Option<f64>, d: &mut Dice, m: &Modes) -> Option<(Vec<f64>, Marks)> {
    // SYNTHS' own defaults: wind 30 s, the other dur-taking voices 20 s.
    let dur20 = dur.unwrap_or(20.0);
    Some(match name {
        "wind" => (atmos::wind(dur.unwrap_or(30.0), d, m), Vec::new()),
        "heartbeat" => atmos::heartbeat(dur20, d, m),
        "drone" => (pieces::drone(dur20), Vec::new()),
        "whispers" => atmos::whispers(dur20, d, m),
        "thunder" => (atmos::thunder(d, m), Vec::new()),
        "creak" => (atmos::creak(d, m), Vec::new()),
        "shriek" => (atmos::shriek(d, m), Vec::new()),
        "toll" => pieces::toll(),
        "organ" => pieces::organ(),
        "descent" => (pieces::descent(), Vec::new()),
        "waltz" => pieces::waltz(),
        "musicbox" => (pieces::musicbox(), Vec::new()),
        _ => return None,
    })
}

/// CPython round(v, 3): nearest 3-decimal value, ties to even — the same
/// rounding {:.3} performs, so format-and-parse IS the semantics.
pub fn round3(v: f64) -> f64 {
    format!("{v:.3}").parse().unwrap_or(v)
}

/// The mixed scene and its marker lists, sorted like the Python's.
/// `uni_fused` is the platform's uniform form; `looped` picks the tail.
pub fn render_scene(
    id: &str,
    duration_ms: f64,
    score: &[Ev],
    wet: f64,
    looped: bool,
    uni_fused: bool,
    m: &Modes,
) -> Option<(Vec<f64>, SceneMarks)> {
    let dur = duration_ms / 1000.0;
    let mut buf = vec![0.0; (dur * SR_F) as usize];
    let mut d = Dice::new(u128::from(crc32(id.as_bytes())), uni_fused);
    let mut markers: SceneMarks = Vec::new();
    for ev in score {
        let (mut sig, mut marks) = voice(&ev.synth, ev.dur, &mut d, m)?;
        if let Some(take) = ev.take {
            sig.truncate((take * SR_F) as usize);
            master::fade_tail(&mut sig, 0.4);
            marks.retain(|(t, _)| *t < take);
        }
        let scaled: Vec<f64> = sig.iter().map(|v| v * ev.gain).collect();
        pieces::place(&mut buf, &scaled, ev.t);
        let slot = match markers.iter_mut().find(|(n, _)| *n == ev.synth) {
            Some((_, v)) => v,
            None => {
                markers.push((ev.synth.clone(), Vec::new()));
                &mut markers.last_mut().unwrap().1
            }
        };
        slot.extend(
            marks
                .iter()
                .filter(|(t, _)| ev.t + t < dur - 0.1)
                .map(|(t, v)| (((ev.t + t) * 1000.0) as i64, round3(*v))),
        );
    }
    buf = atmos::apply_reverb(&buf, wet, &mut d);
    if looped {
        master::loop_crossfade(&mut buf);
    } else {
        master::end_fade(&mut buf);
    }
    buf = master::limit(&buf, 0.89);
    master::normalize(&mut buf, 0.89);
    for (_, v) in markers.iter_mut() {
        v.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
    }
    Some((buf, markers))
}

#[cfg(test)]
mod tests {
    use super::*;

    const M: Modes = Modes {
        mul_fused: true,
        poly_fused: false,
        div_fused: true,
        sqrt_form: 2,
        sos_fused: true,
    };

    #[test]
    fn a_scene_mixes_normalises_and_reports_markers() {
        let score = [
            Ev {
                synth: "toll".into(),
                t: 0.5,
                gain: 0.8,
                dur: None,
                take: None,
            },
            Ev {
                synth: "heartbeat".into(),
                t: 0.0,
                gain: 1.0,
                dur: Some(4.0),
                take: Some(2.0),
            },
        ];
        let (buf, marks) = render_scene("vigil", 5000.0, &score, 0.0, false, true, &M).unwrap();
        assert_eq!(buf.len(), 5 * 44100);
        let peak = buf.iter().fold(0.0f64, |a, v| a.max(v.abs()));
        assert!((peak - 0.89).abs() < 1e-12);
        assert_eq!(*buf.last().unwrap(), 0.0); // one-shot fades out
        let hb = &marks.iter().find(|(n, _)| n == "heartbeat").unwrap().1;
        assert!(hb.iter().all(|(ms, _)| *ms < 2000)); // take trimmed marks
        assert_eq!(
            marks.iter().find(|(n, _)| n == "toll").unwrap().1,
            [(500, 1.0)]
        );
    }

    #[test]
    fn round3_is_pythons_round() {
        // CPython-verified: 0.5555's double is 0.55549…, so it rounds DOWN
        assert_eq!(round3(0.5555), 0.555);
        assert_eq!(round3(0.0625), 0.062); // a true tie, to even
        assert_eq!(round3(1.0), 1.0);
    }
}
