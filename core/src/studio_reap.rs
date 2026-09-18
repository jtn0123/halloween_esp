//! Taking the children with us — the shutdown half of
//! [`studio_proc`](crate::studio_proc).
//!
//! Every studio child is spawned into its own process group
//! (`studio_proc::own_group`), because a watchdog that kills only the child
//! leaves the grandchild holding the pipe (grade report 2026-09-17 B2). The
//! price of that fix was the terminal's own signal: a child in its own group
//! no longer hears the Ctrl-C that reaches `make studio`, so quitting the
//! server orphaned a running ffmpeg/yt-dlp/demucs for the rest of its
//! fifteen minutes (grade report 2026-09-17 pm B1).
//!
//! So the groups are written down here — pushed on spawn, removed on reap —
//! and the studio bin installs SIGINT/SIGTERM handlers that kill every
//! leader still on the list before exiting 128+signo, which is what the
//! terminal used to do for us.
//!
//! The handler itself does almost nothing: one atomic store and one
//! `write()` down a self-pipe, both async-signal-safe. A dedicated thread
//! reads the pipe and does the killing, so no `Mutex` is ever locked from
//! signal context.

use std::sync::atomic::{AtomicI32, Ordering};
use std::sync::{Mutex, OnceLock};

use crate::studio_proc::kill_group;

unsafe extern "C" {
    fn pipe(fds: *mut i32) -> i32;
    fn read(fd: i32, buf: *mut u8, n: usize) -> isize;
    fn write(fd: i32, buf: *const u8, n: usize) -> isize;
    fn signal(sig: i32, handler: extern "C" fn(i32)) -> usize;
}

const SIGINT: i32 = 2;
const SIGTERM: i32 = 15;

/// The live group leaders — one entry per spawned child that is still
/// running. A process-wide static rather than a field on
/// [`App`](crate::studio::App): `studio_proc::run_piped` is called from
/// five places that have no `App` (the probe shim, the publisher, the
/// generators) and a registry only half the spawns reach is worse than
/// none.
fn leaders() -> &'static Mutex<Vec<i32>> {
    static L: OnceLock<Mutex<Vec<i32>>> = OnceLock::new();
    L.get_or_init(|| Mutex::new(Vec::new()))
}

/// A child has just been spawned into its own group. Call it with the
/// child's pid, which `own_group` made the group's too.
pub fn register(pid: i32) {
    if pid > 1 {
        leaders()
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(pid);
    }
}

/// The child has been waited for: its group is gone (or ours to forget).
pub fn forget(pid: i32) {
    let mut live = leaders().lock().unwrap_or_else(|e| e.into_inner());
    if let Some(i) = live.iter().position(|&p| p == pid) {
        live.swap_remove(i);
    }
}

/// SIGKILL every group still registered, and empty the list. Called from
/// the shutdown thread, never from a signal handler.
pub fn kill_all() {
    let doomed: Vec<i32> =
        std::mem::take(&mut *leaders().lock().unwrap_or_else(|e| e.into_inner()));
    for pid in doomed {
        kill_group(pid);
    }
}

/// How many groups are live — for the tests, and for a status line if the
/// desk ever wants one.
pub fn live_count() -> usize {
    leaders().lock().unwrap_or_else(|e| e.into_inner()).len()
}

static PENDING: AtomicI32 = AtomicI32::new(0);
static WAKE_FD: AtomicI32 = AtomicI32::new(-1);

/// Async-signal-safe by construction: a lock-free store and a one-byte
/// `write()`, and nothing else. No allocation, no locking, no formatting.
extern "C" fn on_signal(sig: i32) {
    PENDING.store(sig, Ordering::SeqCst);
    let fd = WAKE_FD.load(Ordering::SeqCst);
    if fd >= 0 {
        let byte: u8 = 1;
        // A full pipe means a signal is already pending, which is the same
        // answer; the return is deliberately ignored.
        unsafe { write(fd, &byte, 1) };
    }
}

/// Catch SIGINT and SIGTERM for the life of the process: kill every
/// registered group, then exit 128+signo the way a shell-killed process
/// does. Idempotent — the studio calls it once, the tests may not.
pub fn install_shutdown_handlers() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        let mut fds = [-1i32; 2];
        if unsafe { pipe(fds.as_mut_ptr()) } != 0 {
            // No self-pipe, no safe handler. Leave the default disposition
            // alone rather than install one that cannot signal its thread.
            eprintln!("studio: no shutdown pipe — children may outlive a ctrl-c");
            return;
        }
        WAKE_FD.store(fds[1], Ordering::SeqCst);
        let wake = fds[0];
        std::thread::spawn(move || {
            wait_for_signal(wake);
            let sig = PENDING.load(Ordering::SeqCst);
            kill_all();
            std::process::exit(128 + sig);
        });
        for sig in [SIGINT, SIGTERM] {
            unsafe { signal(sig, on_signal) };
        }
    });
}

