//! A child and everything it spawns, as one thing a watchdog can kill.
//!
//! yt-dlp shells out to ffmpeg, a python tool forks, and a killed parent
//! leaves those holding the write end of the pipe we are draining — so the
//! reader thread blocked for the GRANDCHILD's full run and a "timed out
//! after 60s" reply arrived at 85 s (grade report 2026-09-17 B2). The answer
//! is per platform, behind one surface:
//!
//! - **Unix**: the child leads its own process group (`own_group`, before
//!   the spawn) and the group is SIGKILLed as a whole. Python's side does
//!   the same: `tools/progress_process.py` spawns with
//!   `start_new_session=True` and kills with `os.killpg`.
//! - **Windows**: there are no process groups that can be signalled, so the
//!   child is put in a fresh Job Object right after the spawn (`adopt`) and
//!   the job is terminated as a whole. Everything the child starts is in the
//!   job too. The job is created `KILL_ON_JOB_CLOSE`, and the handle is the
//!   studio's alone — so if the studio dies any way at all, a ctrl-c or a
//!   Task Manager kill included, Windows closes the handle and takes every
//!   child with it. That is the job `studio_reap`'s signal handlers do on
//!   Unix, done by the kernel.
//!
//! Callers do the same four things on both: `own_group(&mut cmd)` before
//! the spawn, `adopt(&child)` after it, `kill_group(pid)` to end it early,
//! `release(pid)` once it has been waited for. Each is a no-op where the
//! platform needs nothing.
//!
//! One Windows difference worth knowing: a job is assigned after
//! CreateProcess returns, so a grandchild started in the first instant of
//! the child's life (before `adopt`) would escape it. Every child here is a
//! Python interpreter or yt-dlp, which take far longer than that to start
//! anything. And `release` closes the job, which ends anything the child
//! left running behind it — where on Unix a grandchild that outlives a
//! finished child is left alone. Nothing the studio runs leaves one on
//! purpose.

use std::process::{Child, Command};
#[cfg(windows)]
use std::sync::PoisonError;

#[cfg(unix)]
unsafe extern "C" {
    fn kill(pid: i32, sig: i32) -> i32;
}

/// Give a child its own process group, so a watchdog can kill what the
/// child SPAWNED as well as the child itself. On Windows the grouping is
/// done after the spawn, by [`adopt`].
#[cfg(unix)]
pub fn own_group(cmd: &mut Command) {
    use std::os::unix::process::CommandExt;
    cmd.process_group(0);
}

#[cfg(windows)]
pub fn own_group(_cmd: &mut Command) {}

/// SIGKILL a whole process group — the child `own_group` made a leader of,
/// and everything it spawned. `pid` is the leader's, which is the group's.
///
/// A negative pid is the group; a stray positive one would be a signal to
/// something else entirely, so a pid that is not a plausible leader is left
/// alone and the caller's `child.kill()` stands on its own.
#[cfg(unix)]
pub fn kill_group(pid: i32) {
    if pid > 1 {
        unsafe {
            kill(-pid, 9);
        }
    }
}

/// Terminate the job `adopt` put this child in — the child and everything
/// it started. A pid with no job (never adopted, or already released) is
/// left alone, which is also what makes a late watchdog harmless.
#[cfg(windows)]
pub fn kill_group(pid: i32) {
    let jobs = win::jobs().lock().unwrap_or_else(PoisonError::into_inner);
    if let Some((_, job)) = jobs.iter().find(|(p, _)| *p == pid) {
        unsafe { crate::win32::TerminateJobObject(*job as crate::win32::Handle, 1) };
    }
}

/// Put a just-spawned child in a job of its own (Windows); nothing to do on
/// Unix, where `own_group` already did it before the spawn.
#[cfg(unix)]
pub fn adopt(_child: &Child) {}

#[cfg(windows)]
pub fn adopt(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    if let Some(job) = win::new_job() {
        let ok = unsafe { crate::win32::AssignProcessToJobObject(job, child.as_raw_handle()) };
        if ok == 0 {
            // A studio that is itself in a job that forbids nesting (pre-
            // Windows 8 rules) cannot do this; the child's own kill() is
            // then all the watchdog has, as before.
            unsafe { crate::win32::CloseHandle(job) };
            return;
        }
        let pid = child.id() as i32;
        win::jobs()
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push((pid, job as usize));
    }
}

/// The child has been waited for: drop its job (Windows). Closing the last
/// handle ends whatever the child left running, by KILL_ON_JOB_CLOSE.
#[cfg(unix)]
pub fn release(_pid: i32) {}

#[cfg(windows)]
pub fn release(pid: i32) {
    let mut jobs = win::jobs().lock().unwrap_or_else(PoisonError::into_inner);
    if let Some(i) = jobs.iter().position(|(p, _)| *p == pid) {
        let (_, job) = jobs.swap_remove(i);
        unsafe { crate::win32::CloseHandle(job as crate::win32::Handle) };
    }
}

#[cfg(windows)]
mod win {
    use crate::win32::{
        CreateJobObjectW, ExtendedLimit, Handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, SetInformationJobObject,
    };
    use std::sync::{Mutex, OnceLock};

    /// pid → job handle (as usize: a raw pointer is not Send). Live
    /// children only — `release` takes them out.
    pub fn jobs() -> &'static Mutex<Vec<(i32, usize)>> {
        static J: OnceLock<Mutex<Vec<(i32, usize)>>> = OnceLock::new();
        J.get_or_init(|| Mutex::new(Vec::new()))
    }

    /// An anonymous, non-inheritable job that kills its members when its
    /// last handle closes.
    pub fn new_job() -> Option<Handle> {
        let job = unsafe { CreateJobObjectW(std::ptr::null_mut(), std::ptr::null()) };
        if job.is_null() {
            return None;
        }
        let mut info = ExtendedLimit::default();
        info.basic.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = unsafe {
            SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                (&raw const info).cast(),
                std::mem::size_of::<ExtendedLimit>() as u32,
            )
        };
        if set == 0 {
            unsafe { crate::win32::CloseHandle(job) };
            return None;
        }
        Some(job)
    }
}

/// Is `pid` still running? `kill(pid, 0)` on Unix; on Windows, a process
/// that can be opened and has not reported an exit code. For the tests
/// that check a grandchild really died — a pid can be reused, so it is
/// only ever asked about a process the test just watched start.
#[cfg(all(test, unix))]
pub fn alive(pid: i32) -> bool {
    unsafe { kill(pid, 0) == 0 }
}

#[cfg(all(test, windows))]
pub fn alive(pid: i32) -> bool {
    use crate::win32::{
        CloseHandle, GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        STILL_ACTIVE,
    };
    let h = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid as u32) };
    if h.is_null() {
        return false;
    }
    let mut code = 0u32;
    let ok = unsafe { GetExitCodeProcess(h, &mut code) };
    unsafe { CloseHandle(h) };
    ok != 0 && code == STILL_ACTIVE
}
