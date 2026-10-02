//! A child process together with everything it starts, killed as one.
//!
//! The server spawns importers, ffmpeg, yt-dlp and Demucs; quitting the app
//! must not leave any of them running. Unix: the child leads its own process
//! group and the whole group is signalled. Windows: the child is placed in a
//! job object with KILL_ON_JOB_CLOSE, so the tree dies when the job closes —
//! including when the app itself crashes, which no Unix mechanism here covers
//! (a crashed app on macOS leaves the server running; the next launch finds
//! and reuses it through the identity probe rather than starting a second).

use std::io;
use std::process::{Child, Command, ExitStatus};
use std::time::{Duration, Instant};

pub struct Tree {
    child: Child,
    #[cfg(windows)]
    job: win::Job,
}

impl Tree {
    pub fn spawn(mut cmd: Command) -> io::Result<Tree> {
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(win::CREATE_NO_WINDOW);
            let job = win::Job::new()?;
            let child = cmd.spawn()?;
            if let Err(e) = job.assign(&child) {
                let mut child = child;
                let _ = child.kill();
                return Err(e);
            }
            return Ok(Tree { child, job });
        }
        #[cfg(not(windows))]
        Ok(Tree {
            child: cmd.spawn()?,
        })
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    pub fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        self.child.try_wait()
    }

    /// Ask nicely, wait a moment, then make sure. Safe to call twice.
    pub fn kill_tree(&mut self) {
        #[cfg(unix)]
        {
            let group = -(self.child.id() as libc::pid_t);
            // SAFETY: kill(2) with a negative pid signals that process group;
            // the group is the one this Tree created, and an already-empty
            // group just returns ESRCH.
            unsafe { libc::kill(group, libc::SIGTERM) };
            let deadline = Instant::now() + Duration::from_secs(3);
            while Instant::now() < deadline {
                if matches!(self.child.try_wait(), Ok(Some(_))) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            // Grandchildren can outlive the leader; the group outlives it too.
            // SAFETY: as above.
            unsafe { libc::kill(group, libc::SIGKILL) };
        }
        #[cfg(windows)]
        self.job.terminate();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(windows)]
mod win {
    use std::io;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    pub use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

    /// The job handle, as an integer so the Tree stays Send.
    pub struct Job(isize);

    impl Job {
        pub fn new() -> io::Result<Job> {
            // SAFETY: plain Win32 calls with valid pointers; the handle is
            // owned by the returned Job and closed exactly once in Drop.
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let job = Job(handle as isize);
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    job.handle(),
                    JobObjectExtendedLimitInformation,
                    std::ptr::addr_of!(info).cast(),
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(job)
            }
        }

        fn handle(&self) -> HANDLE {
            self.0 as HANDLE
        }

        pub fn assign(&self, child: &Child) -> io::Result<()> {
            // SAFETY: both handles are live for the duration of the call.
            let ok =
                unsafe { AssignProcessToJobObject(self.handle(), child.as_raw_handle() as HANDLE) };
            if ok == 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(())
            }
        }

        pub fn terminate(&self) {
            // SAFETY: the handle is live until Drop.
            unsafe { TerminateJobObject(self.handle(), 1) };
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // SAFETY: closing the last handle kills the tree (KILL_ON_JOB_CLOSE).
            unsafe { CloseHandle(self.handle()) };
        }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn kills_grandchildren_too() {
        // sh starts a background sleep (the grandchild) and prints its pid.
        let mut cmd = Command::new("/bin/sh");
        cmd.args(["-c", "sleep 30 & echo $! ; wait"]);
        cmd.stdout(std::process::Stdio::piped());
        let mut tree = Tree::spawn(cmd).unwrap();
        let mut out = tree.child.stdout.take().unwrap();
        let mut line = String::new();
        use std::io::Read;
        let mut byte = [0u8; 1];
        while out.read(&mut byte).unwrap() == 1 && byte[0] != b'\n' {
            line.push(byte[0] as char);
        }
        let grandchild: libc::pid_t = line.trim().parse().unwrap();
        tree.kill_tree();
        std::thread::sleep(Duration::from_millis(100));
        // SAFETY: signal 0 only checks for existence.
        let alive = unsafe { libc::kill(grandchild, 0) } == 0;
        assert!(!alive, "grandchild {grandchild} survived");
        assert!(tree.try_wait().unwrap().is_some());
    }
}
