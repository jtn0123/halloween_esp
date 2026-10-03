//! The app's bundled `castle/` folder, and the runtime its first launch sets
//! up from it. `tools/desktop_bundle.py` stages the folder in the release
//! build; `tests/test_desktop_bundle.py` holds the names below equal to its.
//!
//! ```text
//! <resource_dir>/castle/app/         the source tree: the installer's --source
//! <resource_dir>/castle/bin/         castle-core: the installer's --core-from
//! <resource_dir>/castle/uv/uv[.exe]  the pinned uv
//! <resource_dir>/castle/bundle.json  {"schema": 1, "tag", "target", "uv", "stamp"}
//! ```
//!
//! The runtime is an option-A install (install_tree.rs) in per-user local
//! data, plus uv's `python/` (the managed 3.13 the venv points into) and
//! `cache/`, the copy of uv a setup runs (`uv/`), and a `bundle.json` — the
//! bundle's own, copied in once a setup from it has finished. Ready is: the
//! install is whole and that copy holds
//! this bundle's stamp. A new stamp (the app was updated) or no copy (a setup
//! that never finished) is a setup to run; the installer resumes, so a
//! second run costs seconds, not the first run's download. What a setup
//! runs, and with which environment, is setup_cmd.rs.

use crate::install_tree::InstallTree;
use crate::runtime::{exe, Resolved, Source};
use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub const CASTLE: &str = "castle";
pub const APP: &str = "app";
pub const BIN: &str = "bin";
pub const UV: &str = "uv";
pub const ABOUT: &str = "bundle.json";
/// bundle.json's layout; any other number is a damaged bundle.
pub const SCHEMA: u64 = 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bundle {
    pub dir: PathBuf,
    pub tag: String,
    pub stamp: String,
}

/// bundle.json's tag and stamp, or why it is not a bundle.json.
fn read_about(file: &Path) -> Result<(String, String), String> {
    let text = std::fs::read_to_string(file).map_err(|e| e.to_string())?;
    let about: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    if about.get("schema").and_then(Value::as_u64) != Some(SCHEMA) {
        return Err(format!("its schema is not {SCHEMA}"));
    }
    let field = |key: &str| {
        about
            .get(key)
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .ok_or_else(|| format!("it has no {key}"))
    };
    Ok((field("tag")?, field("stamp")?))
}

impl Bundle {
    /// The bundle under `resource_dir`: None when the app carries none (a
    /// dev build), an error when it carries a damaged one — a reason to
    /// reinstall, never to fall through to some other tree.
    pub fn find(resource_dir: &Path) -> Result<Option<Bundle>, String> {
        let dir = resource_dir.join(CASTLE);
        let file = dir.join(ABOUT);
        if !file.exists() {
            return Ok(None);
        }
        let damaged = |why: String| {
            format!("This copy of Castle Tools is damaged: {why}. Reinstall the app.")
        };
        let (tag, stamp) =
            read_about(&file).map_err(|why| damaged(format!("{} — {why}", file.display())))?;
        let bundle = Bundle { dir, tag, stamp };
        for need in [bundle.app(), bundle.bin()] {
            if !need.is_dir() {
                return Err(damaged(format!("{} is missing", need.display())));
            }
        }
        if !bundle.uv().is_file() {
            return Err(damaged(format!("{} is missing", bundle.uv().display())));
        }
        Ok(Some(bundle))
    }

    pub fn app(&self) -> PathBuf {
        self.dir.join(APP)
    }

    pub fn bin(&self) -> PathBuf {
        self.dir.join(BIN)
    }

    pub fn uv(&self) -> PathBuf {
        self.dir.join(UV).join(exe("uv"))
    }

    fn about(&self) -> PathBuf {
        self.dir.join(ABOUT)
    }
}

/// Why a setup runs — the splash words it for the owner.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    FirstRun,
    Unfinished,
    Update,
    Repair,
}

