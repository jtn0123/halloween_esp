//! The setup's processes. uv fetches Python 3.13 into the runtime, then the
//! bundle's own `tools/desktop_install.py` runs under it with `--progress`,
//! and its `@castle-step` lines (tools/desktop_progress.py) become the
//! splash's progress. Every line any of them prints goes to the app's log.
//! Each runs as a child::Tree, so Quit stops the installer and everything
//! it started. setup.rs decides when a setup runs; this is how.

use crate::bundle::Plan;
use crate::child::Tree;
use crate::setup_cmd::{Cmd, SCRUB};
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Command, ExitStatus, Stdio};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::Duration;

/// tools/desktop_progress.py's markers; tests/test_desktop_bundle.py holds
/// the two files equal.
pub const STEP_MARK: &str = "@castle-step";
pub const FAILED_MARK: &str = "@castle-failed";

const STOPPED: &str = "Setup was stopped.";

/// What a runner reports as it goes.
pub trait Sink: Sync {
    fn step(&self, step: u32, steps: u32, what: &str);
    fn line(&self, text: &str);
}

/// Runs a plan's commands: `Processes` for real; setup.rs's tests' own lay
/// out a fake install, or fail, or wait to be stopped.
pub trait Runner: Send + Sync + 'static {
    fn run(&self, plan: &Plan, job: &Job, sink: &dyn Sink) -> Result<(), String>;
}

/// One line of the installer's output, read.
#[derive(Debug, PartialEq, Eq)]
pub enum Line<'a> {
    Step {
        step: u32,
        steps: u32,
        what: &'a str,
    },
    Failed(&'a str),
    Other,
}

/// `@castle-step N/M what`, `@castle-failed why`, or anything else.
pub fn parse(line: &str) -> Line<'_> {
    if let Some(rest) = line
        .strip_prefix(STEP_MARK)
        .and_then(|r| r.strip_prefix(' '))
    {
        let (count, what) = rest.split_once(' ').unwrap_or((rest, ""));
        if let Some((n, m)) = count.split_once('/') {
            if let (Ok(step), Ok(steps)) = (n.parse(), m.parse()) {
                return Line::Step {
                    step,
                    steps,
                    what: what.trim(),
                };
            }
        }
        return Line::Other;
    }
    match line.strip_prefix(FAILED_MARK) {
        Some(why) if why.is_empty() || why.starts_with(' ') => Line::Failed(why.trim()),
        _ => Line::Other,
    }
}

/// The child a setup is running, killable from another thread.
#[derive(Default)]
pub struct Job {
    slot: Mutex<Slot>,
}

#[derive(Default)]
struct Slot {
    child: Option<Tree>,
    stopped: bool,
}

