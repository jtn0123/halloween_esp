//! Link probing and codec comparisons — the second half of
//! tools/studio_media.py, split from studio_media.rs at the 500-line
//! cap. yt-dlp answers the probe; tools/compare_encodes.py (the same
//! encode_set the Python studio calls in-process) produces and scores
//! the comparison rows, so both servers' numbers and error strings have
//! one home.

use std::path::Path;

use crate::jsonio::{self, Json, dumps};
use crate::studio::App;
use crate::studio_media::compares;

/// shutil.which, for the one binary probe cares about.
fn which(name: &str) -> bool {
    std::env::var("PATH")
        .unwrap_or_default()
        .split(':')
        .any(|d| Path::new(d).join(name).is_file())
}

/// studio_media.probe — what is at this link, without downloading it.
pub fn probe(url: &str) -> (Json, bool) {
    use crate::studio_scenes::{Timed, run_split};
    let fail = |msg: String| {
        (
            Json::Obj(vec![
                ("ok".into(), Json::Bool(false)),
                ("error".into(), Json::Str(msg)),
            ]),
            false,
        )
    };
    if !which("yt-dlp") {
        return fail("yt-dlp is not installed (brew install yt-dlp)".into());
    }
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return fail("that does not look like a link".into());
    }
    let mut cmd = std::process::Command::new("yt-dlp");
    cmd.args(["--dump-json", "--no-playlist", "--no-warnings", url]);
    let (ok, out, err) = match run_split(cmd, 60) {
        Timed::Out => return fail("timed out after 60s asking about that link".into()),
        Timed::Done(ok, out, err) => (ok, out, err),
    };
    if !ok {
        // yt-dlp's own message is usually the useful one — its last line.
        let tail = err
            .lines()
            .rev()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("could not read that link");
        return fail(tail.to_string());
    }
    let first = out.lines().next().unwrap_or("");
    let Ok(d) = jsonio::parse(first) else {
        return fail("could not parse what came back".into());
    };
    let s = |k: &str| d.str_or(k, "");
    let dur = d
        .get("duration")
        .cloned()
        .filter(|v| v.as_f64().unwrap_or(0.0) != 0.0);
    let dur_secs = dur.as_ref().and_then(Json::as_f64).unwrap_or(0.0) as i64;
    let uploader = if s("uploader").is_empty() {
        s("channel")
    } else {
        s("uploader")
    };
    let live = matches!(d.get("is_live"), Some(Json::Bool(true)));
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(true)),
            ("title".into(), Json::Str(s("title"))),
            ("uploader".into(), Json::Str(uploader)),
            ("duration".into(), dur.clone().unwrap_or(Json::Int(0))),
            (
                "duration_text".into(),
                Json::Str(if dur.is_some() {
                    format!("{}:{:02}", dur_secs / 60, dur_secs % 60)
                } else {
                    "?".to_string()
                }),
            ),
            ("thumbnail".into(), Json::Str(s("thumbnail"))),
            ("is_live".into(), Json::Bool(live)),
            ("extractor".into(), Json::Str(s("extractor_key"))),
            (
                "warning".into(),
                Json::Str(if live {
                    "this is a live stream — it has no end to trim from".to_string()
                } else {
                    String::new()
                }),
            ),
        ]),
        true,
    )
}

/// float(req.get(k) or default) — the compare route's coercion.
fn num_of(req: &Json, k: &str, d: f64) -> Result<f64, String> {
    let v = req.get(k);
    let truthy = match v {
        None | Some(Json::Null) | Some(Json::Bool(false)) => false,
        Some(Json::Int(i)) => *i != 0,
        Some(Json::Num(f)) => *f != 0.0,
        Some(Json::Str(s)) => !s.is_empty(),
        _ => true,
    };
    if !truthy {
        return Ok(d);
    }
    match v {
        Some(Json::Int(i)) => Ok(*i as f64),
        Some(Json::Num(f)) => Ok(*f),
        Some(Json::Bool(true)) => Ok(1.0),
        Some(Json::Str(s)) => s
            .trim()
            .parse()
            .map_err(|_| format!("ValueError: could not convert string to float: '{s}'")),
        _ => Err("ValueError: bad number".to_string()),
    }
}