impl Reason {
    /// What a server waiting on the setup says meanwhile.
    pub fn waiting(self) -> &'static str {
        match self {
            Reason::FirstRun => "Setting up Castle Tools for the first time…",
            Reason::Unfinished => "Finishing Castle Tools' setup…",
            Reason::Update => "Bringing Castle Tools' tools up to date…",
            Reason::Repair => "Repairing Castle Tools…",
        }
    }
}

/// A setup to run: why, from which bundle, into which runtime, with which
/// library (the installer's --data-dir).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub reason: Reason,
    pub bundle: Bundle,
    pub runtime: InstallTree,
    pub data: PathBuf,
}

/// The stamp of the bundle the runtime was last set up from, if a setup
/// from one ever finished.
pub fn set_up_from(runtime: &InstallTree) -> Option<String> {
    read_about(&runtime.root.join(ABOUT)).ok().map(|(_, s)| s)
}

fn has_anything(dir: &Path) -> bool {
    std::fs::read_dir(dir).is_ok_and(|mut d| d.next().is_some())
}

/// The runtime, when it is ready for this bundle; else the setup to run.
/// `repair` (the owner's Repair) sets it up again even when it looks ready.
pub fn plan(bundle: Bundle, runtime: InstallTree, data: PathBuf, repair: bool) -> Resolved {
    let reason = if repair {
        Reason::Repair
    } else if !runtime.is_install() {
        if has_anything(&runtime.root) {
            Reason::Unfinished
        } else {
            Reason::FirstRun
        }
    } else {
        match set_up_from(&runtime) {
            None => Reason::Unfinished,
            Some(stamp) if stamp != bundle.stamp => Reason::Update,
            // The record says whole and the tree disagrees (an env deleted
            // by hand, a cleaner app): set it up again, from scratch.
            Some(_) => match runtime.runtime(Source::Bundled) {
                Ok(rt) => return Resolved::Ready(rt),
                Err(_) => Reason::Repair,
            },
        }
    };
    Resolved::Setup(Plan {
        reason,
        bundle,
        runtime,
        data,
    })
}

impl Plan {
    /// Before a setup runs: forget which bundle the runtime came from, so a
    /// setup cut short is never taken for a finished one, and place the uv
    /// it runs (`uv_program`).
    pub fn begin(&self) -> std::io::Result<()> {
        std::fs::create_dir_all(self.runtime.root.join(UV))?;
        match std::fs::remove_file(self.runtime.root.join(ABOUT)) {
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => return Err(e),
            _ => {}
        }
        let uv = self.uv_program();
        let partial = uv.with_extension("partial");
        std::fs::write(&partial, std::fs::read(self.bundle.uv())?)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&partial, std::fs::Permissions::from_mode(0o755))?;
        }
        std::fs::rename(&partial, &uv)
    }

    /// After it succeeds: the bundle's bundle.json, copied in beside, then
    /// renamed, so the stamp is whole or absent.
    pub fn finish(&self) -> std::io::Result<()> {
        let partial = self.runtime.root.join("bundle.json.partial");
        std::fs::copy(self.bundle.about(), &partial)?;
        std::fs::rename(&partial, self.runtime.root.join(ABOUT))
    }
}

/// A bundle under `res`, with `stamp` (the tests' fixture).
#[cfg(test)]
pub fn fake_bundle(res: &Path, stamp: &str) -> Bundle {
    let dir = res.join(CASTLE);
    std::fs::create_dir_all(dir.join(APP).join("tools")).unwrap();
    std::fs::create_dir_all(dir.join(BIN)).unwrap();
    std::fs::create_dir_all(dir.join(UV)).unwrap();
    std::fs::write(dir.join(UV).join(exe("uv")), "the bundle's uv").unwrap();
    let about = serde_json::json!({"schema": SCHEMA, "tag": "v1.2.3", "stamp": stamp});
    std::fs::write(dir.join(ABOUT), about.to_string()).unwrap();
    Bundle::find(res).unwrap().unwrap()
}

