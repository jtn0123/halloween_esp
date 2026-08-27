//! ffmpeg decode — analyze.py's load_audio/load_stereo, byte-identical.
//!
//! Both languages run the SAME ffmpeg with the SAME arguments and read
//! raw f32le off its stdout, so the samples agree by construction: f32
//! widens to f64 exactly, and there is no arithmetic of ours in between.
//! What ffmpeg itself produces for a given file is deterministic per
//! build — the parity test decodes on both sides of the same machine.

use std::process::Command;

fn decode(path: &str, channels: u32, sr: u32) -> Option<Vec<u8>> {
    let out = Command::new("ffmpeg")
        .args([
            "-v",
            "quiet",
            "-i",
            path,
            "-f",
            "f32le",
            "-ac",
            &channels.to_string(),
            "-ar",
            &sr.to_string(),
            "-",
        ])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    Some(out.stdout)
}

fn floats(bytes: &[u8]) -> Vec<f64> {
    bytes
        .chunks_exact(4)
        .map(|c| f64::from(f32::from_le_bytes([c[0], c[1], c[2], c[3]])))
        .collect()
}

/// Decode anything ffmpeg understands to mono f64 at 44100.
pub fn load_audio(path: &str) -> Option<Vec<f64>> {
    Some(floats(&decode(path, 1, 44100)?))
}

/// Left and right channels; a mono file comes back as two equal arrays.
pub fn load_stereo(path: &str) -> Option<(Vec<f64>, Vec<f64>)> {
    let x = floats(&decode(path, 2, 44100)?);
    let left = x.iter().step_by(2).copied().collect();
    let right = x.iter().skip(1).step_by(2).copied().collect();
    Some((left, right))
}
