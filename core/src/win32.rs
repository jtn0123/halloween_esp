//! The handful of kernel32 calls the native half needs on Windows, declared
//! by hand so the crate stays zero-dependency (Cargo.lock has nothing in it,
//! on purpose — see Cargo.toml). Each one is the Windows twin of a Unix call
//! the crate already makes through its own two-line extern:
//!
//! - `LockFileEx` / `UnlockFileEx` — flock(2), for the tracks.json lock
//!   ([`manifest`](crate::manifest)).
//! - the job-object calls — process groups and `kill(-pgid, SIGKILL)`, so a
//!   watchdog takes yt-dlp's ffmpeg down with it
//!   ([`procgroup`](crate::procgroup)).
//! - `OpenProcess` / `GetExitCodeProcess` — `kill(pid, 0)`, for the tests
//!   that check a grandchild is really gone.
//!
//! Layouts are the x64/arm64 ones from the Windows SDK headers (`minwinbase.h`,
//! `winnt.h`); `#[repr(C)]` with the same field types gives the same padding.
//! Nothing here is compiled anywhere but Windows, and nothing here has run
//! on Windows from this repo's CI — `cargo check --target
//! x86_64-pc-windows-msvc` is what vouches for it.

use std::ffi::c_void;

pub type Handle = *mut c_void;

/// `OVERLAPPED` — only its offset fields matter to a lock of the whole
/// file, and they are zero: the lock starts at byte 0.
#[repr(C)]
#[derive(Default)]
pub struct Overlapped {
    pub internal: usize,
    pub internal_high: usize,
    pub offset: u32,
    pub offset_high: u32,
    pub h_event: usize,
}

/// `JOBOBJECT_BASIC_LIMIT_INFORMATION`.
#[repr(C)]
#[derive(Default)]
pub struct BasicLimit {
    pub per_process_user_time_limit: i64,
    pub per_job_user_time_limit: i64,
    pub limit_flags: u32,
    pub minimum_working_set_size: usize,
    pub maximum_working_set_size: usize,
    pub active_process_limit: u32,
    pub affinity: usize,
    pub priority_class: u32,
    pub scheduling_class: u32,
}

/// `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` — the class that carries
/// `KILL_ON_JOB_CLOSE`; the basic one cannot.
#[repr(C)]
#[derive(Default)]
pub struct ExtendedLimit {
    pub basic: BasicLimit,
    pub io_info: [u64; 6],
    pub process_memory_limit: usize,
    pub job_memory_limit: usize,
    pub peak_process_memory_used: usize,
    pub peak_job_memory_used: usize,
}

pub const LOCKFILE_FAIL_IMMEDIATELY: u32 = 0x1;
pub const LOCKFILE_EXCLUSIVE_LOCK: u32 = 0x2;
/// `JobObjectExtendedLimitInformation` in `JOBOBJECTINFOCLASS`.
pub const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;
pub const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x2000;
pub const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
/// `STILL_ACTIVE` — what GetExitCodeProcess reports for a live process.
pub const STILL_ACTIVE: u32 = 259;

#[link(name = "kernel32")]
unsafe extern "system" {
    pub fn LockFileEx(
        file: Handle,
        flags: u32,
        reserved: u32,
        bytes_low: u32,
        bytes_high: u32,
        overlapped: *mut Overlapped,
    ) -> i32;
    pub fn UnlockFileEx(
        file: Handle,
        reserved: u32,
        bytes_low: u32,
        bytes_high: u32,
        overlapped: *mut Overlapped,
    ) -> i32;
    pub fn CreateJobObjectW(attributes: *mut c_void, name: *const u16) -> Handle;
    pub fn SetInformationJobObject(job: Handle, class: i32, info: *const c_void, len: u32) -> i32;
    pub fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
    pub fn TerminateJobObject(job: Handle, exit_code: u32) -> i32;
    pub fn CloseHandle(handle: Handle) -> i32;
    pub fn OpenProcess(access: u32, inherit: i32, pid: u32) -> Handle;
    pub fn GetExitCodeProcess(process: Handle, code: *mut u32) -> i32;
}
