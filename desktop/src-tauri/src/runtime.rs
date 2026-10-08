//! Where the Python server and its tools come from. Three places, first
//! match wins:
//!
//! 1. **The app's own runtime** — a release carries `<resource_dir>/castle/`
//!    (bundle.rs; staged by `tools/desktop_bundle.py`), and its first launch
//!    sets up an option-A install from it in per-user local data (setup.rs).
//!    Ready, it is used; not yet (first run, an update, an interrupted or a
//!    damaged setup), `resolve` says what setting it up means instead.
//! 2. **Configured install** — `CASTLE_INSTALL_DIR`, else `install_dir` in
//!    settings.json: an option-A install (install_tree.rs), or any checkout.
//! 3. **Dev checkout** — the repo this crate was built from, when it is
//!    still on disk. A buyer never has it; a developer always does.
//!
//! The environment a server is started with is childenv.rs.

use crate::bundle::{self, Bundle, Plan};
use crate::install_tree::InstallTree;
use crate::settings::Settings;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

pub const SERVER: &str = "demo/castle-radio/server.py";

/// The castle-core tools `tools/core_bins.py` runs; a folder holding both is
/// one CASTLE_CORE_BIN_DIR may name.
pub const CORE_TOOLS: [&str; 2] = ["analyze_track", "scene_render"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Bundled,
    Install,
    Checkout,
}

impl Source {
    pub fn describe(self) -> &'static str {
        match self {
            Source::Bundled => "app's own runtime",
            Source::Install => "configured install",
            Source::Checkout => "developer checkout",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Runtime {
    pub source: Source,
    /// The tree holding `tools/` and `demo/castle-radio/` (the server's cwd).
    pub root: PathBuf,
    pub python: PathBuf,
    /// A folder of tools that goes first on PATH (an install's `bin/`).
    pub bin_dir: Option<PathBuf>,
    /// The option-A install root — `models/`, `bin/`, `install.json` — when
    /// the runtime is one (desktop_env.launch_env's variables follow from it).
    pub install: Option<PathBuf>,
    /// CASTLE_FFMPEG / CASTLE_YTDLP and the files they name.
    pub tools: Vec<(String, PathBuf)>,
    /// CASTLE_CORE_BIN_DIR: prebuilt castle-core, so no tool reaches for
    /// cargo (`tools/core_bins.py`).
    pub core_bin_dir: Option<PathBuf>,
    /// castle-core's `studio` bin — the cue desk server — when there is one.
    pub studio: Option<PathBuf>,
}

/// What `resolve` reads from the process environment — a closure so the
/// tests never depend on the shell that runs them.
pub type Env<'a> = &'a dyn Fn(&str) -> Option<OsString>;

pub fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    }
}

/// The interpreter of a venv, if present.
fn python_in(dir: &Path) -> Option<PathBuf> {
    let candidates: &[&str] = if cfg!(windows) {
        &["python.exe", "Scripts/python.exe"]
    } else {
        &["bin/python3", "bin/python"]
    };
    candidates.iter().map(|c| dir.join(c)).find(|p| p.is_file())
}

pub fn has_server(root: &Path) -> bool {
    root.join(SERVER).is_file()
}

/// The studio bin: a tree's own bin/ first, then its cargo build (where the
/// installer places a release's castle-core too).
pub fn find_studio(root: &Path, bin_dir: Option<&Path>) -> Option<PathBuf> {
    let name = exe("studio");
    bin_dir
        .map(|b| b.join(&name))
        .into_iter()
        .chain([root.join("core/target/release").join(&name)])
        .find(|p| p.is_file())
}

/// ffmpeg and yt-dlp in `bin`, by their variable — those not already named.
pub fn tools_in(bin: &Path, named: &[(String, PathBuf)]) -> Vec<(String, PathBuf)> {
    [("CASTLE_FFMPEG", "ffmpeg"), ("CASTLE_YTDLP", "yt-dlp")]
        .iter()
        .filter(|(var, _)| !named.iter().any(|(n, _)| n == var))
        .map(|(var, tool)| ((*var).to_owned(), bin.join(exe(tool))))
        .filter(|(_, file)| file.is_file())
        .collect()
}

/// `dir`, when it holds every tool core_bins.py runs.
pub fn core_dir(dir: &Path) -> Option<PathBuf> {
    CORE_TOOLS
        .iter()
        .all(|t| dir.join(exe(t)).is_file())
        .then(|| dir.to_path_buf())
}

