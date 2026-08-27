//! Background jobs — tools/studio_jobs.py: the runner that babysits
//! yt-dlp/ffmpeg children, the progress reader, and the one-line reason()
//! verdicts the desk shows instead of raw shell output.

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use crate::jsonio::{py_float, Json};
use crate::studio::App;
use crate::studio_reason::explain;

#[cfg(not(target_arch = "wasm32"))]
extern "C" {
    fn dup2(oldfd: i32, newfd: i32) -> i32;
    fn kill(pid: i32, sig: i32) -> i32;
}

/// The child's stderr joins its stdout at the fd level (Python's
/// stderr=STDOUT): after the pipe lands on fd 1, dup it onto fd 2.
#[cfg(not(target_arch = "wasm32"))]
fn merge_stderr(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;
    unsafe {
        cmd.pre_exec(|| {
            dup2(1, 2);
            Ok(())
        });
    }
}

#[cfg(target_arch = "wasm32")]
fn merge_stderr(_cmd: &mut Command) {}

#[cfg(not(target_arch = "wasm32"))]
fn kill9(pid: i32) {
    unsafe {
        kill(pid, 9);
    }
}

#[cfg(target_arch = "wasm32")]
fn kill9(_pid: i32) {}

/// The importer's CLI flags — shared by import, async import and refresh.
pub const OPT_KEYS: [&str; 12] = [
    "id",
    "start",
    "take",
    "sensitivity",
    "bitrate",
    "sample_rate",
    "channels",
    "format",
    "gain_db",
    "fade_in",
    "fade_out",
    "notes",
];

/// studio_jobs.opt_args — `--key=value` (the `=` so a value can't become
/// a flag), values spelled the way str() spells them.
pub fn opt_args(req: &Json, keys: &[&str]) -> Vec<String> {
    let mut args = Vec::new();
    for k in keys {
        let Some(v) = req.get(k) else { continue };
        let s = match v {
            Json::Null => continue,
            Json::Str(s) if s.is_empty() => continue,
            Json::Str(s) => s.clone(),
            Json::Int(i) => i.to_string(),
            Json::Num(f) => py_float(*f),
            Json::Bool(true) => "True".to_string(),
            Json::Bool(false) => "False".to_string(),
            _ => continue,
        };
        args.push(format!("--{}={}", k.replace('_', "-"), s));
    }
    match req.get("normalize") {
        Some(Json::Bool(true)) => args.push("--normalize".to_string()),
        Some(Json::Bool(false)) => args.push("--no-normalize".to_string()),
        _ => {}
    }
    args
}

pub struct Job {
    pub id: String,
    pub phase: String,
    pub percent: f64,
    pub detail: String,
    pub log: Vec<String>,
    pub error: String,
}

fn round1(v: f64) -> f64 {
    format!("{v:.1}").parse().unwrap_or(v)
}

impl Job {
    pub fn as_json(&self) -> Json {
        let done = self.phase == "done" || self.phase == "failed";
        let tail = if self.log.len() > 40 {
            &self.log[self.log.len() - 40..]
        } else {
            &self.log[..]
        };
        Json::Obj(vec![
            ("id".into(), Json::Str(self.id.clone())),
            ("phase".into(), Json::Str(self.phase.clone())),
            ("percent".into(), Json::Num(round1(self.percent))),
            ("detail".into(), Json::Str(self.detail.clone())),
            ("error".into(), Json::Str(self.error.clone())),
            ("done".into(), Json::Bool(done)),
            (
                "log".into(),
                Json::Arr(tail.iter().map(|l| Json::Str(l.clone())).collect()),
            ),
        ])
    }
}

type Registry = Mutex<Vec<(String, Arc<Mutex<Job>>)>>;

fn jobs() -> &'static Registry {
    static J: OnceLock<Registry> = OnceLock::new();
    J.get_or_init(|| Mutex::new(Vec::new()))
}