impl Job {
    fn lock(&self) -> MutexGuard<'_, Slot> {
        self.slot.lock().unwrap_or_else(PoisonError::into_inner)
    }

    pub fn stopped(&self) -> bool {
        self.lock().stopped
    }

    /// Kill the running child's tree; nothing starts after this.
    pub fn stop(&self) {
        let child = {
            let mut slot = self.lock();
            slot.stopped = true;
            slot.child.take()
        };
        if let Some(mut tree) = child {
            tree.kill_tree();
        }
    }

    /// Run `cmd` to its end: stdout lines to `each`, stderr lines to the
    /// log, under the plan's environment, from the runtime's folder.
    fn run(
        &self,
        plan: &Plan,
        cmd: &Cmd,
        sink: &dyn Sink,
        each: &mut dyn FnMut(&str),
    ) -> Result<ExitStatus, String> {
        let mut command = Command::new(&cmd.program);
        command
            .args(&cmd.args)
            .current_dir(&plan.runtime.root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for var in SCRUB {
            command.env_remove(var);
        }
        for (key, value) in plan.env() {
            command.env(key, value);
        }
        let (out, err) = {
            let mut slot = self.lock();
            if slot.stopped {
                return Err(STOPPED.into());
            }
            let mut tree = Tree::spawn(command)
                .map_err(|e| format!("Could not start {}: {e}", cmd.program.display()))?;
            let pipes = tree.take_pipes();
            slot.child = Some(tree);
            pipes
        };
        std::thread::scope(|scope| {
            if let Some(err) = err {
                scope.spawn(move || lines(err, &mut |l| sink.line(l)));
            }
            if let Some(out) = out {
                lines(out, each);
            }
        });
        self.wait()
    }

    fn wait(&self) -> Result<ExitStatus, String> {
        loop {
            {
                let mut slot = self.lock();
                let Some(tree) = slot.child.as_mut() else {
                    return Err(STOPPED.into());
                };
                match tree.try_wait() {
                    Ok(Some(status)) => {
                        slot.child = None;
                        return Ok(status);
                    }
                    Ok(None) => {}
                    Err(e) => return Err(e.to_string()),
                }
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}

/// Every line `from` prints, as text (a stray non-UTF-8 byte is replaced,
/// never a reason to stop reading).
fn lines(from: impl Read, each: &mut dyn FnMut(&str)) {
    let mut reader = BufReader::new(from);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => break,
            Ok(_) => each(String::from_utf8_lossy(&buf).trim_end_matches(['\r', '\n'])),
        }
    }
}

/// The real runner.
pub struct Processes;

impl Runner for Processes {
    fn run(&self, plan: &Plan, job: &Job, sink: &dyn Sink) -> Result<(), String> {
        sink.step(0, 0, "Getting Python 3.13");
        let got = job.run(plan, &plan.get_python(), sink, &mut |l| sink.line(l))?;
        if !got.success() {
            return Err(format!(
                "Python 3.13 could not be downloaded (uv: {got}) — the first start needs the internet"
            ));
        }
        let mut found = String::new();
        let status = job.run(plan, &plan.find_python(), sink, &mut |l| {
            sink.line(l);
            if found.is_empty() {
                found = l.trim().to_owned();
            }
        })?;
        let python = PathBuf::from(&found);
        if !status.success() || !python.is_file() {
            return Err(format!(
                "uv installed Python 3.13 but could not find it ({status})"
            ));
        }
        let mut failed: Option<String> = None;
        let mut doing = String::from("the start of the installer");
        let status = job.run(
            plan,
            &plan.install(&python),
            sink,
            &mut |l| match parse(l) {
                Line::Step { step, steps, what } => {
                    doing = format!("step {step} of {steps} ({what})");
                    sink.step(step, steps, what);
                }
                Line::Failed(why) => {
                    failed = Some(why.to_owned());
                    sink.line(l);
                }
                Line::Other => sink.line(l),
            },
        )?;
        if status.success() {
            return Ok(());
        }
        let why = failed.unwrap_or_else(|| format!("the installer stopped ({status})"));
        Err(format!("Setup stopped at {doing}: {why}"))
    }
}

/// What a `FakeRunner` comes to, once it has reported a step slowly enough
/// that a second caller is waiting by the time it ends.
#[cfg(test)]
#[derive(Clone, Copy, PartialEq)]
pub enum Does {
    Install,
    Fail,
    Hang,
}

/// A runner that runs no program (the tests' fixture): it counts its runs
/// and its repairs, then lays out a finished install, fails, or waits to be
/// stopped.
#[cfg(test)]
pub struct FakeRunner {
    pub does: Does,
    pub runs: std::sync::Arc<std::sync::atomic::AtomicU32>,
    pub repairs: std::sync::Arc<std::sync::atomic::AtomicU32>,
}

#[cfg(test)]
impl Runner for FakeRunner {
    fn run(&self, plan: &Plan, job: &Job, sink: &dyn Sink) -> Result<(), String> {
        use std::sync::atomic::Ordering;
        self.runs.fetch_add(1, Ordering::SeqCst);
        if plan
            .install(std::path::Path::new("py"))
            .args
            .iter()
            .any(|a| a == "--repair")
        {
            self.repairs.fetch_add(1, Ordering::SeqCst);
        }
        sink.step(2, 8, "Installing Python");
        std::thread::sleep(Duration::from_millis(150));
        match self.does {
            Does::Install => {
                crate::install_tree::fake_install(&plan.runtime.root);
                Ok(())
            }
            Does::Fail => Err("no internet".into()),
            Does::Hang => {
                while !job.stopped() {
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err("stopped".into())
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_installers_two_markers() {
        assert_eq!(
            parse("@castle-step 2/8 Installing Python and its packages"),
            Line::Step {
                step: 2,
                steps: 8,
                what: "Installing Python and its packages"
            }
        );
        assert_eq!(
            parse("@castle-failed uv stopped with status 2"),
            Line::Failed("uv stopped with status 2")
        );
        for other in [
            "$ uv pip sync",
            "@castle-step two/8 x",
            "@castle-stepper 1/2 x",
            "@castle-failedx",
            "",
        ] {
            assert_eq!(parse(other), Line::Other, "{other:?}");
        }
    }

    #[cfg(unix)]
    mod processes {
        use super::super::*;
        use crate::bundle::{self, fake_bundle};
        use crate::install_tree::InstallTree;
        use std::os::unix::fs::PermissionsExt;
        use std::path::Path;
        use std::sync::Arc;

        #[derive(Default)]
        struct Recorder {
            steps: Mutex<Vec<(u32, u32, String)>>,
            lines: Mutex<Vec<String>>,
        }

        impl Sink for Recorder {
            fn step(&self, step: u32, steps: u32, what: &str) {
                self.steps.lock().unwrap().push((step, steps, what.into()));
            }
            fn line(&self, text: &str) {
                self.lines.lock().unwrap().push(text.into());
            }
        }

        fn script(path: &Path, body: &str) {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, format!("#!/bin/sh\n{body}\n")).unwrap();
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
        }

        /// A bundle whose uv "installs" a python that runs `installer`.
        fn world(name: &str, installer: &str) -> (PathBuf, Plan) {
            let base =
                std::env::temp_dir().join(format!("castle-sr-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&base);
            let bundle = fake_bundle(&base.join("res"), "s1");
            let python = base.join("runtime/python/bin/python3");
            script(
                &bundle.uv(),
                &format!(
                    "echo \"uv $*\" >&2\n[ \"$2\" = find ] && echo {}\nexit 0",
                    python.display()
                ),
            );
            script(&python, installer);
            let plan = bundle::plan(
                bundle,
                InstallTree::new(base.join("runtime")),
                base.join("data"),
                false,
            )
            .into_plan()
            .unwrap();
            plan.begin().unwrap();
            (base, plan)
        }

        #[test]
        fn progress_and_success() {
            let (base, plan) = world(
                "ok",
                "echo \"@castle-step 1/2 Copying\"\necho \"cache=$UV_CACHE_DIR\"\necho \"@castle-step 2/2 Finishing\"",
            );
            let sink = Recorder::default();
            Processes.run(&plan, &Job::default(), &sink).unwrap();
            let steps = sink.steps.lock().unwrap().clone();
            assert_eq!(
                steps,
                vec![
                    (0, 0, "Getting Python 3.13".to_owned()),
                    (1, 2, "Copying".to_owned()),
                    (2, 2, "Finishing".to_owned())
                ]
            );
            let lines = sink.lines.lock().unwrap().clone();
            let cache = format!("cache={}", base.join("runtime/cache").display());
            assert!(lines.contains(&cache), "{lines:?}");
            assert!(
                lines
                    .iter()
                    .any(|l| l.starts_with("uv python install 3.13")),
                "{lines:?}"
            );
            let _ = std::fs::remove_dir_all(base);
        }

        #[test]
        fn a_failure_names_its_step_and_reason() {
            let (base, plan) = world(
                "fail",
                "echo \"@castle-step 2/8 Installing Python\"\necho \"@castle-failed uv stopped with status 2\"\nexit 1",
            );
            let err = Processes
                .run(&plan, &Job::default(), &Recorder::default())
                .unwrap_err();
            assert_eq!(
                err,
                "Setup stopped at step 2 of 8 (Installing Python): uv stopped with status 2"
            );
            let _ = std::fs::remove_dir_all(base);
        }

        #[test]
        fn stop_kills_the_installer() {
            let (base, plan) = world("stop", "echo \"@castle-step 1/8 Copying\"\nexec sleep 30");
            let job = Arc::new(Job::default());
            let sink = Arc::new(Recorder::default());
            let running = {
                let (job, sink, plan) = (Arc::clone(&job), Arc::clone(&sink), plan.clone());
                std::thread::spawn(move || Processes.run(&plan, &job, sink.as_ref()))
            };
            while sink.steps.lock().unwrap().len() < 2 {
                std::thread::sleep(Duration::from_millis(10));
            }
            let started = std::time::Instant::now();
            job.stop();
            assert_eq!(running.join().unwrap(), Err(STOPPED.into()));
            assert!(started.elapsed() < Duration::from_secs(5));
            let _ = std::fs::remove_dir_all(base);
        }
    }
}
