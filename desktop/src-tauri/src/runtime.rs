//! Where the Python server and its tools come from, and the environment the
//! child gets. Three places, first match wins:
//!
//! 1. **Bundled sidecar** — `<resource_dir>/castle/` (layout: `desktop/README.md`).
//!    What a release build ships; nothing on the machine is assumed.
//! 2. **Configured install** — `CASTLE_INSTALL_DIR`, else `install_dir` in
//!    settings.json: the option-A installer's tree, or any checkout.
//! 3. **Dev checkout** — the repo this crate was built from, when it is
//!    still on disk. A buyer never has it; a developer always does.

use crate::settings::Settings;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

pub const SERVER: &str = "demo/castle-radio/server.py";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Sidecar,
    Install,
    Checkout,
}

impl Source {
    pub fn describe(self) -> &'static str {
        match self {
            Source::Sidecar => "bundled tools",
            Source::Install => "configured install",
            Source::Checkout => "developer checkout",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Runtime {
    pub source: Source,
    /// The tree holding `tools/` and `demo/castle-radio/` (the server's cwd).
    pub root: PathBuf,
    pub python: PathBuf,
    /// ffmpeg, yt-dlp and the castle-core bins, when the runtime ships them.
    pub bin_dir: Option<PathBuf>,
    /// A Hugging Face hub cache with the Demucs model, when shipped.
    pub models: Option<PathBuf>,
    /// castle-core's `studio` bin — the cue desk server — when there is one.
    pub studio: Option<PathBuf>,
}

/// What `resolve` reads from the process environment — a closure so the
/// tests never depend on the shell that runs them.
pub type Env<'a> = &'a dyn Fn(&str) -> Option<OsString>;

fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_owned()
    }
}

/// The interpreter of a python-build-standalone tree or a venv, if present.
fn python_in(dir: &Path) -> Option<PathBuf> {
    let candidates: &[&str] = if cfg!(windows) {
        &["python.exe", "Scripts/python.exe"]
    } else {
        &["bin/python3", "bin/python"]
    };
    candidates.iter().map(|c| dir.join(c)).find(|p| p.is_file())
}

fn has_server(root: &Path) -> bool {
    root.join(SERVER).is_file()
}

/// The studio bin: the runtime's own bin/ first (where a release unpacks
/// `castle-core-<target>-<tag>.zip`), then a checkout's cargo build.
fn find_studio(root: &Path, bin_dir: Option<&Path>) -> Option<PathBuf> {
    let name = exe("studio");
    bin_dir
        .map(|b| b.join(&name))
        .into_iter()
        .chain([root.join("core/target/release").join(&name)])
        .find(|p| p.is_file())
}

fn sidecar(resource_dir: &Path) -> Option<Runtime> {
    let base = resource_dir.join("castle");
    let root = base.join("app");
    let python = python_in(&base.join("python"))?;
    if !has_server(&root) {
        return None;
    }
    let bin = base.join("bin");
    let bin_dir = bin.is_dir().then_some(bin);
    let models = base.join("models");
    Some(Runtime {
        source: Source::Sidecar,
        studio: find_studio(&root, bin_dir.as_deref()),
        root,
        python,
        bin_dir,
        models: models.is_dir().then_some(models),
    })
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
    Ok(Runtime {
        source,
        studio: find_studio(&root, bin_dir.as_deref()),
        root,
        python,
        bin_dir,
        models: None,
    })
}

/// The dev checkout this binary was built from: desktop/src-tauri/../..
fn checkout() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

pub fn resolve(
    resource_dir: Option<&Path>,
    settings: &Settings,
    env: Env<'_>,
) -> Result<Runtime, String> {
    if let Some(found) = resource_dir.and_then(sidecar) {
        return Ok(found);
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
        return tree(Source::Install, root, explicit_py);
    }
    let dev = checkout();
    if has_server(&dev) {
        let root = dev.canonicalize().unwrap_or(dev);
        return tree(Source::Checkout, root, explicit_py);
    }
    Err(
        "Castle Tools could not find its Python tools. Reinstall the app, or set \
         \"install_dir\" in settings.json to a Castle Tools install."
            .to_owned(),
    )
}

