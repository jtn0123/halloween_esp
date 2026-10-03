//! An option-A install, read: the tree `tools/desktop_install.py` makes
//! (`tools/desktop_env.py` `Dirs`). Two places hold one:
//!
//! * the app's own runtime in per-user local data, which its first launch
//!   sets up from the bundle (bundle.rs, setup.rs) — `Source::Bundled`;
//! * an option-A install the owner points `install_dir` at — `Source::Install`.
//!
//! ```text
//! <root>/app/           the source tree (Castle Radio, tools/, castle-core
//!                       placed in core/target/release/)
//! <root>/env/           the Python environment from requirements-desktop.lock
//! <root>/bin/           ffmpeg, ffprobe, the managed yt-dlp
//! <root>/models/        the htdemucs weights (huggingface/, torch/)
//! <root>/install.json   what the installer found, written last
//! ```

use crate::runtime::{core_dir, find_studio, has_server, tools_in, Runtime, Source};
use std::path::PathBuf;

/// install.json: written by the installer as its last real step, so a tree
/// without one is an install that has not finished.
pub const RECORD: &str = "install.json";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstallTree {
    pub root: PathBuf,
}

impl InstallTree {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn app(&self) -> PathBuf {
        self.root.join("app")
    }

    pub fn env(&self) -> PathBuf {
        self.root.join("env")
    }

    /// desktop_env.Dirs.python: the venv's interpreter.
    pub fn python(&self) -> PathBuf {
        if cfg!(windows) {
            self.env().join("Scripts").join("python.exe")
        } else {
            self.env().join("bin").join("python")
        }
    }

    pub fn bin(&self) -> PathBuf {
        self.root.join("bin")
    }

    pub fn models(&self) -> PathBuf {
        self.root.join("models")
    }

    pub fn record_path(&self) -> PathBuf {
        self.root.join(RECORD)
    }

    /// install.json as an object, or None when it is missing or unreadable.
    pub fn record(&self) -> Option<serde_json::Map<String, serde_json::Value>> {
        let text = std::fs::read_to_string(self.record_path()).ok()?;
        match serde_json::from_str(&text).ok()? {
            serde_json::Value::Object(map) => Some(map),
            _ => None,
        }
    }

    /// A finished install: its record and its server are both there.
    pub fn is_install(&self) -> bool {
        self.record_path().is_file() && has_server(&self.app())
    }

    /// The tools the installer settled on and recorded, by the variable the
    /// tools read them from (desktop_env.launch_env) — only those that exist.
    fn recorded_tools(&self) -> Vec<(String, PathBuf)> {
        let record = self.record().unwrap_or_default();
        [("ffmpeg", "CASTLE_FFMPEG"), ("ytdlp", "CASTLE_YTDLP")]
            .iter()
            .filter_map(|(key, var)| {
                let path = PathBuf::from(record.get(*key)?.as_str()?);
                path.is_file().then(|| ((*var).to_owned(), path))
            })
            .collect()
    }

    pub fn runtime(&self, source: Source) -> Result<Runtime, String> {
        let root = self.app();
        if !has_server(&root) {
            return Err(format!(
                "{} has no Castle Radio — set it up again",
                root.display()
            ));
        }
        let python = self.python();
        if !python.is_file() {
            return Err(format!("{} has no Python environment", self.root.display()));
        }
        let bin = self.bin();
        let bin_dir = bin.is_dir().then_some(bin);
        let mut tools = self.recorded_tools();
        if let Some(bin) = &bin_dir {
            let more = tools_in(bin, &tools);
            tools.extend(more);
        }
        Ok(Runtime {
            source,
            studio: find_studio(&root, None),
            core_bin_dir: self.prebuilt_core(),
            root,
            python,
            bin_dir,
            install: Some(self.root.clone()),
            tools,
        })
    }

    /// castle-core the installer placed rather than built ("release" or
    /// "bundled" in install.json) is the whole answer: named, so no tool
    /// checks it against core/src and reaches for a cargo the owner may
    /// happen to have. Built from source, the tree keeps its cargo rule.
    fn prebuilt_core(&self) -> Option<PathBuf> {
        let record = self.record()?;
        let how = record.get("core")?.as_str()?;
        if how != "release" && how != "bundled" {
            return None;
        }
        core_dir(&self.app().join("core/target/release"))
    }
}

/// Lay out a finished install under `root` (the tests' fixture, shared by
/// runtime.rs, childenv.rs, bundle.rs and setup.rs).
#[cfg(test)]
pub fn fake_install(root: &std::path::Path) -> InstallTree {
    use crate::runtime::exe;
    let tree = InstallTree::new(root);
    let touch = |p: PathBuf| {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, "").unwrap();
    };
    touch(tree.app().join(crate::runtime::SERVER));
    touch(tree.python());
    for bin in ["studio", "analyze_track", "scene_render"] {
        touch(tree.app().join("core/target/release").join(exe(bin)));
    }
    touch(tree.bin().join(exe("ffmpeg")));
    let record = serde_json::json!({
        "core": "bundled",
        "ffmpeg": tree.bin().join(exe("ffmpeg")),
        "schema": 1,
    });
    std::fs::write(tree.record_path(), record.to_string()).unwrap();
    tree
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::exe;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("castle-it-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn an_install_is_its_record_and_its_server() {
        let root = scratch("is");
        let tree = InstallTree::new(&root);
        assert!(!tree.is_install());
        let tree = fake_install(&root);
        assert!(tree.is_install());
        std::fs::remove_file(tree.record_path()).unwrap();
        assert!(
            !tree.is_install(),
            "no record: the installer never finished"
        );
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn the_runtime_is_the_installers_layout() {
        let root = scratch("rt");
        let tree = fake_install(&root);
        let rt = tree.runtime(Source::Bundled).unwrap();
        assert_eq!(rt.root, root.join("app"));
        assert_eq!(rt.python, tree.python());
        assert_eq!(rt.install.as_deref(), Some(root.as_path()));
        assert_eq!(
            rt.studio,
            Some(root.join("app/core/target/release").join(exe("studio")))
        );
        assert_eq!(
            rt.tools,
            vec![("CASTLE_FFMPEG".to_owned(), tree.bin().join(exe("ffmpeg")))],
            "the recorded ffmpeg, once — not again from bin/"
        );
        assert_eq!(
            rt.core_bin_dir,
            Some(root.join("app/core/target/release")),
            "placed, not built: CASTLE_CORE_BIN_DIR names it"
        );
        let mut record = tree.record().unwrap();
        record.insert("core".into(), "source".into());
        let record = serde_json::Value::Object(record).to_string();
        std::fs::write(tree.record_path(), record).unwrap();
        assert_eq!(tree.runtime(Source::Bundled).unwrap().core_bin_dir, None);
        std::fs::remove_file(tree.python()).unwrap();
        let err = tree.runtime(Source::Bundled).unwrap_err();
        assert!(err.contains("no Python environment"), "{err}");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn a_broken_record_is_no_record() {
        let root = scratch("rec");
        let tree = fake_install(&root);
        std::fs::write(tree.record_path(), "[1, 2]").unwrap();
        assert!(tree.record().is_none());
        assert!(tree.recorded_tools().is_empty());
        let _ = std::fs::remove_dir_all(root);
    }
}