/// A runtime set up from a bundle with `stamp` (the tests' fixture).
#[cfg(test)]
pub fn fake_set_up(runtime: &Path, stamp: &str) {
    let about = serde_json::json!({"schema": SCHEMA, "tag": "v1.2.3", "stamp": stamp});
    std::fs::write(runtime.join(ABOUT), about.to_string()).unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::install_tree::fake_install;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("castle-bu-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn reason(bundle: &Bundle, runtime: &Path, repair: bool) -> Option<Reason> {
        plan(
            bundle.clone(),
            InstallTree::new(runtime),
            "/d".into(),
            repair,
        )
        .into_plan()
        .map(|p| p.reason)
    }

    #[test]
    fn a_bundle_is_its_about_file_and_its_three_parts() {
        let res = scratch("find");
        assert_eq!(Bundle::find(&res), Ok(None), "a dev build carries none");
        let bundle = fake_bundle(&res, "s1");
        assert_eq!(
            (bundle.tag.as_str(), bundle.stamp.as_str()),
            ("v1.2.3", "s1")
        );
        assert_eq!(bundle.uv(), res.join("castle/uv").join(exe("uv")));
        fs::remove_file(bundle.uv()).unwrap();
        let err = Bundle::find(&res).unwrap_err();
        assert!(
            err.contains("damaged") && err.contains("Reinstall"),
            "{err}"
        );
        for bad in [
            "{",
            r#"{"schema": 2, "tag": "v1", "stamp": "s"}"#,
            r#"{"schema": 1, "tag": "v1"}"#,
        ] {
            fs::write(res.join("castle/bundle.json"), bad).unwrap();
            assert!(Bundle::find(&res).is_err(), "{bad}");
        }
        let _ = fs::remove_dir_all(res);
    }

    #[test]
    fn the_runtime_is_ready_only_for_the_bundle_it_was_set_up_from() {
        let base = scratch("plan");
        let bundle = fake_bundle(&base.join("res"), "s1");
        let rt = base.join("runtime");
        assert_eq!(reason(&bundle, &rt, false), Some(Reason::FirstRun));
        fs::create_dir_all(rt.join("python")).unwrap();
        assert_eq!(reason(&bundle, &rt, false), Some(Reason::Unfinished));
        let tree = fake_install(&rt);
        assert_eq!(
            reason(&bundle, &rt, false),
            Some(Reason::Unfinished),
            "whole, but no setup from a bundle ever finished"
        );
        fake_set_up(&rt, "s0");
        assert_eq!(reason(&bundle, &rt, false), Some(Reason::Update));
        fake_set_up(&rt, "s1");
        assert_eq!(reason(&bundle, &rt, false), None, "ready: no setup");
        assert_eq!(reason(&bundle, &rt, true), Some(Reason::Repair));
        fs::remove_file(tree.python()).unwrap();
        assert_eq!(
            reason(&bundle, &rt, false),
            Some(Reason::Repair),
            "damaged under a good record: set up again with --repair"
        );
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn begin_forgets_and_finish_records_the_bundle() {
        let base = scratch("mark");
        let bundle = fake_bundle(&base.join("res"), "s1");
        let rt = base.join("runtime");
        fake_install(&rt);
        let p = plan(bundle.clone(), InstallTree::new(&rt), "/d".into(), false)
            .into_plan()
            .unwrap();
        p.begin().unwrap();
        p.begin().unwrap();
        assert_eq!(set_up_from(&p.runtime), None);
        let uv = p.uv_program();
        assert_eq!(uv, rt.join("uv").join(exe("uv")));
        assert_eq!(fs::read(&uv).unwrap(), b"the bundle's uv");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&uv).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o755);
        }
        p.finish().unwrap();
        assert_eq!(set_up_from(&p.runtime).as_deref(), Some("s1"));
        assert!(!rt.join("bundle.json.partial").exists());
        assert!(plan(bundle, InstallTree::new(&rt), "/d".into(), false)
            .into_plan()
            .is_none());
        let _ = fs::remove_dir_all(base);
    }
}
