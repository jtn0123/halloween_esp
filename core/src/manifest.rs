//! tracks.json — tools/manifest.py's read-modify-write, flock and all.
//!
//! The Rust studio and the Python import_track children it spawns edit the
//! SAME manifest, so the cross-process protocol must match exactly: an
//! exclusive flock on tracks.lock around load-modify-save, and an atomic
//! write-then-rename so a crash can never truncate the file. flock(2) comes
//! in through a two-line extern — std grows File::lock in 1.89, libSystem
//! is already linked, and the crate stays zero-dep.

use std::path::{Path, PathBuf};

use crate::jsonio::{self, Json};

unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

fn lock_file(f: &std::fs::File, op: i32) -> i32 {
    use std::os::unix::io::AsRawFd;
    unsafe { flock(f.as_raw_fd(), op) }
}

const LOCK_EX: i32 = 2;
const LOCK_UN: i32 = 8;

pub struct Lock {
    file: std::fs::File,
}

impl Drop for Lock {
    fn drop(&mut self) {
        lock_file(&self.file, LOCK_UN);
    }
}

/// The exclusive cross-process lock manifest.py's _locked() takes.
pub fn lock(tracks: &Path) -> std::io::Result<Lock> {
    match std::fs::create_dir(tracks) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e),
    }
    let file = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .open(tracks.join("tracks.lock"))?;
    if lock_file(&file, LOCK_EX) != 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(Lock { file })
}

pub fn path(tracks: &Path) -> PathBuf {
    tracks.join("tracks.json")
}

/// Every entry, in file order. A damaged file is moved aside loudly rather
/// than read as "no tracks were ever imported" — manifest.load()'s rule.
pub fn load(tracks: &Path) -> Vec<(String, Json)> {
    let p = path(tracks);
    let Ok(text) = std::fs::read_to_string(&p) else {
        return Vec::new();
    };
    match jsonio::parse(&text) {
        Ok(Json::Obj(entries)) => entries,
        _ => {
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let aside = tracks.join(format!("tracks.json.corrupt-{stamp}"));
            let _ = std::fs::rename(&p, &aside);
            eprintln!(
                "WARNING: {} did not parse — moved aside to {} so the next save \
                 cannot erase every track's provenance",
                p.display(),
                aside.display()
            );
            Vec::new()
        }
    }
}

/// Atomic write: json.dumps(indent=2, sort_keys=True) + newline, then rename.
pub fn save(tracks: &Path, data: &[(String, Json)]) -> std::io::Result<()> {
    let _ = std::fs::create_dir(tracks);
    let text = jsonio::dumps_pretty(&Json::Obj(data.to_vec())) + "\n";
    let tmp = tracks.join("tracks.tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path(tracks))
}

pub fn get(tracks: &Path, tid: &str) -> Option<Json> {
    load(tracks)
        .into_iter()
        .find(|(k, _)| k == tid)
        .map(|(_, v)| v)
}

/// manifest.patch: merge fields into one entry under the lock; a track with
/// no entry gets a bare one.
pub fn patch(tracks: &Path, tid: &str, fields: Vec<(String, Json)>) -> std::io::Result<()> {
    let _lock = lock(tracks)?;
    let mut data = load(tracks);
    if !data.iter().any(|(k, _)| k == tid) {
        data.push((tid.to_string(), Json::obj()));
    }
    let entry = data.iter_mut().find(|(k, _)| k == tid).unwrap();
    if let Json::Obj(o) = &mut entry.1 {
        jsonio::obj_update(o, fields);
    }
    save(tracks, &data)
}

/// manifest.forget: drop one entry under the lock.
pub fn forget(tracks: &Path, tid: &str) -> std::io::Result<()> {
    let _lock = lock(tracks)?;
    let mut data = load(tracks);
    let before = data.len();
    data.retain(|(k, _)| k != tid);
    if data.len() != before {
        save(tracks, &data)?;
    }
    Ok(())
}