/// Where the app's own runtime lives and what it is set up from.
#[derive(Debug, Clone)]
pub struct Places {
    /// The app's resource dir; a release has `castle/` in it.
    pub resource_dir: Option<PathBuf>,
    /// The app's own runtime: `<app_local_data_dir>/runtime`.
    pub runtime_dir: PathBuf,
    /// The installer's `--data-dir`: the one library (childenv::DataDirs).
    pub data_dir: PathBuf,
}

#[derive(Debug)]
pub enum Resolved {
    Ready(Runtime),
    Setup(Plan),
}

#[cfg(test)]
impl Resolved {
    pub fn into_plan(self) -> Option<Plan> {
        match self {
            Resolved::Setup(plan) => Some(plan),
            Resolved::Ready(_) => None,
        }
    }
}

/// A venv the way the existing launchers look for one: the desktop venv the
/// Mac installer builds first, then the developer venv.
fn tree_python(root: &Path, explicit: Option<PathBuf>) -> Option<PathBuf> {
    explicit.filter(|p| p.is_file()).or_else(|| {
        [".venv-desktop", ".venv"]
            .iter()
            .find_map(|v| python_in(&root.join(v)))
    })
}

/// A tree that is not an option-A install: a checkout, with its own venv.
fn tree(source: Source, root: PathBuf, explicit: Option<PathBuf>) -> Result<Runtime, String> {
    if !has_server(&root) {
        return Err(format!("{} has no {SERVER}", root.display()));
    }
    let python = tree_python(&root, explicit).ok_or_else(|| {
        format!(
            "no Python found for {} (set CASTLE_PY or \"python\" in settings.json)",
            root.display()
        )
    })?;
    let bin = root.join("bin");
    let bin_dir = bin.is_dir().then_some(bin);
    let tools = bin_dir
        .as_deref()
        .map(|b| tools_in(b, &[]))
        .unwrap_or_default();
    Ok(Runtime {
        source,
        studio: find_studio(&root, bin_dir.as_deref()),
        core_bin_dir: bin_dir.as_deref().and_then(core_dir),
        root,
        python,
        bin_dir,
        install: None,
        tools,
    })
}

