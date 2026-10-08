//! Children for the process tests, in a language every platform has.
//!
//! The watchdog, the job runner and the shutdown reaper are tested against
//! real child processes, and those used to be `/bin/sh -c '…'`, `sleep`,
//! `cat` and `wc` — none of which a Windows machine has. They are Python
//! now, run by the interpreter the studio itself would pick
//! ([`py`](crate::studio_proc::py)): `CASTLE_PY`, the project venv, or the
//! `python3`/`python` on PATH. Every snippet is the standard library only,
//! so a bare system Python (the CI rust job's) runs them as well as the venv.
//!
//! Compiled only under `cargo test`, like [`testkit`](crate::testkit).

use std::path::Path;
use std::process::Command;

/// The interpreter, resolved the way the studio resolves it for this repo.
pub fn python() -> String {
    crate::studio_proc::py(&crate::studio::repo_root())
}

/// `python -c code` as an argv, for the job runner (which takes one).
pub fn python_argv(code: &str) -> Vec<String> {
    vec![python(), "-c".to_string(), code.to_string()]
}

/// `python -c code` as a Command, for `studio_proc::run` and friends.
pub fn python_cmd(code: &str) -> Command {
    let mut c = Command::new(python());
    c.args(["-c", code]);
    c
}

/// `sh -c 'sleep N & echo $! > pidfile; wait'`: a child that starts a
/// grandchild sleeping `secs`, reports the grandchild's pid through
/// `pidfile` (written whole, then renamed, so a reader never sees half a
/// number), and waits for it. The grandchild inherits the child's stdout
/// and stderr, so it holds the pipe the way yt-dlp's ffmpeg does — which is
/// the case the group kill exists for.
pub fn spawner(pidfile: &Path, secs: u32) -> Command {
    const CODE: &str = "import os, subprocess, sys\n\
        p = subprocess.Popen([sys.executable, '-c', \
        'import time; time.sleep(' + sys.argv[2] + ')'])\n\
        with open(sys.argv[1] + '.tmp', 'w') as f:\n    f.write(str(p.pid))\n\
        os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n\
        p.wait()\n";
    let mut c = python_cmd(CODE);
    c.arg(pidfile).arg(secs.to_string());
    c
}
