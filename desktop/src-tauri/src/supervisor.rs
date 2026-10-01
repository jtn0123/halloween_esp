//! Starts a server once, reuses one that is already running, notices when
//! ours dies, and stops only what it started — the rules the Swift launcher
//! and `Open Castle Studio.command` kept, in one place. One Supervisor per
//! server (service.rs): Castle Radio, and the cue desk studio beside it.

use crate::child::Tree;
use crate::logfile::LogFile;
use crate::probe::{self, Probe};
use crate::runtime::{self, DataDirs, Runtime};
use crate::service::Service;
use crate::settings::Settings;
use serde::Serialize;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

/// How long a cold start may take before it is called a failure. The first
/// import of numpy/scipy from a fresh bundle on a slow laptop is the cost.
const START_TIMEOUT: Duration = Duration::from_secs(90);
const POLL: Duration = Duration::from_millis(250);
const WATCH: Duration = Duration::from_secs(1);
/// A reused server is someone else's; asking it every few seconds is enough.
const REUSED_WATCH: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum Status {
    Idle,
    Starting { detail: String },
    Running { url: String, owned: bool },
    Failed { message: String },
}

/// What the supervisor tells the UI layer; the UI decides what to show.
pub trait Events: Send + Sync + 'static {
    fn ready(&self, url: &str);
    fn failed(&self, message: &str);
}

pub struct Config {
    pub service: Service,
    pub port: u16,
    pub resource_dir: Option<PathBuf>,
    pub app_data: PathBuf,
    pub settings: Settings,
}

struct Inner {
    status: Status,
    child: Option<Tree>,
    /// Bumped by every start and stop; a watcher from an older generation
    /// sees the change and exits instead of reporting on a world it no
    /// longer owns.
    generation: u64,
}

pub struct Supervisor {
    config: Config,
    log: Arc<LogFile>,
    events: Box<dyn Events>,
    inner: Mutex<Inner>,
}

impl Supervisor {
    pub fn new(config: Config, log: Arc<LogFile>, events: Box<dyn Events>) -> Arc<Self> {
        Arc::new(Self {
            config,
            log,
            events,
            inner: Mutex::new(Inner {
                status: Status::Idle,
                child: None,
                generation: 0,
            }),
        })
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn name(&self) -> &'static str {
        self.config.service.name()
    }

