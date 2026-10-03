//! What a server is started with: the per-user data dirs, the first run's
//! seeded show, and every variable on top of the app's own environment.
//! An option-A runtime gets the variables `tools/desktop_env.py`
//! `launch_env` gives the option-A launcher, so a server started by the app
//! and one started by `Castle Tools.command` see the same world.

use crate::install_tree::InstallTree;
use crate::runtime::Runtime;
use crate::settings::Settings;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

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
/// True when it copied. (The app's own runtime is seeded by its installer
/// already; this is the same rule for a checkout.)
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
        // Windows' default text encoding is the ANSI code page, and the
        // tools read UTF-8 YAML and JSON. Harmless everywhere else.
        ("PYTHONUTF8".into(), "1".into()),
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
    if crate::channel::opted_in_now(settings.prerelease) {
        vars.push((crate::channel::ENV.into(), "1".into()));
    }
    if let Some(root) = &rt.install {
        let install = InstallTree::new(root);
        // Demucs fetches its weights through huggingface_hub; the installer
        // downloaded them here, so the import never reaches the network.
        let models = install.models();
        vars.push(("HF_HOME".into(), models.join("huggingface").into()));
        vars.push(("TORCH_HOME".into(), models.join("torch").into()));
        // The managed yt-dlp is the installer's own: Update the downloader
        // replaces the one copy.
        vars.push(("CASTLE_DOWNLOADER_DIR".into(), install.bin().into()));
    }
    for (var, file) in &rt.tools {
        vars.push((var.clone(), file.clone().into()));
    }
    if let Some(core) = &rt.core_bin_dir {
        vars.push(("CASTLE_CORE_BIN_DIR".into(), core.clone().into()));
    }
    // PATH first: the tools find ffmpeg / yt-dlp / the python's own scripts
    // with shutil.which today, before any explicit variable is honoured.
    let mut path_dirs: Vec<PathBuf> = Vec::new();
    path_dirs.extend(rt.bin_dir.iter().cloned());
    path_dirs.extend(rt.python.parent().map(Path::to_path_buf));
    path_dirs.extend(
        rt.tools
            .iter()
            .filter_map(|(_, f)| f.parent().map(Path::to_path_buf)),
    );
    if let Some(old) = inherited_path {
        path_dirs.extend(std::env::split_paths(&old));
    }
    let mut seen: Vec<PathBuf> = Vec::new();
    for dir in path_dirs {
        if !seen.contains(&dir) {
            seen.push(dir);
        }
    }
    if let Ok(joined) = std::env::join_paths(seen) {
        vars.push(("PATH".into(), joined));
    }
    vars
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::install_tree::fake_install;
    use crate::runtime::{exe, Source};
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("castle-ce-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn checkout_rt() -> Runtime {
        Runtime {
            source: Source::Checkout,
            root: PathBuf::from("/r"),
            python: PathBuf::from("/r/.venv/bin/python"),
            bin_dir: None,
            install: None,
            tools: Vec::new(),
            core_bin_dir: None,
            studio: None,
        }
    }

    fn get(env: &[(String, OsString)], key: &str) -> Option<OsString> {
        env.iter().find(|(n, _)| n == key).map(|(_, v)| v.clone())
    }

    #[test]
    fn an_install_gets_the_option_a_launchers_variables() {
        let base = scratch("inst");
        let tree = fake_install(&base.join("rt"));
        let rt = tree.runtime(Source::Bundled).unwrap();
        let data = DataDirs::new(&base.join("data"));
        let env = child_env(&rt, &data, &Settings::default(), Some("/usr/bin".into()));
        let ffmpeg = tree.bin().join(exe("ffmpeg"));
        assert_eq!(get(&env, "CASTLE_FFMPEG"), Some(ffmpeg.clone().into()));
        assert!(get(&env, "CASTLE_YTDLP").is_none(), "only tools that exist");
        assert_eq!(
            get(&env, "HF_HOME"),
            Some(tree.models().join("huggingface").into())
        );
        assert_eq!(
            get(&env, "TORCH_HOME"),
            Some(tree.models().join("torch").into())
        );
        assert_eq!(get(&env, "CASTLE_DOWNLOADER_DIR"), Some(tree.bin().into()));
        assert_eq!(get(&env, "PYTHONUTF8"), Some("1".into()));
        assert!(
            get(&env, "CASTLE_HOST").is_none(),
            "unpinned, the store decides"
        );
        assert_eq!(get(&env, "CASTLE_TRACKS"), Some(data.tracks().into()));
        let path: Vec<PathBuf> = std::env::split_paths(&get(&env, "PATH").unwrap()).collect();
        assert_eq!(path[0], tree.bin(), "the install's own tools first");
        assert_eq!(path[1], tree.python().parent().unwrap());
        assert_eq!(
            path.iter().filter(|p| **p == tree.bin()).count(),
            1,
            "ffmpeg's folder is bin/: named once"
        );
        assert_eq!(path.last(), Some(&PathBuf::from("/usr/bin")));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn a_checkout_gets_no_install_variables() {
        let env = child_env(
            &checkout_rt(),
            &DataDirs::new(Path::new("/d")),
            &Settings::default(),
            None,
        );
        for var in [
            "HF_HOME",
            "TORCH_HOME",
            "CASTLE_DOWNLOADER_DIR",
            "CASTLE_CORE_BIN_DIR",
        ] {
            assert!(get(&env, var).is_none(), "{var}");
        }
        let core = Runtime {
            core_bin_dir: Some("/r/bin".into()),
            ..checkout_rt()
        };
        let env = child_env(
            &core,
            &DataDirs::new(Path::new("/d")),
            &Settings::default(),
            None,
        );
        assert_eq!(get(&env, "CASTLE_CORE_BIN_DIR"), Some("/r/bin".into()));
    }

    #[test]
    fn seeds_the_show_once_and_never_overwrites() {
        let base = scratch("seed");
        let rt = Runtime {
            root: base.join("app"),
            ..checkout_rt()
        };
        let data = DataDirs::new(&base.join("data"));
        assert!(
            !seed_scenes(&rt, &data).unwrap(),
            "nothing shipped, nothing seeded"
        );
        fs::create_dir_all(rt.root.join("scenes")).unwrap();
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
        let rt = checkout_rt();
        let data = DataDirs::new(Path::new("/d"));
        let none = child_env(&rt, &data, &Settings::default(), None);
        assert!(!none
            .iter()
            .any(|(k, _)| k.ends_with("_HOST") || k == "CASTLE_KEY"));
        // The store is the user's even from a checkout: never the repo's.
        assert!(none
            .iter()
            .any(|(k, v)| k == "CASTLE_DEVICES" && v == "/d/radio/devices.toml"));
        let dir = scratch("key");
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