/// studio_media.compare — one clip encoded every way, by the same Python
/// that scores it for the Python studio (tools/compare_encodes.py), so
/// the rows and the error strings have one home. Rows are kept until a
/// few newer ones push them out. The caller holds the encode lock.
pub fn compare(app: &App, req: &Json) -> (Json, u16) {
    use crate::studio_scenes::py;
    let raw = req.str_or("id", "");
    let name = raw.trim().trim_end_matches('/');
    let name = name.rsplit('/').next().unwrap_or("");
    let Some(p) = crate::studio_tracks::track_path(&app.tracks, name) else {
        return (
            Json::Obj(vec![
                ("ok".into(), Json::Bool(false)),
                ("error".into(), Json::Str("no such track".into())),
            ]),
            404,
        );
    };
    let built: Result<Json, String> = (|| {
        let take_truthy = match req.get("take") {
            None | Some(Json::Null) | Some(Json::Bool(false)) => false,
            Some(Json::Int(i)) => *i != 0,
            Some(Json::Num(f)) => *f != 0.0,
            Some(Json::Str(s)) => !s.is_empty(),
            _ => true,
        };
        let take = if take_truthy {
            Some(num_of(req, "take", 0.0)?)
        } else {
            None
        };
        Ok(Json::Obj(vec![
            ("start".into(), Json::Num(num_of(req, "start", 0.0)?)),
            ("take".into(), take.map(Json::Num).unwrap_or(Json::Null)),
            ("fade_in".into(), Json::Null),
            ("fade_out".into(), Json::Null),
            ("normalize".into(), Json::Bool(false)),
            ("gain_db".into(), Json::Null),
            (
                "bitrate".into(),
                Json::Int(num_of(req, "bitrate", 96.0)? as i64),
            ),
            (
                "channels".into(),
                Json::Int(num_of(req, "channels", 1.0)? as i64),
            ),
            (
                "sample_rate".into(),
                Json::Int(num_of(req, "sample_rate", 44100.0)? as i64),
            ),
        ]))
    })();
    let opts = match built {
        Ok(v) => v,
        // A typo in a number is the caller's mistake, not a server fault:
        // the Python raises BadRequest here for the same 400, rather than
        // letting the ValueError reach its error boundary (report A5).
        Err(e) => {
            return (
                Json::Obj(vec![
                    ("ok".into(), Json::Bool(false)),
                    ("error".into(), Json::Str(e)),
                ]),
                400,
            );
        }
    };
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let token = format!(
        "{}-{}",
        p.file_stem().and_then(|s| s.to_str()).unwrap_or(""),
        secs
    );
    let dest = std::env::temp_dir().join(format!("castle-cmp-{token}"));
    let payload = dumps(&Json::Obj(vec![
        ("path".into(), Json::Str(p.to_string_lossy().into_owned())),
        ("opts".into(), opts),
        (
            "dest".into(),
            Json::Str(dest.to_string_lossy().into_owned()),
        ),
    ]));
    let Some(v) = shim(&py(&app.root), &app.root, &payload) else {
        return (
            Json::Obj(vec![
                ("ok".into(), Json::Bool(false)),
                (
                    "error".into(),
                    Json::Str("codec comparison unavailable".into()),
                ),
            ]),
            500,
        );
    };
    if !matches!(v.get("ok"), Some(Json::Bool(true))) {
        let _ = std::fs::remove_dir_all(&dest);
        return (v, 500);
    }
    {
        let mut c = compares().lock().unwrap_or_else(|e| e.into_inner());
        c.push((token.clone(), dest));
        while c.len() > 3 {
            let (_, old) = c.remove(0);
            let _ = std::fs::remove_dir_all(old);
        }
    }
    let mut rows = v.get("codecs").cloned().unwrap_or(Json::Arr(Vec::new()));
    if let Json::Arr(list) = &mut rows {
        for row in list.iter_mut() {
            if let Json::Obj(o) = row {
                let codec = o
                    .iter()
                    .find(|(k, _)| k == "codec")
                    .and_then(|(_, val)| val.as_str())
                    .unwrap_or("")
                    .to_string();
                o.push((
                    "url".into(),
                    Json::Str(format!("/api/compare/{token}/{codec}")),
                ));
            }
        }
    }
    (
        Json::Obj(vec![
            ("ok".into(), Json::Bool(true)),
            ("token".into(), Json::Str(token)),
            (
                "reference".into(),
                v.get("reference")
                    .cloned()
                    .unwrap_or(Json::Str("wav".into())),
            ),
            ("codecs".into(), rows),
        ]),
        200,
    )
}

