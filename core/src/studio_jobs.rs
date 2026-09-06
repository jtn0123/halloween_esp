//! Background jobs — tools/studio_jobs.py: the runner that babysits
//! yt-dlp/ffmpeg children, feeds their output to the progress reader next
//! door (`studio_progress`), and hands the desk a one-line reason
//! (`studio_reason`) instead of raw shell when one of them dies.

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use crate::jsonio::{Json, py_float};
use crate::studio::App;
use crate::studio_progress::{Job, interpret};
use crate::studio_reason::explain;

unsafe extern "C" {
    fn dup2(oldfd: i32, newfd: i32) -> i32;
    fn kill(pid: i32, sig: i32) -> i32;
}

/// The child's stderr joins its stdout at the fd level (Python's
/// stderr=STDOUT): after the pipe lands on fd 1, dup it onto fd 2.
fn merge_stderr(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;
    unsafe {
        cmd.pre_exec(|| {
            dup2(1, 2);
            Ok(())
        });
    }
}

fn kill9(pid: i32) {
    unsafe {
        kill(pid, 9);
    }
}

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
    let job = Arc::new(Mutex::new(Job::new(new_id())));
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::time::{Duration, Instant};

    /// The registry is process-global, one per server, and cargo runs the
    /// tests in threads of one process — so the tests that put jobs in it
    /// queue behind this instead of counting each other's work.
    fn registry_gate() -> &'static Mutex<()> {
        static G: OnceLock<Mutex<()>> = OnceLock::new();
        G.get_or_init(|| Mutex::new(()))
    }

    fn app() -> Arc<App> {
        Arc::new(App::new(PathBuf::from(".")))
    }

    fn start_id(app: &Arc<App>, argv: &[&str]) -> String {
        let snap = start(app, argv.iter().map(|s| s.to_string()).collect());
        snap.get("id")
            .and_then(Json::as_str)
            .expect("start() returned no id")
            .to_string()
    }

    fn text(id: &str, key: &str) -> String {
        let j = get(id).expect("the job left the registry");
        j.get(key)
            .and_then(Json::as_str)
            .unwrap_or_default()
            .to_string()
    }

    fn log_of(id: &str) -> Vec<String> {
        let j = get(id).expect("the job left the registry");
        match j.get("log") {
            Some(Json::Arr(v)) => v
                .iter()
                .map(|l| l.as_str().unwrap_or_default().to_string())
                .collect(),
            _ => panic!("the log is not an array"),
        }
    }

    /// Spin rather than sleep a fixed time, so the assertions are not racy
    /// on a loaded machine.
    fn wait_done(id: &str) -> String {
        for _ in 0..2000 {
            let phase = text(id, "phase");
            if phase == "done" || phase == "failed" {
                return phase;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        panic!("job {id} never finished");
    }

    /// The whole point of the runner: the HTTP request hands back an id
    /// and lets go, instead of holding the connection open for a
    /// forty-minute download.
    #[test]
    fn start_returns_at_once_with_an_id_the_page_can_poll() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let app = app();
        let t0 = Instant::now();
        let id = start_id(&app, &["sleep", "1"]);
        assert!(
            t0.elapsed() < Duration::from_millis(300),
            "start() blocked on the child process"
        );
        assert_eq!(id.len(), 12);
        let phase = text(&id, "phase");
        assert!(phase == "queued" || phase == "fetching", "{phase}");
        assert_eq!(text(&id, "id"), id);
    }

    /// A poll for a job that has been pruned, or was never started, must
    /// answer "no such job" rather than invent one.
    #[test]
    fn a_poll_for_an_unknown_id_finds_nothing() {
        assert!(get("nosuchjob").is_none());
    }

    /// The studio's synchronous encodes and its background jobs take turns
    /// at ffmpeg: while the oplock is held the job sits queued — visibly
    /// in line, not hung — and it runs the moment the lock is free.
    ///
    /// Python's JobRunner takes its gate as an argument and defaults to
    /// None; the Rust runner has no such knob, because there is exactly
    /// one runner and the App's oplock is always its gate. There is no
    /// ungated case here to test.
    #[test]
    fn a_job_waits_while_the_studios_oplock_is_held() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let app = app();
        let held = app.oplock.lock().unwrap_or_else(|e| e.into_inner());
        let id = start_id(&app, &["true"]);
        std::thread::sleep(Duration::from_millis(150));
        assert_eq!(text(&id, "phase"), "queued");
        drop(held);
        assert_eq!(wait_done(&id), "done");
    }

    /// The gate is held for the life of the child, not just for the
    /// paperwork around it — otherwise two ffmpegs race for the CPU and
    /// both take longer than either would alone.
    #[test]
    fn a_running_job_holds_the_oplock_until_its_child_exits() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let app = app();
        let id = start_id(&app, &["sleep", "0.3"]);
        std::thread::sleep(Duration::from_millis(100));
        assert!(app.oplock.try_lock().is_err(), "the gate was free mid-job");
        wait_done(&id);
        // The worker drops the gate just after it writes the phase, so
        // give that last instruction a moment rather than a lucky read.
        let mut freed = false;
        for _ in 0..200 {
            if app.oplock.try_lock().is_ok() {
                freed = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(freed, "the gate was never released");
    }

    /// A finished import leaves the bar full and the error box empty —
    /// the page reads both, and a stale percentage reads as a stall.
    #[test]
    fn a_successful_child_ends_done_at_a_hundred_with_no_error() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let id = start_id(&app(), &["true"]);
        assert_eq!(wait_done(&id), "done");
        let j = get(&id).unwrap();
        assert_eq!(j.get("percent").and_then(Json::as_f64), Some(100.0));
        assert_eq!(text(&id, "error"), "");
    }

    /// A failure with nothing to explain still needs a non-empty message:
    /// an empty error box tells the person nothing at all.
    #[test]
    fn a_failing_child_ends_failed_naming_the_exit_code() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let id = start_id(&app(), &["false"]);
        assert_eq!(wait_done(&id), "failed");
        let err = text(&id, "error");
        assert!(err.contains("exit 1"), "{err}");
    }

    /// When the child did say something worth reading, the desk gets the
    /// sentence — and the raw line stays in the log for whoever wants it.
    #[test]
    fn a_failing_childs_output_is_translated_before_the_desk_sees_it() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let id = start_id(
            &app(),
            &[
                "sh",
                "-c",
                "echo 'ERROR: [youtube] x: Private video'; exit 1",
            ],
        );
        assert_eq!(wait_done(&id), "failed");
        assert_eq!(text(&id, "error"), "That video is private.");
        assert!(
            log_of(&id).contains(&"ERROR: [youtube] x: Private video".to_string()),
            "the raw line was dropped from the log"
        );
    }

    /// The worker thread has nobody to raise to, so a binary that is not
    /// there has to land on the job as a failure rather than take the
    /// thread down silently.
    #[test]
    fn a_missing_binary_fails_the_job_instead_of_killing_its_thread() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let id = start_id(&app(), &["/nonexistent/definitely-not-here"]);
        assert_eq!(wait_done(&id), "failed");
        assert!(!text(&id, "error").is_empty(), "failure carried no reason");
    }

    /// End to end through the pipe: a line the child printed reaches the
    /// log the browser polls.
    #[test]
    fn progress_from_a_real_child_reaches_the_jobs_log() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let line = "[download]  41.8% of 2.39MiB at 1.0MiB/s ETA 00:03";
        let id = start_id(&app(), &["sh", "-c", &format!("echo '{line}'")]);
        assert_eq!(wait_done(&id), "done");
        assert_eq!(log_of(&id), vec![line.to_string()]);
    }

    /// yt-dlp pads its output with blank lines; logging them would push
    /// the useful forty out of the tail the desk is shown.
    #[test]
    fn blank_output_lines_are_not_logged() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        let id = start_id(&app(), &["sh", "-c", "echo; echo kept; echo"]);
        assert_eq!(wait_done(&id), "done");
        assert_eq!(log_of(&id), vec!["kept".to_string()]);
    }

    /// A studio left open all October must not accumulate every import it
    /// ever ran — and the prune must never take the job that just began,
    /// which is the one somebody is watching.
    #[test]
    fn finished_jobs_are_pruned_but_the_newest_survives() {
        let _g = registry_gate().lock().unwrap_or_else(|e| e.into_inner());
        jobs().lock().unwrap_or_else(|e| e.into_inner()).clear();
        let app = app();
        for _ in 0..40 {
            let id = start_id(&app, &["true"]);
            wait_done(&id);
        }
        let before = jobs().lock().unwrap_or_else(|e| e.into_inner()).len();
        assert_eq!(before, 40);
        let newest = start_id(&app, &["true"]);
        let after = jobs().lock().unwrap_or_else(|e| e.into_inner()).len();
        assert!(after < 41, "finished jobs were never pruned ({after})");
        assert!(
            get(&newest).is_some(),
            "pruning threw away the job that just started"
        );
    }
}