/// Block until the handler pokes the pipe. `read` is interrupted by every
/// signal that arrives while we are in it, so a short error pause keeps a
/// broken fd from becoming a spin.
fn wait_for_signal(fd: i32) {
    let mut byte = [0u8; 1];
    loop {
        let n = unsafe { read(fd, byte.as_mut_ptr(), 1) };
        if n == 1 {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    unsafe extern "C" {
        fn kill(pid: i32, sig: i32) -> i32;
    }

    /// The harness below is this same test binary, re-run with the pidfile
    /// it should report through named in the environment.
    const HARNESS_ENV: &str = "CASTLE_REAP_HARNESS_PIDFILE";

    fn alive(pid: i32) -> bool {
        unsafe { kill(pid, 0) == 0 }
    }

    #[test]
    fn the_registry_forgets_a_reaped_leader_and_only_that_one() {
        // Not the live list the other test uses: plain bookkeeping, checked
        // against pids that cannot collide with a real child's.
        let before = live_count();
        register(0); // a group of "whatever we are" is never registered
        register(1);
        assert_eq!(live_count(), before, "0 and 1 are not leaders");
        register(0x7f00_0001);
        register(0x7f00_0002);
        assert_eq!(live_count(), before + 2);
        forget(0x7f00_0001);
        forget(0x7f00_0001); // twice is not an error
        assert_eq!(live_count(), before + 1);
        forget(0x7f00_0002);
        assert_eq!(live_count(), before);
    }

    /// grade report 2026-09-17 pm B1: in the shape of
    /// `studio_proc.rs`'s watchdog-grandchild test, but the thing that
    /// fires is a SIGNAL rather than a deadline. A child studio (this test
    /// binary, re-run as the harness) starts a `sleep` through the real
    /// `studio_proc::run` path; we SIGTERM the studio and the sleep has to
    /// die with it, rather than outliving the server that started it.
    #[test]
    fn a_signalled_studio_takes_its_children_with_it() {
        let dir = std::env::temp_dir().join(format!("castle-reap-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let pidfile = dir.join("grandchild.pid");
        let _ = std::fs::remove_file(&pidfile);
        let exe = std::env::current_exe().expect("the test binary");
        let mut harness = Command::new(exe)
            .args([
                "--exact",
                "studio_reap::tests::the_harness_studio",
                "--ignored",
                "--test-threads=1",
            ])
            .env(HARNESS_ENV, &pidfile)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("re-running this binary as a harness studio");

        // The harness writes the grandchild's pid once the shell has it.
        let deadline = Instant::now() + Duration::from_secs(60);
        let pid = loop {
            if let Ok(t) = std::fs::read_to_string(&pidfile) {
                if let Ok(p) = t.trim().parse::<i32>() {
                    break p;
                }
            }
            assert!(
                Instant::now() < deadline,
                "the harness never reported a grandchild"
            );
            std::thread::sleep(Duration::from_millis(50));
        };
        assert!(alive(pid), "the grandchild {pid} never ran");

        unsafe { kill(harness.id() as i32, SIGTERM) };
        let st = harness.wait().expect("the harness exits");
        assert_eq!(
            st.code(),
            Some(128 + SIGTERM),
            "a signalled studio exits 128+signo"
        );

        let mut gone = false;
        for _ in 0..400 {
            if !alive(pid) {
                gone = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        if !gone {
            unsafe { kill(pid, 9) };
        }
        assert!(
            gone,
            "the grandchild {pid} outlived the studio that spawned it"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Not a test: the studio the test above kills. Ignored so a plain
    /// `cargo test` never runs it, and a no-op unless the environment names
    /// the pidfile, so `cargo test -- --ignored` on its own still passes.
    #[test]
    #[ignore = "a harness process, spawned by a_signalled_studio_takes_its_children_with_it"]
    fn the_harness_studio() {
        let Some(pidfile) = std::env::var_os(HARNESS_ENV) else {
            return;
        };
        let pidfile = PathBuf::from(pidfile);
        install_shutdown_handlers();
        std::thread::spawn(move || {
            let mut c = Command::new("/bin/sh");
            c.args([
                "-c",
                &format!("sleep 300 & echo $! > {}; wait", pidfile.display()),
            ]);
            // The production path: own_group, register, watchdog, forget.
            let _ = crate::studio_proc::run(c, 600);
        });
        // The signal arrives within a second or two; this is only a ceiling
        // so a harness that is somehow never signalled cannot hang CI.
        std::thread::sleep(Duration::from_secs(120));
        eprintln!("harness studio was never signalled");
        std::process::exit(9);
    }
}