/// Per-user data dirs the children are pointed at — never a repo. One
/// library for both servers: Castle Radio derives tracks/, scenes.yaml and
/// build/ from CASTLE_RADIO_DATA (radio_jobs.py), and the studio reads the
/// same three from CASTLE_TRACKS / CASTLE_SCENES / CASTLE_BUILD.
pub struct DataDirs {
    pub radio: PathBuf,
}

impl DataDirs {
    pub fn new(app_data: &Path) -> Self {
        Self {
            radio: app_data.join("radio"),
        }
    }

    pub fn tracks(&self) -> PathBuf {
        self.radio.join("tracks")
    }

    pub fn scenes(&self) -> PathBuf {
        self.radio.join("scenes.yaml")
    }

    pub fn build(&self) -> PathBuf {
        self.radio.join("build")
    }

    /// The castle key store (tools/castle_keys.py) — this user's, never a
    /// checkout's tracked devices.toml (docs/notes/06-buyer-build.md).
    pub fn devices(&self) -> PathBuf {
        self.radio.join("devices.toml")
    }
}

/// First run: the show the runtime ships (`<root>/scenes/scenes.yaml`)
/// becomes the owner's own copy. Never overwrites — after the first run the
/// file is the owner's edits — and never writes anywhere but the data dir.
/// True when it copied.
pub fn seed_scenes(rt: &Runtime, data: &DataDirs) -> std::io::Result<bool> {
    // Both supervisors seed as they start, on their own threads; one at a
    // time, so the second sees the first one's file rather than racing it.
    static SEEDING: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _one = SEEDING
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let target = data.scenes();
    if target.exists() {
        return Ok(false);
    }
    let shipped = rt.root.join("scenes").join("scenes.yaml");
    if !shipped.is_file() {
        return Ok(false);
    }
    std::fs::create_dir_all(&data.radio)?;
    // Copy beside, then rename: a crash mid-copy must not leave a torn show
    // that the next launch would take for the owner's own.
    let partial = data.radio.join("scenes.yaml.seeding");
    std::fs::copy(&shipped, &partial)?;
    std::fs::rename(&partial, &target)?;
    Ok(true)
}