/// The dev checkout this binary was built from: desktop/src-tauri/../..
fn checkout() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The runtime to start from, or the setup that has to run first. `repair`
/// is the owner's Repair: the app's own runtime is set up again even when
/// it looks ready.
pub fn resolve(
    places: &Places,
    settings: &Settings,
    env: Env<'_>,
    repair: bool,
) -> Result<Resolved, String> {
    if let Some(res) = &places.resource_dir {
        if let Some(found) = Bundle::find(res)? {
            let runtime = InstallTree::new(&places.runtime_dir);
            return Ok(bundle::plan(
                found,
                runtime,
                places.data_dir.clone(),
                repair,
            ));
        }
    }
    let explicit_py = env("CASTLE_PY")
        .map(PathBuf::from)
        .or_else(|| settings.python.clone());
    let configured = env("CASTLE_INSTALL_DIR")
        .map(PathBuf::from)
        .or_else(|| settings.install_dir.clone());
    if let Some(root) = configured {
        // A configured install that is broken is an error worth reading,
        // not a reason to fall through to some other tree silently.
        let install = InstallTree::new(&root);
        if install.is_install() {
            return install.runtime(Source::Install).map(Resolved::Ready);
        }
        return tree(Source::Install, root, explicit_py).map(Resolved::Ready);
    }
    let dev = checkout();
    if has_server(&dev) {
        let root = dev.canonicalize().unwrap_or(dev);
        return tree(Source::Checkout, root, explicit_py).map(Resolved::Ready);
    }
    Err(
        "Castle Tools could not find its Python tools. Reinstall the app, or set \
         \"install_dir\" in settings.json to a Castle Tools install."
            .to_owned(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::install_tree::fake_install;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("castle-rt-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn touch(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, "").unwrap();
    }

    fn no_env(_: &str) -> Option<OsString> {
        None
    }

    fn places(base: &Path) -> Places {
        Places {
            resource_dir: Some(base.join("res")),
            runtime_dir: base.join("runtime"),
            data_dir: base.join("data/radio"),
        }
    }

    fn ready(resolved: Result<Resolved, String>) -> Runtime {
        match resolved {
            Ok(Resolved::Ready(rt)) => rt,
            other => panic!("not ready: {other:?}"),
        }
    }

    #[test]
    fn the_bundle_comes_first_and_says_when_to_set_up() {
        let base = scratch("bundled");
        let at = places(&base);
        let env = |k: &str| (k == "CASTLE_INSTALL_DIR").then(|| base.join("elsewhere").into());
        // No bundle: the configured install is next, and says why it fails.
        let err = resolve(&at, &Settings::default(), &env, false).unwrap_err();
        assert!(err.contains("has no"), "{err}");
        crate::bundle::fake_bundle(&base.join("res"), "s1");
        match resolve(&at, &Settings::default(), &env, false).unwrap() {
            Resolved::Setup(plan) => assert_eq!(plan.reason, crate::bundle::Reason::FirstRun),
            other => panic!("{other:?}"),
        }
        fake_install(&at.runtime_dir);
        crate::bundle::fake_set_up(&at.runtime_dir, "s1");
        let rt = ready(resolve(&at, &Settings::default(), &env, false));
        assert_eq!(rt.source, Source::Bundled);
        assert_eq!(rt.root, at.runtime_dir.join("app"));
        assert!(matches!(
            resolve(&at, &Settings::default(), &env, true),
            Ok(Resolved::Setup(_))
        ));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn a_configured_option_a_install_is_read_as_one() {
        let base = scratch("opta");
        let root = base.join("install");
        let tree = fake_install(&root);
        let env = |k: &str| (k == "CASTLE_INSTALL_DIR").then(|| root.clone().into_os_string());
        let at = Places {
            resource_dir: None,
            ..places(&base)
        };
        let rt = ready(resolve(&at, &Settings::default(), &env, false));
        assert_eq!((rt.source, rt.python), (Source::Install, tree.python()));
        assert_eq!(rt.install.as_deref(), Some(root.as_path()));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn configured_tree_is_used_and_reports_why_it_fails() {
        let base = scratch("inst");
        let root = base.join("tree");
        fs::create_dir_all(&root).unwrap();
        let at = places(&base);
        let env = |k: &str| (k == "CASTLE_INSTALL_DIR").then(|| root.clone().into_os_string());
        let err = resolve(&at, &Settings::default(), &env, false).unwrap_err();
        assert!(err.contains("has no"), "{err}");
        touch(&root.join(SERVER));
        let err = resolve(&at, &Settings::default(), &env, false).unwrap_err();
        assert!(err.contains("no Python"), "{err}");
        let venv_py = if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python3"
        };
        touch(&root.join(venv_py));
        let rt = ready(resolve(&at, &Settings::default(), &env, false));
        assert_eq!(
            (rt.source, rt.python),
            (Source::Install, root.join(venv_py))
        );
        assert_eq!((rt.install, rt.core_bin_dir), (None, None));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn studio_and_core_tools_found_in_bin_then_checkout() {
        let base = scratch("studio");
        let root = base.join("tree");
        touch(&root.join(SERVER));
        touch(&root.join(".venv/bin/python3"));
        touch(&root.join(".venv/Scripts/python.exe"));
        let at = places(&base);
        let env = |k: &str| (k == "CASTLE_INSTALL_DIR").then(|| root.clone().into_os_string());
        let get = || ready(resolve(&at, &Settings::default(), &env, false));
        assert_eq!(get().studio, None);
        let built = root.join("core/target/release").join(exe("studio"));
        touch(&built);
        assert_eq!(get().studio, Some(built));
        let shipped = root.join("bin").join(exe("studio"));
        touch(&shipped);
        assert_eq!(get().studio, Some(shipped));
        // A bin/ is CASTLE_CORE_BIN_DIR only once it holds both tools:
        // core_bins.py stops hard on a named folder that lacks one.
        touch(&root.join("bin").join(exe("analyze_track")));
        assert_eq!(get().core_bin_dir, None);
        touch(&root.join("bin").join(exe("scene_render")));
        assert_eq!(get().core_bin_dir, Some(root.join("bin")));
        touch(&root.join("bin").join(exe("ffmpeg")));
        assert_eq!(
            get().tools,
            vec![(
                "CASTLE_FFMPEG".to_owned(),
                root.join("bin").join(exe("ffmpeg"))
            )]
        );
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn a_damaged_bundle_is_an_error_not_a_fall_through() {
        let base = scratch("damaged");
        let res = base.join("res");
        touch(&res.join("castle/bundle.json"));
        let err = resolve(&places(&base), &Settings::default(), &no_env, false).unwrap_err();
        assert!(err.contains("Reinstall"), "{err}");
        let _ = fs::remove_dir_all(base);
    }
}
