//! One log for everything the app does: its own decisions and the Python
//! server's stdout/stderr, appended to the same file in the per-user app
//! log dir (macOS `~/Library/Logs/<identifier>/`, Windows
//! `%LOCALAPPDATA%\<identifier>\logs\`). "Open log" opens exactly this file.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// A log that outgrows this is moved aside to `<name>.1` when the app
/// starts, so the file "Open log" shows is this season's and not every one.
const ROTATE_BYTES: u64 = 5 * 1024 * 1024;

pub struct LogFile {
    path: PathBuf,
    // Serialises our own lines; the child writes through its own handle.
    lock: Mutex<()>,
}

impl LogFile {
    pub fn open(dir: &Path, name: &str) -> io::Result<Self> {
        fs::create_dir_all(dir)?;
        let path = dir.join(name);
        if fs::metadata(&path)
            .map(|m| m.len() > ROTATE_BYTES)
            .unwrap_or(false)
        {
            let _ = fs::rename(&path, path.with_extension("log.1"));
        }
        OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            path,
            lock: Mutex::new(()),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// A fresh append handle for a child's stdout or stderr.
    pub fn handle(&self) -> io::Result<File> {
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
    }

    pub fn line(&self, message: &str) {
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        if let Ok(mut file) = self.handle() {
            let _ = writeln!(file, "[{}] castle-tools: {message}", utc_now());
        }
    }
}

/// `YYYY-MM-DD HH:MM:SSZ`, without a date crate: the civil-from-days
/// conversion is ten lines and the log is the only thing that needs it.
fn utc_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format_utc(secs)
}

fn format_utc(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    // Howard Hinnant's days_from_civil, inverted.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}Z",
        rem / 3_600,
        rem % 3_600 / 60,
        rem % 60
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_known_instants() {
        assert_eq!(format_utc(0), "1970-01-01 00:00:00Z");
        assert_eq!(format_utc(951_782_400), "2000-02-29 00:00:00Z");
        assert_eq!(format_utc(1_790_000_000), "2026-09-21 14:13:20Z");
    }

    #[test]
    fn appends_lines() {
        let dir = std::env::temp_dir().join(format!("castle-log-{}", std::process::id()));
        let log = LogFile::open(&dir, "t.log").unwrap();
        log.line("one");
        log.line("two");
        let text = fs::read_to_string(log.path()).unwrap();
        assert!(text.contains("castle-tools: one") && text.ends_with("castle-tools: two\n"));
        let _ = fs::remove_dir_all(dir);
    }
}