    fn title(&self) -> &'static str {
        self.config.service.title()
    }

    fn probe(&self) -> Probe {
        probe::identify(self.config.port, self.config.service.identity())
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.config.port)
    }

    pub fn status(&self) -> Status {
        self.lock().status.clone()
    }

    pub fn log_path(&self) -> PathBuf {
        self.log.path().to_path_buf()
    }

    pub fn log_line(&self, line: &str) {
        self.log.line(line);
    }

    /// Start, unless a start is under way or a server is already up. Returns
    /// at once; the work happens on its own thread.
    pub fn start(self: &Arc<Self>) {
        let generation = {
            let mut inner = self.lock();
            match &inner.status {
                Status::Starting { .. } | Status::Running { .. } => return,
                Status::Idle | Status::Failed { .. } => {}
            }
            inner.generation += 1;
            inner.status = Status::Starting {
                detail: format!("Looking for {}…", self.name()),
            };
            inner.generation
        };
        let me = Arc::clone(self);
        std::thread::spawn(move || me.run(generation));
    }

    /// Stop what we started; leave a reused server alone. Safe to call twice.
    pub fn stop(&self) {
        let child = {
            let mut inner = self.lock();
            inner.generation += 1;
            inner.status = Status::Idle;
            inner.child.take()
        };
        if let Some(mut tree) = child {
            self.log
                .line(&format!("stopping {} (pid {})", self.name(), tree.pid()));
            tree.kill_tree();
        }
    }

    /// Set the status, unless a newer start/stop has taken over.
    fn set(&self, generation: u64, status: Status) -> bool {
        let mut inner = self.lock();
        if inner.generation != generation {
            return false;
        }
        inner.status = status;
        true
    }

    fn fail(&self, generation: u64, message: String) {
        self.log.line(&message);
        if self.set(
            generation,
            Status::Failed {
                message: message.clone(),
            },
        ) {
            self.events.failed(&message);
        }
    }

    fn running(&self, generation: u64, owned: bool) -> bool {
        let url = self.url();
        let current = self.set(
            generation,
            Status::Running {
                url: url.clone(),
                owned,
            },
        );
        if current {
            self.events.ready(&url);
        }
        current
    }

    fn run(&self, generation: u64) {
        let port = self.config.port;
        match self.probe() {
            Probe::Ours => {
                self.log.line(&format!("reusing {} already on port {port}", self.name()));
                self.running(generation, false);
                self.watch_reused(generation);
                return;
            }
            Probe::Other => {
                return self.fail(
                    generation,
                    format!(
                        "Port {port}, which {} needs, is being used by another app. Close it, then try again.",
                        self.name()
                    ),
                )
            }
            Probe::Nothing => {}
        }
        let rt = match runtime::resolve(
            self.config.resource_dir.as_deref(),
            &self.config.settings,
            &|k| std::env::var_os(k),
        ) {
            Ok(rt) => rt,
            Err(message) => return self.fail(generation, message),
        };
        if let Err(message) = self.spawn(generation, &rt) {
            return self.fail(generation, message);
        }
        self.wait_ready(generation);
    }

    fn spawn(&self, generation: u64, rt: &Runtime) -> Result<(), String> {
        let launch = self.config.service.launch(rt, self.config.port)?;
        let data = DataDirs::new(&self.config.app_data);
        std::fs::create_dir_all(&data.radio)
            .map_err(|e| format!("Could not create {}: {e}", data.radio.display()))?;
        match runtime::seed_scenes(rt, &data) {
            Ok(true) => self
                .log
                .line(&format!("first run: seeded {}", data.scenes().display())),
            Ok(false) => {}
            Err(e) => self
                .log
                .line(&format!("could not seed {}: {e}", data.scenes().display())),
        }
        let out = self.log.handle().map_err(|e| format!("log: {e}"))?;
        let err = out.try_clone().map_err(|e| format!("log: {e}"))?;
        let mut cmd = Command::new(&launch.program);
        // The tree's root is the cwd for both: Castle Radio imports its
        // siblings from there, and the studio finds tools/ by walking up
        // from it (core/src/studio.rs repo_root).
        cmd.args(&launch.args)
            .current_dir(&rt.root)
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err);
        for (key, value) in
            runtime::child_env(rt, &data, &self.config.settings, std::env::var_os("PATH"))
        {
            cmd.env(key, value);
        }
        self.log.line(&format!(
            "starting {} from the {} at {}: {} on port {}",
            self.name(),
            rt.source.describe(),
            rt.root.display(),
            launch.program.display(),
            self.config.port
        ));
        let tree = Tree::spawn(cmd)
            .map_err(|e| format!("Could not start {}: {e}", launch.program.display()))?;
        let mut inner = self.lock();
        if inner.generation != generation {
            drop(inner);
            let mut tree = tree;
            tree.kill_tree();
            return Err("start superseded".into());
        }
        inner.status = Status::Starting {
            detail: format!("Starting {}…", self.name()),
        };
        inner.child = Some(tree);
        Ok(())
    }

    /// Poll our child until it answers as its server, then watch it.
    fn wait_ready(&self, generation: u64) {
        let deadline = Instant::now() + START_TIMEOUT;
        loop {
            match self.child_exit(generation) {
                Some(Ok(Some(code))) => {
                    return self.fail(
                        generation,
                        format!(
                            "{} could not start ({code}). Open the log for the reason.",
                            self.title()
                        ),
                    )
                }
                Some(Ok(None)) => {}
                Some(Err(_)) | None => return, // superseded
            }
            if self.probe() == Probe::Ours {
                // Only a start that is still current says so: a Quit that
                // landed mid-poll has already logged the stop.
                if self.running(generation, true) {
                    self.log.line(&format!("{} is up", self.title()));
                }
                return self.watch_owned(generation);
            }
            if Instant::now() > deadline {
                self.stop_child(generation);
                return self.fail(
                    generation,
                    format!(
                        "{} did not answer in time. Open the log for the reason.",
                        self.title()
                    ),
                );
            }
            std::thread::sleep(POLL);
        }
    }

    /// None when superseded; otherwise the child's exit, if it has exited.
    fn child_exit(&self, generation: u64) -> Option<Result<Option<String>, ()>> {
        let mut inner = self.lock();
        if inner.generation != generation {
            return None;
        }
        let tree = inner.child.as_mut()?;
        Some(match tree.try_wait() {
            Ok(Some(status)) => Ok(Some(status.to_string())),
            Ok(None) => Ok(None),
            Err(_) => Err(()),
        })
    }

    fn stop_child(&self, generation: u64) {
        let child = {
            let mut inner = self.lock();
            if inner.generation != generation {
                return;
            }
            inner.child.take()
        };
        if let Some(mut tree) = child {
            tree.kill_tree();
        }
    }

    fn watch_owned(&self, generation: u64) {
        loop {
            std::thread::sleep(WATCH);
            match self.child_exit(generation) {
                Some(Ok(Some(code))) => {
                    self.lock().child = None;
                    return self.fail(
                        generation,
                        format!(
                            "{} stopped unexpectedly ({code}). Open the log, then try again.",
                            self.title()
                        ),
                    );
                }
                Some(Ok(None)) => {}
                _ => return,
            }
        }
    }

    fn watch_reused(&self, generation: u64) {
        loop {
            std::thread::sleep(REUSED_WATCH);
            if self.lock().generation != generation {
                return;
            }
            if self.probe() != Probe::Ours {
                return self.fail(
                    generation,
                    format!(
                        "The copy of {} that was already running has stopped. Try again to start a new one.",
                        self.name()
                    ),
                );
            }
        }
    }
}
