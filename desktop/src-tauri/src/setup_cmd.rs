//! The commands a setup runs, and what they run with. uv fetches a managed
//! Python 3.13 into the runtime; that Python runs the bundle's
//! tools/desktop_install.py into the same runtime, told where everything
//! the app carries already is. Nothing either writes leaves the runtime.
//! `tests/test_desktop_bundle.py` holds `PYTHON`, `INSTALLER` and every flag
//! `install` passes equal to what the installer reads.

use crate::bundle::{Plan, Reason, UV};
use crate::runtime::exe;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// The Python the runtime is set up with — install.sh's and install.ps1's.
pub const PYTHON: &str = "3.13";
/// The installer, under the bundle's `app/`.
pub const INSTALLER: &str = "tools/desktop_install.py";

/// Variables a setup command must not inherit: each would point uv, Python
/// or the installer at something other than the runtime being set up.
pub const SCRUB: &[&str] = &[
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "UV_PYTHON",
    "UV_PYTHON_DOWNLOADS",
    "UV_PYTHON_PREFERENCE",
    "UV_OFFLINE",
    "UV_SYSTEM_PYTHON",
    "CASTLE_TOOLS_HOME",
    "CASTLE_TOOLS_DATA",
    "CASTLE_PY",
];

/// A program and its arguments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cmd {
    pub program: PathBuf,
    pub args: Vec<OsString>,
}

impl Plan {
    /// Variables every setup command runs with: uv keeps its Python and its
    /// cache in the runtime, reads no uv.toml from anywhere, and Python
    /// writes no bytecode into the bundle (signed, and read-only once
    /// installed) as the installer runs from it.
    pub fn env(&self) -> Vec<(&'static str, OsString)> {
        let root = &self.runtime.root;
        vec![
            ("UV_PYTHON_INSTALL_DIR", root.join("python").into()),
            ("UV_CACHE_DIR", root.join("cache").into()),
            ("UV_NO_CONFIG", "1".into()),
            ("PYTHONDONTWRITEBYTECODE", "1".into()),
            ("PYTHONUNBUFFERED", "1".into()),
            ("PYTHONUTF8", "1".into()),
            ("PYTHONIOENCODING", "utf-8".into()),
        ]
    }

    /// The uv a setup runs: `begin` copies the bundle's into the runtime,
    /// byte for byte, so the copy is a new file of the app's own. The
    /// bundle's copy may carry the quarantine flag a downloaded dmg gives
    /// every file in it, and Gatekeeper refuses to run a flagged program
    /// nobody approved. The owner approves the app, not the uv inside it.
    pub fn uv_program(&self) -> PathBuf {
        self.runtime.root.join(UV).join(exe("uv"))
    }

    fn uv(&self, args: &[&str]) -> Cmd {
        Cmd {
            program: self.uv_program(),
            args: args.iter().map(OsString::from).collect(),
        }
    }

    /// uv fetches its managed Python into the runtime — and nowhere else:
    /// no shim on PATH, no Windows registry entry.
    pub fn get_python(&self) -> Cmd {
        self.uv(&["python", "install", PYTHON, "--no-bin", "--no-registry"])
    }

    /// Prints the interpreter `get_python` placed.
    pub fn find_python(&self) -> Cmd {
        self.uv(&["python", "find", "--managed-python", PYTHON])
    }

    /// tools/desktop_install.py, from the bundle, into the runtime: the same
    /// installer option A runs, told where everything already is.
    pub fn install(&self, python: &Path) -> Cmd {
        let app = self.bundle.app();
        let mut args: Vec<OsString> = vec![
            app.join(INSTALLER).into(),
            "--uv".into(),
            self.uv_program().into(),
            "--source".into(),
            app.into(),
            "--prefix".into(),
            self.runtime.root.clone().into(),
            "--data-dir".into(),
            self.data.clone().into(),
            "--core-from".into(),
            self.bundle.bin().into(),
            "--ffmpeg".into(),
            "download".into(),
            "--no-launcher".into(),
            "--progress".into(),
        ];
        if self.reason == Reason::Repair {
            args.push("--repair".into());
        }
        Cmd {
            program: python.to_path_buf(),
            args,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bundle::{fake_bundle, plan};
    use crate::install_tree::InstallTree;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("castle-cmd-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn the_commands_keep_everything_in_the_runtime() {
        let base = scratch("cmds");
        let bundle = fake_bundle(&base.join("res"), "s1");
        let rt = base.join("runtime");
        let p = plan(
            bundle.clone(),
            InstallTree::new(&rt),
            base.join("data"),
            false,
        )
        .into_plan()
        .unwrap();
        let env = p.env();
        let get = |k: &str| env.iter().find(|(n, _)| *n == k).map(|(_, v)| v.clone());
        assert_eq!(get("UV_PYTHON_INSTALL_DIR"), Some(rt.join("python").into()));
        assert_eq!(get("UV_CACHE_DIR"), Some(rt.join("cache").into()));
        assert_eq!(get("PYTHONDONTWRITEBYTECODE"), Some("1".into()));
        let get_py = p.get_python();
        assert_eq!(get_py.program, p.uv_program());
        assert_eq!(p.find_python().program, p.uv_program());
        assert_eq!(
            get_py.args,
            ["python", "install", "3.13", "--no-bin", "--no-registry"].map(OsString::from)
        );
        assert_eq!(
            p.find_python().args[..3],
            ["python", "find", "--managed-python"].map(OsString::from)
        );
        let py = rt.join("python/bin/python3");
        let install = p.install(&py);
        assert_eq!(install.program, py);
        assert_eq!(
            install.args[0],
            OsString::from(bundle.app().join(INSTALLER))
        );
        let flag = |name: &str| {
            let at = install.args.iter().position(|a| a == name)?;
            install.args.get(at + 1).cloned()
        };
        assert_eq!(flag("--uv"), Some(p.uv_program().into()));
        assert_eq!(flag("--source"), Some(bundle.app().into()));
        assert_eq!(flag("--prefix"), Some(rt.clone().into()));
        assert_eq!(flag("--data-dir"), Some(base.join("data").into()));
        assert_eq!(flag("--core-from"), Some(bundle.bin().into()));
        assert_eq!(flag("--ffmpeg"), Some("download".into()));
        assert!(install.args.iter().any(|a| a == "--progress"));
        assert!(!install.args.iter().any(|a| a == "--repair"));
        let repair = plan(bundle, InstallTree::new(&rt), base.join("data"), true)
            .into_plan()
            .unwrap();
        assert!(repair.install(&py).args.iter().any(|a| a == "--repair"));
        let _ = fs::remove_dir_all(base);
    }
}
