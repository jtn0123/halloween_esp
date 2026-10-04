//! The first-launch setup — and the update's, and Repair's — run once
//! however many servers are waiting on it. Both supervisors resolve their
//! runtime through here; when the app's own runtime is not ready
//! (bundle.rs `plan`), each asks `ensure`, and the first to ask runs the
//! installer while the other waits for the same outcome. The splash reads
//! `progress` for the step it is on and, when it fails, why.
//!
//! A failure is the answer for the rest of that start: the second server
//! is not handed a second download that will fail the same way. Try again
//! (`arm`) clears it; Repair (`request_repair`) also sets the runtime up
//! again with the installer's `--repair` even when it looks ready.

use crate::bundle::{self, Plan, Reason};
use crate::logfile::LogFile;
use crate::runtime::{self, Env, Places, Resolved};
use crate::settings::Settings;
use crate::setup_run::{Job, Runner, Sink};
use serde::Serialize;
use std::panic::AssertUnwindSafe;
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::time::Instant;

/// What `ensure` answers a server whose start was stopped or restarted.
pub const SUPERSEDED: &str = "setup superseded";

/// Where the setup is, for the splash.
#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum Progress {
    Idle,
    Running {
        reason: Reason,
        step: u32,
        steps: u32,
        what: String,
    },
    Failed {
        reason: Reason,
        message: String,
    },
    Done {
        reason: Reason,
    },
}

struct Outcome {
    run: u64,
    result: Result<(), String>,
    cancelled: bool,
}

struct State {
    progress: Progress,
    running: bool,
    runs: u64,
    last: Option<Outcome>,
    repair: bool,
    job: Option<Arc<Job>>,
}

pub struct Setup {
    places: Places,
    runner: Box<dyn Runner>,
    log: Arc<LogFile>,
    state: Mutex<State>,
    finished: Condvar,
}

impl Setup {
    pub fn new(places: Places, runner: Box<dyn Runner>, log: Arc<LogFile>) -> Arc<Self> {
        Arc::new(Self {
            places,
            runner,
            log,
            state: Mutex::new(State {
                progress: Progress::Idle,
                running: false,
                runs: 0,
                last: None,
                repair: false,
                job: None,
            }),
            finished: Condvar::new(),
        })
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    pub fn progress(&self) -> Progress {
        self.lock().progress.clone()
    }

    /// Whether this app carries its own tools, so Repair means something.
    pub fn repairable(&self) -> bool {
        self.places
            .resource_dir
            .as_deref()
            .is_some_and(|r| r.join(bundle::CASTLE).join(bundle::ABOUT).is_file())
    }

    pub fn resolve(&self, settings: &Settings, env: Env<'_>) -> Result<Resolved, String> {
        let repair = self.lock().repair;
        runtime::resolve(&self.places, settings, env, repair)
    }

    /// A new start (Try again, the tray's Start, a castle-tools:// link):
    /// the last start's failure is no longer the answer.
    pub fn arm(&self) {
        let mut st = self.lock();
        if !st.running {
            st.last = None;
            if matches!(st.progress, Progress::Failed { .. }) {
                st.progress = Progress::Idle;
            }
        }
    }

    /// The owner's Repair: the next setup runs with --repair, ready or not.
    pub fn request_repair(&self) {
        self.lock().repair = true;
    }

    /// Stop a setup that is running (Quit, an update): its installer and
    /// everything it started. The next launch resumes it.
    pub fn stop(&self) {
        let job = self.lock().job.clone();
        if let Some(job) = job {
            self.log.line("setup: stopping");
            job.stop();
        }
    }

    /// Run `plan` — or wait for the run already under way and share its
    /// outcome. `wanted` is the caller's "am I still the current start?".
    pub fn ensure(&self, plan: &Plan, wanted: &dyn Fn() -> bool) -> Result<(), String> {
        let mut st = self.lock();
        loop {
            if !wanted() {
                return Err(SUPERSEDED.into());
            }
            if st.running {
                let run = st.runs;
                while st.running && st.runs == run {
                    st = self
                        .finished
                        .wait(st)
                        .unwrap_or_else(PoisonError::into_inner);
                }
                match &st.last {
                    Some(o) if o.run == run && !o.cancelled => return o.result.clone(),
                    _ => continue,
                }
            }
            match &st.last {
                Some(o) if o.result.is_err() && !o.cancelled => return o.result.clone(),
                _ => break,
            }
        }
        st.runs += 1;
        let run = st.runs;
        let job = Arc::new(Job::default());
        st.running = true;
        st.job = Some(Arc::clone(&job));
        st.progress = Progress::Running {
            reason: plan.reason,
            step: 0,
            steps: 0,
            what: plan.reason.waiting().into(),
        };
        drop(st);
        let result = std::panic::catch_unwind(AssertUnwindSafe(|| self.run_once(plan, &job)))
            .unwrap_or_else(|_| {
                Err("Setup stopped unexpectedly. Open the log for the reason.".into())
            });
        let cancelled = job.stopped();
        let mut st = self.lock();
        st.running = false;
        st.job = None;
        st.progress = match (&result, cancelled) {
            (_, true) => Progress::Idle,
            (Ok(()), false) => Progress::Done {
                reason: plan.reason,
            },
            (Err(message), false) => Progress::Failed {
                reason: plan.reason,
                message: message.clone(),
            },
        };
        if result.is_ok() && plan.reason == Reason::Repair {
            st.repair = false;
        }
        st.last = Some(Outcome {
            run,
            result: result.clone(),
            cancelled,
        });
        drop(st);
        self.finished.notify_all();
        result
    }

    fn run_once(&self, plan: &Plan, job: &Job) -> Result<(), String> {
        self.log.line(&format!(
            "setup ({:?}): {} from the {} bundle at {}",
            plan.reason,
            plan.runtime.root.display(),
            plan.bundle.tag,
            plan.bundle.dir.display()
        ));
        let started = Instant::now();
        plan.begin()
            .map_err(|e| format!("Could not prepare {}: {e}", plan.runtime.root.display()))?;
        let sink = Reporter { setup: self, plan };
        let ran = self.runner.run(plan, job, &sink).and_then(|()| {
            plan.finish()
                .map_err(|e| format!("Could not finish setting up: {e}"))
        });
        match &ran {
            Ok(()) => self.log.line(&format!(
                "setup finished in {}s",
                started.elapsed().as_secs()
            )),
            Err(message) => self.log.line(&format!("setup failed: {message}")),
        }
        ran
    }
}

struct Reporter<'a> {
    setup: &'a Setup,
    plan: &'a Plan,
}