fn shim(py: &str, root: &Path, payload: &str) -> Option<Json> {
    use std::io::Write;
    let mut cmd = std::process::Command::new(py);
    cmd.arg(root.join("tools").join("compare_encodes.py"))
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    let mut child = cmd.spawn().ok()?;
    child.stdin.as_mut()?.write_all(payload.as_bytes()).ok()?;
    drop(child.stdin.take());
    let out = child.wait_with_output().ok()?;
    // The answer is the last non-empty stdout line, whatever a child
    // tool may have narrated above it.
    let text = String::from_utf8_lossy(&out.stdout);
    let line = text.lines().rev().find(|l| !l.trim().is_empty())?;
    jsonio::parse(line.trim()).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(pairs: Vec<(&str, Json)>) -> Json {
        Json::Obj(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }

    /// `float(req.get(k) or default)` — the `or` is Python's truthiness,
    /// which is why 0 and "" take the DEFAULT rather than themselves.
    #[test]
    fn a_falsy_field_takes_the_default_the_way_pythons_or_does() {
        let d = 96.0;
        assert_eq!(num_of(&req(vec![]), "bitrate", d), Ok(d));
        for falsy in [
            Json::Null,
            Json::Bool(false),
            Json::Int(0),
            Json::Num(0.0),
            Json::Str(String::new()),
        ] {
            assert_eq!(num_of(&req(vec![("bitrate", falsy)]), "bitrate", d), Ok(d));
        }
    }

    #[test]
    fn a_number_arrives_as_itself_however_it_was_spelt() {
        let d = 0.0;
        assert_eq!(
            num_of(&req(vec![("start", Json::Int(12))]), "start", d),
            Ok(12.0)
        );
        assert_eq!(
            num_of(&req(vec![("start", Json::Num(1.5))]), "start", d),
            Ok(1.5)
        );
        assert_eq!(
            num_of(&req(vec![("start", Json::Bool(true))]), "start", d),
            Ok(1.0)
        );
        // The desk sends form fields as strings; Python's float() takes
        // the surrounding whitespace with them.
        let s = |v: &str| req(vec![("start", Json::Str(v.into()))]);
        assert_eq!(num_of(&s("2.5"), "start", d), Ok(2.5));
        assert_eq!(num_of(&s("  7 "), "start", d), Ok(7.0));
        assert_eq!(num_of(&s("-3"), "start", d), Ok(-3.0));
    }

    /// A typo in a number is the caller's 400, in the Python's words —
    /// not a 500, and not a silent fall-back to the default.
    #[test]
    fn a_typed_number_that_is_not_one_carries_the_pythons_words() {
        let bad = req(vec![("start", Json::Str("1o".into()))]);
        assert_eq!(
            num_of(&bad, "start", 0.0),
            Err("ValueError: could not convert string to float: '1o'".to_string())
        );
        let arr = req(vec![("start", Json::Arr(vec![Json::Int(1)]))]);
        assert_eq!(
            num_of(&arr, "start", 0.0),
            Err("ValueError: bad number".to_string())
        );
    }
}
