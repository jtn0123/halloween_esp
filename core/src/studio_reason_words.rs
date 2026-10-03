//! The words of a failed import — what the castle's owner reads when one
//! goes wrong: what happened, then what to do next. One sentence each, no
//! exit codes, no exception names; the raw output stays in the job's log.
//!
//! `tools/import_reason.py` spells every one of these identically (Castle
//! Radio and the importer speak through it), and `tests/test_import_reason.py`
//! reads this file to hold the two together — sentence for sentence, and
//! the KNOWN table entry for entry, in order. docs/PARITY.md lists the pair.

pub const DOWNLOADER_OLD: &str =
    "The downloader may be out of date — Update the downloader, then try the link again.";
pub const DOWNLOADER_MISSING: &str = "The song downloader is not installed yet — Update the downloader to install it, then try the link again.";
pub const DOWNLOAD_FAILED: &str = "The download did not finish — try the link again, and if it keeps failing, Update the downloader.";
pub const DISK_FULL: &str =
    "The disk is full — free up some space on this computer, then try again.";
pub const NO_ACCESS: &str = "Castle Tools was not allowed to read or save a file it needed — copy the song to a folder on this computer and choose it again.";
pub const OUT_OF_MEMORY: &str =
    "The computer ran out of memory — close other apps, or try a shorter song.";
pub const FFMPEG_MISSING: &str =
    "The audio converter (ffmpeg) is missing — reinstall Castle Tools to put it back.";
pub const DEMUCS_MISSING: &str = "Voice separation is not installed — reinstall Castle Tools, or import with voice separation turned off.";
pub const START_FAILED: &str =
    "Castle Tools could not start its audio tools — reinstall Castle Tools to repair them.";
pub const PRIVATE: &str = "That video is private — try a different link.";
pub const MEMBERS: &str = "That video is for channel members only — try a different link.";
pub const BOT_CHECK: &str = "The website asked to check that you are not a robot — wait an hour and try again, or try a different link.";
pub const SIGN_IN: &str = "That website wants a signed-in account before it will share this video — try a different link.";
pub const GEO: &str = "That video is blocked in your country — try a different link.";
pub const NOT_YET: &str =
    "That video has not started yet — try again once the premiere or live stream is over.";
pub const DRM: &str =
    "That video is copy-protected and cannot be downloaded — try a different link.";
pub const UNAVAILABLE: &str =
    "That video is unavailable and may have been removed — try a different link.";
pub const NOT_A_LINK: &str = "That does not look like a complete link — copy the whole address from your browser and paste it again.";
pub const UNSUPPORTED_SITE: &str =
    "The downloader does not know that website — try a link from a different site.";
pub const NOT_FOUND: &str =
    "That link leads to a page that does not exist — check the link and try again.";
pub const TOO_MANY: &str =
    "The website is refusing downloads for now — wait an hour, then try again.";
pub const SITE_DOWN: &str = "The website is having trouble right now — try again later.";
pub const NETWORK: &str =
    "Could not reach that website — check that this computer is online, then try again.";
pub const NO_AUDIO: &str = "The download finished without any audio — try a different link.";
pub const CONVERT_FAILED: &str =
    "The downloaded audio could not be converted — try the link again, or a different link.";
pub const STALLED: &str =
    "The import took too long and was stopped — try again, or try a shorter song.";
pub const GENERIC: &str = "The import failed for a reason Castle Tools does not recognise — try again, and open Details if it keeps failing.";
/// `{prog}` is the program that failed (ffmpeg, yt-dlp), never its path.
pub const TOOL_FAILED: &str = "{prog} stopped with an error — try again, and if it keeps failing, try a different copy of the song.";

