//! The two servers the app supervises, and what tells them apart: their
//! port, the answer that identifies them, and the command that starts them.
//!
//! * **Castle Radio** — the Python player + importer the window shows. Its
//!   port is fixed at 8871 because the castle's own page opens its companion
//!   popup on exactly that origin; `CASTLE_STUDIO_PORT` (the variable the
//!   .command launcher already reads) moves it for testing.
//! * **The cue desk studio** — castle-core's `studio` bin, the scene editor
//!   and rebuild/publish server. Not 8765: that is the developer's own
//!   `make studio`, pointed at the repo's show, and reusing it would let the
//!   app write a buyer's edits into a checkout. `CASTLE_DESK_PORT` moves it.
//!
//! Both get the same environment (childenv::child_env), so they read and
//! write the same per-user library and scenes.yaml.

use crate::probe::Identity;
use crate::runtime::{self, Env, Runtime};
use std::ffi::OsString;
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Service {
    Radio,
    Studio,
}

/// A program and its arguments, before the environment is added.
#[derive(Debug, PartialEq, Eq)]
pub struct Launch {
    pub program: PathBuf,
    pub args: Vec<OsString>,
}

impl Service {
    pub fn name(self) -> &'static str {
        match self {
            Service::Radio => "Castle Radio",
            Service::Studio => "the cue desk studio",
        }
    }

    /// The name at the start of a sentence.
    pub fn title(self) -> &'static str {
        match self {
            Service::Radio => "Castle Radio",
            Service::Studio => "The cue desk studio",
        }
    }

    pub fn identity(self) -> Identity {
        match self {
            Service::Radio => Identity::CastleRadio,
            Service::Studio => Identity::Studio,
        }
    }

    fn default_port(self) -> u16 {
        match self {
            Service::Radio => 8871,
            Service::Studio => 8775,
        }
    }

    fn port_var(self) -> &'static str {
        match self {
            Service::Radio => "CASTLE_STUDIO_PORT",
            Service::Studio => "CASTLE_DESK_PORT",
        }
    }

    /// The port, from its variable when that parses as a non-zero port.
    pub fn port(self, env: Env<'_>) -> u16 {
        env(self.port_var())
            .and_then(|v| v.to_str().and_then(|s| s.parse::<u16>().ok()))
            .filter(|p| *p != 0)
            .unwrap_or_else(|| self.default_port())
    }

    /// What to run. The studio binds 127.0.0.1 by default (no `--lan`), and
    /// both take the port as their first argument.
    pub fn launch(self, rt: &Runtime, port: u16) -> Result<Launch, String> {
        match self {
            Service::Radio => Ok(Launch {
                program: rt.python.clone(),
                args: vec![
                    rt.root.join(runtime::SERVER).into(),
                    port.to_string().into(),
                ],
            }),
            Service::Studio => {
                let program = rt.studio.clone().ok_or_else(|| {
                    format!(
                        "The {} has no studio binary (looked in its bin/ and core/target/release/); \
                         the light desk is unavailable, Castle Radio is not affected.",
                        rt.source.describe()
                    )
                })?;
                Ok(Launch {
                    program,
                    args: vec![port.to_string().into()],
                })
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::Source;

    fn rt(studio: Option<&str>) -> Runtime {
        Runtime {
            source: Source::Install,
            root: PathBuf::from("/r"),
            python: PathBuf::from("/r/.venv/bin/python3"),
            bin_dir: None,
            install: None,
            tools: Vec::new(),
            core_bin_dir: None,
            studio: studio.map(PathBuf::from),
        }
    }

    #[test]
    fn ports_default_and_move() {
        let none = |_: &str| None;
        assert_eq!(Service::Radio.port(&none), 8871);
        assert_eq!(Service::Studio.port(&none), 8775);
        let set = |k: &str| match k {
            "CASTLE_STUDIO_PORT" => Some(OsString::from("9001")),
            "CASTLE_DESK_PORT" => Some(OsString::from("0")),
            _ => None,
        };
        assert_eq!(Service::Radio.port(&set), 9001);
        assert_eq!(
            Service::Studio.port(&set),
            8775,
            "port 0 is not a port to serve on"
        );
        let junk = |_: &str| Some(OsString::from("eighty"));
        assert_eq!(Service::Radio.port(&junk), 8871);
    }

    #[test]
    fn never_the_developers_studio_port() {
        let none = |_: &str| None;
        assert_ne!(Service::Studio.port(&none), 8765);
    }

    #[test]
    fn launches() {
        let radio = Service::Radio.launch(&rt(None), 8871).unwrap();
        assert_eq!(radio.program, PathBuf::from("/r/.venv/bin/python3"));
        assert_eq!(
            radio.args,
            vec![
                OsString::from(PathBuf::from("/r").join(runtime::SERVER)),
                OsString::from("8871")
            ]
        );
        let studio = Service::Studio
            .launch(&rt(Some("/r/bin/studio")), 8775)
            .unwrap();
        assert_eq!(
            studio,
            Launch {
                program: "/r/bin/studio".into(),
                args: vec!["8775".into()]
            }
        );
        let err = Service::Studio.launch(&rt(None), 8775).unwrap_err();
        assert!(err.contains("no studio binary"), "{err}");
    }
}