fn new_id() -> String {
    use std::io::Read;
    let mut b = [0u8; 6];
    // read_exact, never fs::read — /dev/urandom has no EOF to read to.
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .is_err()
    {
        // Never reached on macOS/Linux; a fixed id would still work.
        b = [1, 2, 3, 4, 5, 6];
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

pub fn get(job_id: &str) -> Option<Json> {
    let reg = jobs().lock().unwrap_or_else(|e| e.into_inner());
    let job = reg.iter().find(|(k, _)| k == job_id)?.1.clone();
    drop(reg);
    let j = job.lock().unwrap_or_else(|e| e.into_inner());
    Some(j.as_json())
}

/// JobRunner.start: a job begins queued, runs behind the studio's encode
/// lock, and reports as yt-dlp prints.
pub fn start(app: &Arc<App>, argv: Vec<String>) -> Json {
    let job = Arc::new(Mutex::new(Job {
        id: new_id(),
        phase: "queued".to_string(),
        percent: 0.0,
        detail: String::new(),
        log: Vec::new(),
        error: String::new(),
    }));
    let id = job.lock().unwrap_or_else(|e| e.into_inner()).id.clone();
    {
        let mut reg = jobs().lock().unwrap_or_else(|e| e.into_inner());
        reg.push((id, Arc::clone(&job)));
        if reg.len() > 40 {
            let mut removed = 0;
            reg.retain(|(_, j)| {
                if removed >= 20 {
                    return true;
                }
                let ph = j.lock().unwrap_or_else(|e| e.into_inner()).phase.clone();
                if ph == "done" || ph == "failed" {
                    removed += 1;
                    false
                } else {
                    true
                }
            });
        }
    }
    let snapshot = job.lock().unwrap_or_else(|e| e.into_inner()).as_json();
    let app = Arc::clone(app);
    let worker = Arc::clone(&job);
    std::thread::spawn(move || {
        let _gate = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
        run_child(&worker, &argv);
    });
    snapshot
}

fn set<F: FnOnce(&mut Job)>(job: &Arc<Mutex<Job>>, f: F) {
    let mut j = job.lock().unwrap_or_else(|e| e.into_inner());
    f(&mut j);
}

fn run_child(job: &Arc<Mutex<Job>>, argv: &[String]) {
    set(job, |j| j.phase = "fetching".to_string());
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    merge_stderr(&mut cmd);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            set(job, |j| {
                j.phase = "failed".to_string();
                j.error = e.to_string();
            });
            return;
        }
    };
    let pid = child.id() as i32;
    // Wall-clock kill: a child that stops producing output blocks the
    // read below forever, and the job would sit at "fetching" for the
    // life of the server.
    let done = Arc::new(AtomicBool::new(false));
    let watchdog_done = Arc::clone(&done);
    let mut timed_out = false;
    let watchdog = std::thread::spawn(move || {
        for _ in 0..9000 {
            if watchdog_done.load(Ordering::Relaxed) {
                return false;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        kill9(pid);
        true
    });
    if let Some(out) = child.stdout.take() {
        for raw in BufReader::new(out).lines() {
            let Ok(raw) = raw else { break };
            let line = raw.trim_end().to_string();
            set(job, |j| {
                if !line.is_empty() {
                    j.log.push(line.clone());
                }
                interpret(j, &line);
            });
        }
    }
    let status = child.wait();
    done.store(true, Ordering::Relaxed);
    if let Ok(t) = watchdog.join() {
        timed_out = t;
    }
    let ok = status.as_ref().map(|s| s.success()).unwrap_or(false);
    set(job, |j| {
        if ok {
            j.phase = "done".to_string();
            j.percent = 100.0;
            j.detail = String::new();
        } else {
            j.phase = "failed".to_string();
            let exp = explain(&j.log);
            j.error = if !exp.is_empty() {
                exp
            } else if timed_out {
                "gave up after 15 minutes — the job stalled".to_string()
            } else {
                let code = status
                    .ok()
                    .and_then(|s| s.code())
                    .map(|c| c.to_string())
                    .unwrap_or_else(|| "-9".to_string());
                format!("import failed (exit {code})")
            };
        }
    });
}

/// JobRunner._interpret — yt-dlp's progress line and the phase markers.
fn interpret(job: &mut Job, line: &str) {
    if let Some((pct, size, rate, eta)) = progress(line) {
        job.phase = "fetching".to_string();
        job.percent = pct;
        job.detail = size;
        if let Some(r) = rate {
            job.detail.push_str(&format!(" at {r}"));
        }
        if let Some(e) = eta {
            job.detail.push_str(&format!(", {e} left"));
        }
        return;
    }
    if line.contains("ExtractAudio") || line.starts_with("[ffmpeg]") {
        job.phase = "converting".to_string();
        job.percent = 100.0;
        job.detail = "extracting audio".to_string();
    } else if line.starts_with("imported ") {
        job.phase = "analysing".to_string();
        job.detail = "detecting onsets".to_string();
    }
}

/// `[download]  41.8% of ~2.39MiB at 15.81MiB/s ETA 00:00`
fn progress(line: &str) -> Option<(f64, String, Option<String>, Option<String>)> {
    let at = line.find("[download]")?;
    let mut rest = line[at + 10..].trim_start();
    let pct_end = rest.find('%')?;
    let pct: f64 = rest[..pct_end]
        .trim()
        .parse()
        .ok()
        .filter(|_| !rest[..pct_end].trim().is_empty())?;
    rest = rest[pct_end + 1..].trim_start();
    rest = rest.strip_prefix("of")?.trim_start();
    rest = rest.strip_prefix('~').map(str::trim_start).unwrap_or(rest);
    let mut words = rest.split_whitespace();
    let size = words.next()?.to_string();
    let toks: Vec<&str> = words.collect();
    let mut rate = None;
    let mut eta = None;
    let mut i = 0;
    while i + 1 < toks.len() {
        if toks[i] == "at" && rate.is_none() && eta.is_none() {
            rate = Some(toks[i + 1].to_string());
            i += 2;
        } else if toks[i] == "ETA" {
            eta = Some(toks[i + 1].to_string());
            break;
        } else {
            break;
        }
    }
    Some((pct, size, rate, eta))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn progress_lines_parse_like_the_regex() {
        let (pct, size, rate, eta) =
            progress("[download]  41.8% of 2.39MiB at 15.81MiB/s ETA 00:00").unwrap();
        assert_eq!(pct, 41.8);
        assert_eq!(size, "2.39MiB");
        assert_eq!(rate.as_deref(), Some("15.81MiB/s"));
        assert_eq!(eta.as_deref(), Some("00:00"));
        let (pct, size, rate, eta) = progress("[download] 100% of ~ 4.0MiB").unwrap();
        assert_eq!(
            (pct, size.as_str(), rate, eta),
            (100.0, "4.0MiB", None, None)
        );
        assert!(progress("[youtube] extracting").is_none());
    }
}
