//! tracks.json — tools/manifest.py's read-modify-write, flock and all.
//!
//! The Rust studio and the Python import_track children it spawns edit the
//! SAME manifest, so the cross-process protocol must match exactly: an
//! exclusive flock on tracks.lock around load-modify-save, and an atomic
//! write-then-rename so a crash can never truncate the file.
//!
//! The lock is flock(2) on macOS and Linux — the very call Python's
//! fcntl.flock makes, so the two exclude each other — and LockFileEx over
//! the whole file on Windows, which is what a byte-range lock from Python's
//! side (msvcrt.locking at offset 0) collides with. Both come in through a
//! few lines of extern so the crate stays zero-dep; std grows the same
//! thing as `File::lock` in 1.89, and this module shrinks to that call the
//! day the toolchain pin (rust-toolchain.toml) moves past it.

use std::path::{Path, PathBuf};

use crate::jsonio::{self, Json};

#[cfg(unix)]
mod os {
    use std::os::unix::io::AsRawFd;

    unsafe extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }

    const LOCK_EX: i32 = 2;
    const LOCK_NB: i32 = 4;
    const LOCK_UN: i32 = 8;

    /// Exclusive; `wait` false is LOCK_NB — fail rather than queue.
    pub fn lock(f: &std::fs::File, wait: bool) -> std::io::Result<()> {
        let op = if wait { LOCK_EX } else { LOCK_EX | LOCK_NB };
        if unsafe { flock(f.as_raw_fd(), op) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn unlock(f: &std::fs::File) {
        unsafe { flock(f.as_raw_fd(), LOCK_UN) };
    }
}

#[cfg(windows)]
mod os {
    use crate::win32::{
        LOCKFILE_EXCLUSIVE_LOCK, LOCKFILE_FAIL_IMMEDIATELY, LockFileEx, Overlapped, UnlockFileEx,
    };
    use std::os::windows::io::AsRawHandle;

    /// Exclusive, every byte from offset 0; `wait` false is
    /// LOCKFILE_FAIL_IMMEDIATELY. std opens files synchronously, so the
    /// waiting form blocks here until the lock is ours, as flock does.
    pub fn lock(f: &std::fs::File, wait: bool) -> std::io::Result<()> {
        let mut flags = LOCKFILE_EXCLUSIVE_LOCK;
        if !wait {
            flags |= LOCKFILE_FAIL_IMMEDIATELY;
        }
        let mut ov = Overlapped::default();
        let h = f.as_raw_handle();
        if unsafe { LockFileEx(h, flags, 0, u32::MAX, u32::MAX, &mut ov) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn unlock(f: &std::fs::File) {
        let mut ov = Overlapped::default();
        unsafe { UnlockFileEx(f.as_raw_handle(), 0, u32::MAX, u32::MAX, &mut ov) };
    }
}

pub struct Lock {
    file: std::fs::File,
}

impl Drop for Lock {
    fn drop(&mut self) {
        os::unlock(&self.file);
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
    os::lock(&file, true)?;
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

#[cfg(test)]
mod tests {
    use super::*;

    /// The lock is a real cross-handle exclusion and not a no-op on some
    /// platform: while one is held, a second handle on the same file cannot
    /// take it, and once it drops the file is free again. The second handle
    /// is what another process's open() would be, which is the case that
    /// matters — the importer children are other processes.
    #[test]
    fn the_manifest_lock_excludes_a_second_handle_until_it_drops() {
        let d = std::env::temp_dir().join(format!("castle-manifest-lock-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        let held = lock(&d).expect("first lock");
        let other = std::fs::OpenOptions::new()
            .write(true)
            .open(d.join("tracks.lock"))
            .expect("second handle");
        assert!(
            os::lock(&other, false).is_err(),
            "a second handle took the lock the first one holds"
        );
        drop(held);
        os::lock(&other, false).expect("the lock outlived its guard");
        os::unlock(&other);
        let _ = std::fs::remove_dir_all(&d);
    }
}
