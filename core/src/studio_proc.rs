//! Running the studio's children — the half of tools/studio.py that is
//! about processes rather than scenes.
//!
//! Every rebuild, import, probe and comparison is a spawned venv tool, so
//! "which python" and "capture it completely, under a watchdog" are asked
//! by five callers and answered once. Split out of
//! [`studio_scenes`](crate::studio_scenes) when that file reached the
//! repo's 500-line cap and its own tests had nowhere to go; the scene
//! editor re-exports the whole surface, so `studio_scenes::run` still
//! means what it did.

use std::path::Path;
use std::process::{Command, Stdio};

/// The interpreter the studio's children run under.
///
/// The Python twin runs its children under `sys.executable` — whatever
/// interpreter is running the server, which is the venv you launched it
/// from. A binary has no such self-knowledge, so the answer is asked for
/// in the same order: `CASTLE_PY` if the launcher named one (a worktree,
/// CI, a venv somewhere else entirely), then the project venv, then
/// `python3` — which, missing numpy/scipy/yaml, is the spelling that used
/// to fail every rebuild confusingly. `check_py` says so at startup.
pub fn py(root: &Path) -> String {
    if let Some(p) = std::env::var_os("CASTLE_PY") {
        if !p.is_empty() {
            return p.to_string_lossy().into_owned();
        }
    }
    let v = root.join(".venv").join("bin").join("python");
    if v.exists() {
        v.to_string_lossy().into_owned()
    } else {
        "python3".to_string()
    }
}

/// Can the interpreter `py()` picked actually run the studio's children?
/// `import yaml` is the cheapest question that separates the project venv
/// from a bare system python: every generator, the importer and the scene
/// writer need it. Some(complaint) when it cannot — the caller prints it
/// and stops, rather than letting every later rebuild fail confusingly.
pub fn check_py(root: &Path) -> Option<String> {
    let exe = py(root);
    let out = Command::new(&exe)
        .args(["-c", "import yaml"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    match out {
        Ok(s) if s.success() => None,
        Ok(_) => Some(format!(
            "{exe} cannot `import yaml` — the studio's children (the \
             generators, the importer) all need the project venv. Run \
             `make setup`, or point CASTLE_PY at the right interpreter."
        )),
        Err(e) => Some(format!(
            "{exe} will not run ({e}) — set CASTLE_PY to the project venv's \
             python, or run `make setup`."
        )),
    }
}

/// Python's `s[-4000:]` — the last 4000 characters, not bytes.
pub fn tail4000(s: &str) -> String {
    let n = s.chars().count();
    if n <= 4000 {
        s.to_string()
    } else {
        s.chars().skip(n - 4000).collect()
    }
}

/// studio.run(): capture a child completely, under the 900 s ceiling that
/// keeps one hung tool from wedging every later rebuild.
pub fn run(cmd: Command, timeout_s: u64) -> (bool, String) {
    match run_split(cmd, timeout_s) {
        Timed::Out => (
            false,
            format!("gave up after {timeout_s}s — the job stalled"),
        ),
        Timed::Done(ok, out, err) => (ok, tail4000(&format!("{out}{err}"))),
    }
}

/// The two-stream form probe needs (yt-dlp's useful line is on stderr).
pub fn run_split(mut cmd: Command, timeout_s: u64) -> Timed {
    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return Timed::Done(false, String::new(), e.to_string()),
    };
    let mut out_pipe = child.stdout.take().expect("piped");
    let mut err_pipe = child.stderr.take().expect("piped");
    let out_t = std::thread::spawn(move || {
        use std::io::Read;
        let mut b = Vec::new();
        let _ = out_pipe.read_to_end(&mut b);
        b
    });
    let err_t = std::thread::spawn(move || {
        use std::io::Read;
        let mut b = Vec::new();
        let _ = err_pipe.read_to_end(&mut b);
        b
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(timeout_s);
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break Some(st),
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(_) => break None,
        }
    };
    let out = out_t.join().unwrap_or_default();
    let err = err_t.join().unwrap_or_default();
    match status {
        None => Timed::Out,
        Some(st) => Timed::Done(
            st.success(),
            String::from_utf8_lossy(&out).into_owned(),
            String::from_utf8_lossy(&err).into_owned(),
        ),
    }
}

/// run_split's answer: the child finished, or the watchdog fired.
pub enum Timed {
    Done(bool, String, String),
    Out,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_log_tail_counts_characters_not_bytes() {
        assert_eq!(tail4000("short"), "short");
        assert_eq!(tail4000(""), "");
        let exact: String = "x".repeat(4000);
        assert_eq!(tail4000(&exact), exact);
        let long: String = "y".repeat(4001);
        assert_eq!(tail4000(&long).chars().count(), 4000);
        // Python slices str by character; a byte slice here would cut a
        // multi-byte log line in half and hand back replacement chars.
        let wide: String = "é".repeat(5000);
        let cut = tail4000(&wide);
        assert_eq!(cut.chars().count(), 4000);
        assert!(cut.chars().all(|c| c == 'é'));
    }

    #[test]
    fn a_child_is_captured_whole_on_both_streams() {
        let mut c = Command::new("/bin/sh");
        c.args(["-c", "printf out; printf err 1>&2; exit 3"]);
        let (ok, log) = run(c, 30);
        assert!(!ok, "exit 3 is a failure");
        assert_eq!(log, "outerr", "stdout then stderr, both kept");
    }

    #[test]
    fn a_child_that_cannot_be_spawned_is_a_failure_not_a_panic() {
        let (ok, log) = run(Command::new("/nowhere/_t_no_such_tool"), 30);
        assert!(!ok);
        assert!(!log.is_empty(), "the OS error is the log");
    }

    #[test]
    fn the_watchdog_kills_a_child_that_will_not_finish() {
        // sleep is spawned DIRECTLY, not through `sh -c`: Linux's sh forks a
        // grandchild that inherits the pipe, so killing the shell leaves the
        // stream drain blocked for the grandchild's full 30s — which is a
        // property of the shell wrapper, not of the watchdog. Production
        // children (the generators) are spawned directly, like this.
        let mut c = Command::new("sleep");
        c.arg("30");
        let start = std::time::Instant::now();
        let (ok, log) = run(c, 1);
        assert!(!ok);
        assert!(log.contains("gave up after 1s"), "{log}");
        assert!(start.elapsed() < std::time::Duration::from_secs(10));
    }

    #[test]
    fn py_names_the_interpreter_the_launcher_asked_for() {
        // The env is read, not written: setting CASTLE_PY here would race
        // every other test in this process. A launcher that named one wins
        // outright; otherwise a root with no .venv falls through to the
        // PATH's python3, and a root with one names the file inside it.
        let root = Path::new("/nowhere/_t_no_such_root");
        match std::env::var_os("CASTLE_PY").filter(|p| !p.is_empty()) {
            Some(named) => assert_eq!(py(root), named.to_string_lossy()),
            None => {
                assert_eq!(py(root), "python3");
                let repo = crate::studio::repo_root();
                if repo.join(".venv").join("bin").join("python").exists() {
                    assert!(py(&repo).ends_with("/.venv/bin/python"), "{}", py(&repo));
                }
            }
        }
    }
}