/// Every variable the child gets on top of the app's own environment.
pub fn child_env(
    rt: &Runtime,
    data: &DataDirs,
    settings: &Settings,
    inherited_path: Option<OsString>,
) -> Vec<(String, OsString)> {
    let mut vars: Vec<(String, OsString)> = vec![
        ("CASTLE_PY".into(), rt.python.clone().into()),
        // Castle Radio keeps its library under CASTLE_RADIO_DATA and derives
        // the three below from it; they are set here as well so any tool the
        // server spawns agrees, whichever variable it reads.
        ("CASTLE_RADIO_DATA".into(), data.radio.clone().into()),
        ("CASTLE_TRACKS".into(), data.tracks().into()),
        ("CASTLE_SCENES".into(), data.scenes().into()),
        ("CASTLE_BUILD".into(), data.build().into()),
        // Where Find my castle remembers the castle, and either app its key.
        ("CASTLE_DEVICES".into(), data.devices().into()),
        ("CASTLE_STUDIO_NO_BROWSER".into(), "1".into()),
        ("PYTHONUNBUFFERED".into(), "1".into()),
        ("PYTHONIOENCODING".into(), "utf-8".into()),
    ];
    // Pinned in settings, both servers use that castle; unpinned, both follow
    // CASTLE_DEVICES' first one (and supervisor.rs drops inherited values).
    if let Some(host) = &settings.castle_host {
        vars.push(("CASTLE_HOST".into(), host.into()));
        vars.push(("CASTLE_RADIO_HOST".into(), host.into()));
    }
    // Only when set: CASTLE_KEY wins over the store, even set-but-empty.
    if let Some(key) = &settings.castle_key {
        vars.push(("CASTLE_KEY".into(), key.expose().into()));
    }
    if rt.source == Source::Sidecar {
        // The bundle is read-only once installed (and signed on macOS).
        vars.push(("PYTHONDONTWRITEBYTECODE".into(), "1".into()));
    }
    if let Some(models) = &rt.models {
        vars.push(("HF_HUB_CACHE".into(), models.clone().into()));
    }
    // PATH first: the tools find ffmpeg / yt-dlp / the python's own scripts
    // with shutil.which today, before any explicit variable is honoured.
    let mut path_dirs: Vec<PathBuf> = Vec::new();
    if let Some(bin) = &rt.bin_dir {
        path_dirs.push(bin.clone());
        for (var, tool) in [("CASTLE_FFMPEG", "ffmpeg"), ("CASTLE_YTDLP", "yt-dlp")] {
            let file = bin.join(exe(tool));
            if file.is_file() {
                vars.push((var.into(), file.into()));
            }
        }
        // INTEGRATION POINT (py-portable / rust-windows agents): the name
        // tools/core_bins.py should read for prebuilt castle-core bins, so a
        // buyer without cargo runs the shipped analyze_track/scene_render.
        // Not honoured on main yet — see desktop/README.md "Integration points".
        vars.push(("CASTLE_CORE_BIN_DIR".into(), bin.clone().into()));
    }
    if let Some(py_dir) = rt.python.parent() {
        path_dirs.push(py_dir.to_path_buf());
    }
    if let Some(old) = inherited_path {
        path_dirs.extend(std::env::split_paths(&old));
    }
    if let Ok(joined) = std::env::join_paths(path_dirs) {
        vars.push(("PATH".into(), joined));
    }
    vars
}

#[cfg(test)]
mod tests {
    use super::*;
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