impl Sink for Reporter<'_> {
    fn step(&self, step: u32, steps: u32, what: &str) {
        self.setup
            .log
            .line(&format!("setup step {step}/{steps}: {what}"));
        let mut st = self.setup.lock();
        if st.running {
            st.progress = Progress::Running {
                reason: self.plan.reason,
                step,
                steps,
                what: what.to_owned(),
            };
        }
    }

    fn line(&self, text: &str) {
        self.setup.log.line(&format!("setup: {text}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bundle::fake_bundle;
    use crate::setup_run::{Does, FakeRunner};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::time::Duration;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("castle-su-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    struct World {
        base: PathBuf,
        runs: Arc<AtomicU32>,
        repairs: Arc<AtomicU32>,
    }

    impl World {
        fn new(name: &str) -> World {
            let base = scratch(name);
            fake_bundle(&base.join("res"), "s1");
            World {
                base,
                runs: Arc::default(),
                repairs: Arc::default(),
            }
        }

        /// One app launch: a Setup over the same dirs every time.
        fn launch(&self, does: Does) -> Arc<Setup> {
            let places = Places {
                resource_dir: Some(self.base.join("res")),
                runtime_dir: self.base.join("runtime"),
                data_dir: self.base.join("data/radio"),
            };
            let log = Arc::new(LogFile::open(&self.base.join("logs"), "t.log").unwrap());
            let runner = FakeRunner {
                does,
                runs: Arc::clone(&self.runs),
                repairs: Arc::clone(&self.repairs),
            };
            Setup::new(places, Box::new(runner), log)
        }

        fn runs(&self) -> u32 {
            self.runs.load(Ordering::SeqCst)
        }
    }

    impl Drop for World {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.base);
        }
    }

    fn plan_of(setup: &Setup) -> Option<Plan> {
        match setup.resolve(&Settings::default(), &|_| None).unwrap() {
            Resolved::Setup(plan) => Some(plan),
            Resolved::Ready(_) => None,
        }
    }

    fn yes() -> bool {
        true
    }

    /// Both servers, at once, the way the supervisors start.
    fn both(setup: &Arc<Setup>) -> [Result<(), String>; 2] {
        let threads = [0, 1].map(|_| {
            let setup = Arc::clone(setup);
            std::thread::spawn(move || match plan_of(&setup) {
                Some(plan) => setup.ensure(&plan, &yes),
                None => Ok(()),
            })
        });
        threads.map(|t| t.join().unwrap())
    }

    #[test]
    fn the_first_launch_sets_up_once_and_the_second_not_at_all() {
        let world = World::new("first");
        let setup = world.launch(Does::Install);
        assert!(setup.repairable());
        assert_eq!(plan_of(&setup).unwrap().reason, Reason::FirstRun);
        assert_eq!(both(&setup), [Ok(()), Ok(())]);
        assert_eq!(world.runs(), 1, "two servers, one installer");
        assert_eq!(
            setup.progress(),
            Progress::Done {
                reason: Reason::FirstRun
            }
        );
        assert!(plan_of(&setup).is_none(), "ready now");
        let again = world.launch(Does::Install);
        assert!(
            plan_of(&again).is_none(),
            "a second launch does not reinstall"
        );
        assert_eq!(world.runs(), 1);
    }

    #[test]
    fn a_failure_is_the_answer_until_try_again() {
        let world = World::new("fail");
        let setup = world.launch(Does::Fail);
        let [a, b] = both(&setup);
        assert_eq!(
            (a.unwrap_err(), b.unwrap_err()),
            ("no internet".into(), "no internet".into())
        );
        assert_eq!(world.runs(), 1);
        assert_eq!(
            setup.progress(),
            Progress::Failed {
                reason: Reason::FirstRun,
                message: "no internet".into()
            }
        );
        // The same start, asking again: the same answer, no second download.
        let plan = plan_of(&setup).unwrap();
        assert!(setup.ensure(&plan, &yes).is_err());
        assert_eq!(world.runs(), 1);
        setup.arm();
        assert_eq!(setup.progress(), Progress::Idle);
        assert!(setup.ensure(&plan, &yes).is_err());
        assert_eq!(world.runs(), 2, "Try again runs it again");
        // The runtime the failed run began is unfinished, not first-run.
        std::fs::create_dir_all(world.base.join("runtime/python")).unwrap();
        assert_eq!(plan_of(&setup).unwrap().reason, Reason::Unfinished);
    }

    #[test]
    fn repair_sets_a_ready_runtime_up_again_once() {
        let world = World::new("repair");
        let setup = world.launch(Does::Install);
        assert_eq!(both(&setup), [Ok(()), Ok(())]);
        setup.request_repair();
        setup.arm();
        let plan = plan_of(&setup).expect("Repair overrides ready");
        assert_eq!(plan.reason, Reason::Repair);
        setup.ensure(&plan, &yes).unwrap();
        assert_eq!(world.repairs.load(Ordering::SeqCst), 1, "--repair passed");
        assert!(plan_of(&setup).is_none(), "repaired, and the request spent");
    }

    #[test]
    fn an_update_sets_up_again_from_the_new_bundle() {
        let world = World::new("update");
        let setup = world.launch(Does::Install);
        assert_eq!(both(&setup), [Ok(()), Ok(())]);
        fake_bundle(&world.base.join("res"), "s2");
        let updated = world.launch(Does::Install);
        assert_eq!(plan_of(&updated).unwrap().reason, Reason::Update);
        assert_eq!(both(&updated), [Ok(()), Ok(())]);
        assert_eq!(world.runs(), 2);
        assert!(plan_of(&updated).is_none());
    }

    #[test]
    fn stop_ends_a_run_and_the_next_start_runs_anew() {
        let world = World::new("stop");
        let setup = world.launch(Does::Hang);
        let plan = plan_of(&setup).unwrap();
        let running = {
            let (setup, plan) = (Arc::clone(&setup), plan.clone());
            std::thread::spawn(move || setup.ensure(&plan, &yes))
        };
        while !matches!(setup.progress(), Progress::Running { step: 2, .. }) {
            std::thread::sleep(Duration::from_millis(5));
        }
        setup.stop();
        assert!(running.join().unwrap().is_err());
        assert_eq!(setup.progress(), Progress::Idle, "stopped is not failed");
        let fresh = {
            let setup = Arc::clone(&setup);
            std::thread::spawn(move || setup.ensure(&plan, &yes))
        };
        while world.runs() < 2 {
            std::thread::sleep(Duration::from_millis(5));
        }
        setup.stop();
        assert!(fresh.join().unwrap().is_err());
    }

    #[test]
    fn a_superseded_start_does_not_run_it() {
        let world = World::new("super");
        let setup = world.launch(Does::Install);
        let plan = plan_of(&setup).unwrap();
        assert_eq!(setup.ensure(&plan, &|| false), Err(SUPERSEDED.into()));
        assert_eq!(world.runs(), 0);
    }
}
