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

unsafe extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
}

/// Give a child its own process group, so a watchdog can kill what the
/// child SPAWNED as well as the child itself.
///
/// yt-dlp shells out to ffmpeg, `sh -c` forks, and a killed parent leaves
/// those holding the write end of the pipe we are draining — so the reader
/// thread blocked for the GRANDCHILD's full run and a "timed out after 60s"
/// reply arrived at 85 s (grade report 2026-09-17 B2). Python's side has
/// always done this: `tools/progress_process.py` spawns with
/// `start_new_session=True` and kills with `os.killpg`.
pub fn own_group(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;
    cmd.process_group(0);
}

/// SIGKILL a whole process group — the child `own_group` made a leader of,
/// and everything it spawned. `pid` is the leader's, which is the group's.
///
/// A negative pid is the group; a stray positive one would be a signal to
/// something else entirely, so a pid that is not a plausible leader is left
/// alone and the caller's `child.kill()` stands on its own.
pub fn kill_group(pid: i32) {
    if pid > 1 {
        unsafe {
            kill(-pid, 9);
        }
    }
}

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
pub fn run_split(cmd: Command, timeout_s: u64) -> Timed {
    run_piped(cmd, None, timeout_s)
}

/// run_split for a child that is fed on stdin — the `compare` shim, whose
/// payload is a JSON line. Written from a thread, because a payload larger
/// than the pipe's buffer would otherwise deadlock against a child that is
/// waiting for us to read what it has already printed. Same watchdog: this
/// one used to be the last child in the crate with none, and it runs while
/// the studio's oplock is held (grade report 2026-09-17 B3).
pub fn run_input(cmd: Command, payload: &str, timeout_s: u64) -> Timed {
    run_piped(cmd, Some(payload.to_string()), timeout_s)
}

fn run_piped(mut cmd: Command, input: Option<String>, timeout_s: u64) -> Timed {
    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        });
    own_group(&mut cmd);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return Timed::Done(false, String::new(), e.to_string()),
    };
    if let (Some(text), Some(mut pipe)) = (input, child.stdin.take()) {
        std::thread::spawn(move || {
            use std::io::Write;
            // The pipe is dropped here, which is the EOF the child waits for.
            let _ = pipe.write_all(text.as_bytes());
        });
    }
    let pid = child.id() as i32;
    // On the shutdown list until it is waited for: a ctrl-c between here
    // and the join below has to reach the group too, not just the watchdog
    // (grade report 2026-09-17 pm B1).
    crate::studio_reap::register(pid);
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
                    // The GROUP, not just the child: the two joins below
                    // wait for every holder of the pipe to let go of it, so
                    // killing the child alone made the watchdog as slow as
                    // whatever the child had spawned.
                    kill_group(pid);
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
    crate::studio_reap::forget(pid);
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
        // The simple case: one process, spawned directly, the way the
        // generators are. The shell-wrapper case — where the grandchild used
        // to hold the pipe open past the deadline — is the next test.
        let mut c = Command::new("sleep");
        c.arg("30");
        let start = std::time::Instant::now();
        let (ok, log) = run(c, 1);
        assert!(!ok);
        assert!(log.contains("gave up after 1s"), "{log}");
        assert!(start.elapsed() < std::time::Duration::from_secs(10));
    }

    /// grade report 2026-09-17 B2: the watchdog killed the child and then
    /// joined the reader threads, which wait for every holder of the pipe —
    /// so a `yt-dlp` that had forked (or an `sh -c` that had) kept the
    /// deadline waiting for the GRANDCHILD. Reproduced as a 60 s timeout
    /// answering at 85 s. The child is its own process group now and the
    /// group is what gets killed, so both processes go and the joins return.
    #[test]
    fn the_watchdog_takes_the_grandchildren_with_it() {
        // The process id, not the thread's: this is the one test that needs
        // the path, and a thread id's `ThreadId(3)` is not shell-safe.
        let dir = std::env::temp_dir().join(format!("castle-pgid-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let pidfile = dir.join("grandchild.pid");
        let mut c = Command::new("/bin/sh");
        c.args([
            "-c",
            &format!("sleep 30 & echo $! > {}; wait", pidfile.display()),
        ]);
        let start = std::time::Instant::now();
        let (ok, log) = run(c, 1);
        let took = start.elapsed();
        assert!(!ok);
        assert!(log.contains("gave up after 1s"), "{log}");
        assert!(
            took < std::time::Duration::from_secs(10),
            "the watchdog waited for the grandchild ({took:?})"
        );
        // And the grandchild is actually gone, rather than orphaned holding
        // the CPU it was killed to release.
        let pid: i32 = std::fs::read_to_string(&pidfile)
            .expect("the shell wrote its child's pid")
            .trim()
            .parse()
            .expect("a pid");
        let mut gone = false;
        for _ in 0..200 {
            if unsafe { kill(pid, 0) } != 0 {
                gone = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(25));
        }
        assert!(gone, "the grandchild sleep {pid} outlived the watchdog");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The stdin form: the payload reaches the child, and the child's answer
    /// comes back whole — the shim's shape, minus the shim.
    #[test]
    fn a_child_can_be_fed_on_stdin_under_the_same_watchdog() {
        let mut c = Command::new("/bin/sh");
        c.args(["-c", "cat"]);
        match run_input(c, "{\"codec\": \"opus\"}\n", 30) {
            Timed::Done(ok, out, _) => {
                assert!(ok);
                assert_eq!(out.trim(), "{\"codec\": \"opus\"}");
            }
            Timed::Out => panic!("cat did not finish"),
        }
        // A payload larger than a pipe buffer must not deadlock: the writer
        // is a thread, and the readers drain while it writes.
        let big = "x".repeat(500_000);
        let mut c = Command::new("/bin/sh");
        c.args(["-c", "wc -c"]);
        match run_input(c, &big, 30) {
            Timed::Done(ok, out, _) => {
                assert!(ok);
                assert_eq!(out.trim(), "500000");
            }
            Timed::Out => panic!("wc deadlocked on a 500 KB payload"),
        }
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