/// Phrases worth a sentence, first match wins, case ignored. The owner's
/// own machine first (a full disk breaks every step, and every tool says
/// so in its own words), then what the website said about the video, then
/// the link, then the signs of a downloader the site has outgrown, then
/// the network, then the audio itself.
pub const KNOWN: [(&str, &str); 68] = [
    ("No space left on device", DISK_FULL),
    ("not enough space on the disk", DISK_FULL),
    ("Disk quota exceeded", DISK_FULL),
    ("Read-only file system", NO_ACCESS),
    ("Permission denied", NO_ACCESS),
    ("Access is denied", NO_ACCESS),
    ("Operation not permitted", NO_ACCESS),
    ("out of memory", OUT_OF_MEMORY),
    ("MemoryError", OUT_OF_MEMORY),
    ("Cannot allocate memory", OUT_OF_MEMORY),
    ("memory allocation of", OUT_OF_MEMORY),
    ("No such file or directory: 'ffmpeg'", FFMPEG_MISSING),
    ("No such file or directory: 'ffprobe'", FFMPEG_MISSING),
    ("ffmpeg: command not found", FFMPEG_MISSING),
    ("ffmpeg not found", FFMPEG_MISSING),
    ("No module named demucs", DEMUCS_MISSING),
    ("No module named 'demucs'", DEMUCS_MISSING),
    ("No such file or directory: 'yt-dlp'", DOWNLOADER_MISSING),
    ("yt-dlp: command not found", DOWNLOADER_MISSING),
    ("Private video", PRIVATE),
    ("members-only", MEMBERS),
    ("not a bot", BOT_CHECK),
    ("Sign in to confirm", SIGN_IN),
    ("in your country", GEO),
    ("from your location", GEO),
    ("geo restrict", GEO),
    ("live event will begin", NOT_YET),
    ("Premieres in", NOT_YET),
    ("Premiere will begin", NOT_YET),
    ("DRM protected", DRM),
    ("Video unavailable", UNAVAILABLE),
    ("video has been removed", UNAVAILABLE),
    ("is not a valid URL", NOT_A_LINK),
    ("Unsupported URL", UNSUPPORTED_SITE),
    ("HTTP Error 404", NOT_FOUND),
    ("HTTP Error 410", NOT_FOUND),
    ("HTTP Error 429", TOO_MANY),
    ("HTTP Error 500", SITE_DOWN),
    ("HTTP Error 502", SITE_DOWN),
    ("HTTP Error 503", SITE_DOWN),
    ("HTTP Error 504", SITE_DOWN),
    ("HTTP Error 403", DOWNLOADER_OLD),
    ("Requested format", DOWNLOADER_OLD),
    ("No video formats found", DOWNLOADER_OLD),
    ("Only images are available", DOWNLOADER_OLD),
    ("Unable to extract", DOWNLOADER_OLD),
    ("Failed to extract", DOWNLOADER_OLD),
    ("nsig extraction failed", DOWNLOADER_OLD),
    ("Signature extraction failed", DOWNLOADER_OLD),
    ("Precondition check failed", DOWNLOADER_OLD),
    ("Confirm you are on the latest version", DOWNLOADER_OLD),
    ("please report this issue", DOWNLOADER_OLD),
    ("Temporary failure in name resolution", NETWORK),
    ("nodename nor servname", NETWORK),
    ("Name or service not known", NETWORK),
    ("getaddrinfo failed", NETWORK),
    ("Failed to resolve", NETWORK),
    ("Network is unreachable", NETWORK),
    ("No route to host", NETWORK),
    ("Connection refused", NETWORK),
    ("Connection reset", NETWORK),
    ("Remote end closed connection", NETWORK),
    ("CERTIFICATE_VERIFY_FAILED", NETWORK),
    ("timed out", NETWORK),
    ("Did not get any data blocks", NETWORK),
    ("Unable to download webpage", NETWORK),
    ("no audio file", NO_AUDIO),
    ("audio conversion failed", CONVERT_FAILED),
];

/// A sentence by its name — the shared corpus names what it expects.
#[cfg(test)]
pub fn named(name: &str) -> Option<&'static str> {
    Some(match name {
        "DOWNLOADER_OLD" => DOWNLOADER_OLD,
        "DOWNLOADER_MISSING" => DOWNLOADER_MISSING,
        "DOWNLOAD_FAILED" => DOWNLOAD_FAILED,
        "DISK_FULL" => DISK_FULL,
        "NO_ACCESS" => NO_ACCESS,
        "OUT_OF_MEMORY" => OUT_OF_MEMORY,
        "FFMPEG_MISSING" => FFMPEG_MISSING,
        "DEMUCS_MISSING" => DEMUCS_MISSING,
        "START_FAILED" => START_FAILED,
        "PRIVATE" => PRIVATE,
        "MEMBERS" => MEMBERS,
        "BOT_CHECK" => BOT_CHECK,
        "SIGN_IN" => SIGN_IN,
        "GEO" => GEO,
        "NOT_YET" => NOT_YET,
        "DRM" => DRM,
        "UNAVAILABLE" => UNAVAILABLE,
        "NOT_A_LINK" => NOT_A_LINK,
        "UNSUPPORTED_SITE" => UNSUPPORTED_SITE,
        "NOT_FOUND" => NOT_FOUND,
        "TOO_MANY" => TOO_MANY,
        "SITE_DOWN" => SITE_DOWN,
        "NETWORK" => NETWORK,
        "NO_AUDIO" => NO_AUDIO,
        "CONVERT_FAILED" => CONVERT_FAILED,
        "STALLED" => STALLED,
        "GENERIC" => GENERIC,
        _ => return None,
    })
}