    #[test]
    fn sidecar_wins_when_complete() {
        let res = scratch("side");
        touch(&res.join("castle/app").join(SERVER));
        let py = if cfg!(windows) {
            "castle/python/python.exe"
        } else {
            "castle/python/bin/python3"
        };
        touch(&res.join(py));
        touch(&res.join("castle/bin").join(exe("ffmpeg")));
        let rt = resolve(Some(&res), &Settings::default(), &no_env).unwrap();
        assert_eq!(rt.source, Source::Sidecar);
        let env = child_env(
            &rt,
            &DataDirs::new(&res.join("data")),
            &Settings::default(),
            None,
        );
        let get = |k: &str| env.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone());
        assert_eq!(
            get("CASTLE_FFMPEG"),
            Some(res.join("castle/bin").join(exe("ffmpeg")).into())
        );
        assert!(
            get("CASTLE_YTDLP").is_none(),
            "only tools that exist are named"
        );
        assert!(get("CASTLE_HOST").is_none(), "unpinned, the store decides");
        assert_eq!(
            get("CASTLE_TRACKS"),
            Some(res.join("data/radio/tracks").into())
        );
        let _ = fs::remove_dir_all(res);
    }

    #[test]
    fn configured_install_is_used_and_reports_why_it_fails() {
        let root = scratch("inst");
        let env = |k: &str| (k == "CASTLE_INSTALL_DIR").then(|| root.clone().into_os_string());
        let err = resolve(None, &Settings::default(), &env).unwrap_err();
        assert!(err.contains("has no"), "{err}");
        touch(&root.join(SERVER));
        let err = resolve(None, &Settings::default(), &env).unwrap_err();
        assert!(err.contains("no Python"), "{err}");
        let venv_py = if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python3"
        };
        touch(&root.join(venv_py));
        let rt = resolve(None, &Settings::default(), &env).unwrap();
        assert_eq!(
            (rt.source, rt.python),
            (Source::Install, root.join(venv_py))
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn studio_bin_found_in_bin_then_checkout() {
        let root = scratch("studio");
        touch(&root.join(SERVER));
        touch(&root.join(".venv/bin/python3"));
        touch(&root.join(".venv/Scripts/python.exe"));
        let env = |k: &str| (k == "CASTLE_INSTALL_DIR").then(|| root.clone().into_os_string());
        assert_eq!(
            resolve(None, &Settings::default(), &env).unwrap().studio,
            None
        );
        let built = root.join("core/target/release").join(exe("studio"));
        touch(&built);
        assert_eq!(
            resolve(None, &Settings::default(), &env).unwrap().studio,
            Some(built)
        );
        let shipped = root.join("bin").join(exe("studio"));
        touch(&shipped);
        assert_eq!(
            resolve(None, &Settings::default(), &env).unwrap().studio,
            Some(shipped)
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn seeds_the_show_once_and_never_overwrites() {
        let base = scratch("seed");
        let rt = Runtime {
            source: Source::Sidecar,
            root: base.join("app"),
            python: base.join("py"),
            bin_dir: None,
            models: None,
            studio: None,
        };
        let data = DataDirs::new(&base.join("data"));
        assert!(
            !seed_scenes(&rt, &data).unwrap(),
            "nothing shipped, nothing seeded"
        );
        touch(&rt.root.join("scenes/scenes.yaml"));
        fs::write(rt.root.join("scenes/scenes.yaml"), "scenes: shipped\n").unwrap();
        assert!(seed_scenes(&rt, &data).unwrap());
        assert_eq!(
            fs::read_to_string(data.scenes()).unwrap(),
            "scenes: shipped\n"
        );
        assert!(!data.radio.join("scenes.yaml.seeding").exists());
        fs::write(data.scenes(), "scenes: the owner's\n").unwrap();
        assert!(!seed_scenes(&rt, &data).unwrap());
        assert_eq!(
            fs::read_to_string(data.scenes()).unwrap(),
            "scenes: the owner's\n"
        );
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn both_servers_share_one_library() {
        let data = DataDirs::new(Path::new("/d"));
        assert_eq!(data.tracks(), Path::new("/d/radio/tracks"));
        assert_eq!(data.scenes(), Path::new("/d/radio/scenes.yaml"));
        assert_eq!(data.build(), Path::new("/d/radio/build"));
    }

    #[test]
    fn castle_host_and_key_only_from_settings() {
        let rt = Runtime {
            source: Source::Checkout,
            root: PathBuf::from("/r"),
            python: PathBuf::from("/r/.venv/bin/python"),
            bin_dir: None,
            models: None,
            studio: None,
        };
        let data = DataDirs::new(Path::new("/d"));
        let none = child_env(&rt, &data, &Settings::default(), None);
        assert!(!none
            .iter()
            .any(|(k, _)| k.ends_with("_HOST") || k == "CASTLE_KEY"));
        // The store is the user's even from a checkout: never the repo's.
        assert!(none
            .iter()
            .any(|(k, v)| k == "CASTLE_DEVICES" && v == "/d/radio/devices.toml"));
        let dir = std::env::temp_dir().join(format!("castle-rt-key-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let json = r#"{"castle_host":"10.1.2.3","castle_key":"k3y!"}"#;
        fs::write(dir.join(crate::settings::FILE_NAME), json).unwrap();
        let some = child_env(&rt, &data, &crate::settings::load(&dir).0, None);
        let _ = fs::remove_dir_all(dir);
        let pins = [
            ("CASTLE_HOST", "10.1.2.3"),
            ("CASTLE_RADIO_HOST", "10.1.2.3"),
        ];
        for (var, want) in [pins[0], pins[1], ("CASTLE_KEY", "k3y!")] {
            assert!(some.iter().any(|(k, v)| k == var && v == want), "{var}");
        }
    }
}
