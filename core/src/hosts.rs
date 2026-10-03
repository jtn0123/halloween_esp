//! Which castle are we talking to? — tools/hosts.py's resolution, ported.
//!
//! Order: explicit argument (an IP, or a devices.toml name expanded to its
//! host + fallbacks) — CASTLE_HOST (a comma list; bare names are looked
//! up) — every devices.toml entry's host followed by its fallbacks.
//! CASTLE_HOST set-but-EMPTY is "explicitly no castle" and yields nothing.
//! tests/test_bridge_rust.py holds `candidates` to hosts.py's answers on
//! the same inputs, combo for combo.
//!
//! The parser reads the SUBSET of TOML devices.toml actually uses — named
//! tables holding `host = "…"`, `fallbacks = ["…", …]` and `key = "…"`, one
//! value to a line, with comments — because a LAN inventory file does not
//! justify a TOML dependency and the parity tests keep this honest against
//! Python's tomllib. Strings are TOML's: basic strings with their escapes
//! (a castle key may hold `"` and `\`, and tools/castle_keys.py writes them
//! escaped) and literal strings verbatim. A string that is MALFORMED makes
//! the whole file "no devices", which is what tomllib's refusal means to
//! hosts.py.
//!
//! `castle_key` is hosts.py's rule for the castle key (firmware v5.74):
//! tests/test_castle_key_rust.py holds the header the relay and the
//! `castle` bin actually SEND to hosts.key_headers on the same inputs.

/// One devices.toml entry, normalized: the host plus its fallbacks.
pub struct Device {
    pub name: String,
    pub host: String,
    pub fallbacks: Vec<String>,
    /// The castle key (firmware v5.74, sd_web_prefs.h) — "" for none.
    pub key: String,
}

/// sd_web_prefs.h kKeyMax — the longest key the firmware will hold.
pub const KEY_MAX: usize = 64;

/// devices.toml's device tables, in file order. Malformed lines and tables
/// without a `host` are skipped, not errors — hosts.py's "no devices, not
/// a traceback".
pub fn parse_devices(text: &str) -> Vec<Device> {
    parse_strict(text).unwrap_or_default()
}

/// parse_devices, with a malformed string as the Err that empties it.
fn parse_strict(text: &str) -> Result<Vec<Device>, Malformed> {
    let mut out: Vec<Device> = Vec::new();
    let mut current: Option<Device> = None;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some(name) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            if let Some(d) = current.take().filter(|d| !d.host.is_empty()) {
                out.push(d);
            }
            current = Some(Device {
                name: name.trim().to_string(),
                host: String::new(),
                fallbacks: Vec::new(),
                key: String::new(),
            });
            continue;
        }
        let Some((field, val)) = line.split_once('=') else {
            continue;
        };
        let Some(d) = current.as_mut() else { continue };
        match field.trim() {
            "host" => {
                if let Some((s, _)) = toml_string(val)? {
                    d.host = s;
                }
            }
            "key" => {
                if let Some((s, _)) = toml_string(val)? {
                    d.key = s;
                }
            }
            "fallbacks" => d.fallbacks = string_array(val)?,
            _ => {}
        }
    }
    if let Some(d) = current.take().filter(|d| !d.host.is_empty()) {
        out.push(d);
    }
    Ok(out)
}

/// A string tomllib would refuse — and with it, the whole file.
#[derive(Debug, PartialEq)]
struct Malformed;

/// The TOML string at the start of `val` (leading spaces allowed) and what
/// follows it; Ok(None) when the value is not a one-line string at all.
fn toml_string(val: &str) -> Result<Option<(String, &str)>, Malformed> {
    let val = val.trim_start();
    // A multi-line string is outside the subset: not read, not an error.
    if val.starts_with("\"\"\"") || val.starts_with("'''") {
        return Ok(None);
    }
    let control = |c: char| (c < ' ' && c != '\t') || c == '\u{7f}';
    if let Some(rest) = val.strip_prefix('\'') {
        let end = rest.find('\'').ok_or(Malformed)?;
        if rest[..end].chars().any(control) {
            return Err(Malformed);
        }
        return Ok(Some((rest[..end].to_string(), &rest[end + 1..])));
    }
    let Some(rest) = val.strip_prefix('"') else {
        return Ok(None);
    };
    let mut out = String::new();
    let mut chars = rest.char_indices();
    while let Some((i, c)) = chars.next() {
        match c {
            '"' => return Ok(Some((out, &rest[i + 1..]))),
            '\\' => out.push(escape(&mut chars)?),
            c if control(c) => return Err(Malformed),
            c => out.push(c),
        }
    }
    Err(Malformed)
}

/// One basic-string escape, the backslash already taken (TOML 1.0, which
/// is what Python 3.13's tomllib reads).
fn escape(chars: &mut std::str::CharIndices<'_>) -> Result<char, Malformed> {
    let (_, e) = chars.next().ok_or(Malformed)?;
    let hex = |n: usize, chars: &mut std::str::CharIndices<'_>| {
        let digits: String = chars.by_ref().take(n).map(|(_, c)| c).collect();
        if digits.len() != n || !digits.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err(Malformed);
        }
        u32::from_str_radix(&digits, 16)
            .ok()
            .and_then(char::from_u32)
            .ok_or(Malformed)
    };
    Ok(match e {
        'b' => '\u{8}',
        't' => '\t',
        'n' => '\n',
        'f' => '\u{c}',
        'r' => '\r',
        '"' => '"',
        '\\' => '\\',
        'u' => hex(4, chars)?,
        'U' => hex(8, chars)?,
        _ => return Err(Malformed),
    })
}

/// Every string inside the `[...]` that opens `val`, in order.
fn string_array(val: &str) -> Result<Vec<String>, Malformed> {
    let Some(mut rest) = val.trim_start().strip_prefix('[') else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    while let Some((s, after)) = toml_string(rest)? {
        out.push(s);
        rest = after.trim_start();
        rest = rest.strip_prefix(',').unwrap_or(rest);
    }
    Ok(out)
}

/// Every address worth trying, best first — hosts.py `candidates`. Empty
/// means "no castle". `env` is CASTLE_HOST verbatim (None = unset).
pub fn candidates(arg: Option<&str>, env: Option<&str>, toml: &str) -> Vec<String> {
    let entries = parse_devices(toml);
    let expand = |h: &str| -> Vec<String> {
        entries.iter().find(|d| d.name == h).map_or_else(
            || vec![h.to_string()],
            |d| {
                std::iter::once(d.host.clone())
                    .chain(d.fallbacks.clone())
                    .collect()
            },
        )
    };
    if let Some(a) = arg.filter(|a| !a.is_empty()) {
        return expand(a);
    }
    if let Some(env) = env {
        return env
            .split(',')
            .map(str::trim)
            .filter(|h| !h.is_empty())
            .flat_map(expand)
            .collect();
    }
    from_table(toml)
}

/// Every table entry's host + fallbacks — hosts.py `_from_table`, the
/// floor `resolve()` falls back to when candidates comes up empty.
pub fn from_table(toml: &str) -> Vec<String> {
    parse_devices(toml)
        .into_iter()
        .flat_map(|d| std::iter::once(d.host).chain(d.fallbacks))
        .collect()
}

/// A key the firmware could hold (sd_web_prefs.h key_chars_ok): 1–64
/// printable ASCII characters, no space — hosts.py `valid_key`. Nothing
/// else is ever sent: a castle could not have it, and a CR or LF in a
/// header line would be an injection.
pub fn valid_key(key: &str) -> bool {
    !key.is_empty() && key.len() <= KEY_MAX && key.bytes().all(|b| (0x21..=0x7e).contains(&b))
}

/// Python's `str.strip()` whitespace: Unicode White_Space, plus the four
/// information separators (U+001C–U+001F) that `str.isspace()` also counts.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// The castle key to send to `host`, or "" for none — hosts.py
/// `castle_key`. `env` is CASTLE_KEY verbatim (None = unset; set-but-empty
/// is "no key"); else the key of the FIRST entry whose host or fallbacks
/// name `host` (any entry's, the first, when `host` is None). A key the
/// firmware could not hold is no key.
pub fn castle_key(host: Option<&str>, env: Option<&str>, toml: &str) -> String {
    let key = match env {
        Some(e) => e.trim_matches(py_space).to_string(),
        None => parse_devices(toml)
            .into_iter()
            .find(|d| host.is_none_or(|h| d.host == h || d.fallbacks.iter().any(|f| f == h)))
            .map(|d| d.key)
            .unwrap_or_default(),
    };
    if valid_key(&key) { key } else { String::new() }
}

/// CASTLE_KEY as `castle_key` takes it: a value that is not UTF-8 is a key
/// no castle could hold, so it reads as "set, and not a key" — Python sees
/// the same bytes through surrogateescape and refuses them the same way.
pub fn env_key() -> Option<String> {
    std::env::var_os("CASTLE_KEY").map(|v| v.to_string_lossy().into_owned())
}

/// The inventory file — CASTLE_DEVICES when set and not empty, else
/// `default` — hosts.py `devices_path`, the one rule every reader uses.
pub fn devices_path(default: std::path::PathBuf) -> std::path::PathBuf {
    match std::env::var_os("CASTLE_DEVICES") {
        Some(v) if !v.is_empty() => std::path::PathBuf::from(v),
        _ => default,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOML: &str = r#"
# a comment
[castle-feather-s3]
host = "10.0.0.7"   # trailing comment
fallbacks = ["10.0.0.8", "10.0.0.9"]
[spare]
host = "10.0.0.20"
[broken]
nickname = "no host key, skipped"
"#;

    #[test]
    fn the_subset_parser_reads_the_inventory_shape() {
        let d = parse_devices(TOML);
        assert_eq!(d.len(), 2);
        assert_eq!(d[0].name, "castle-feather-s3");
        assert_eq!(d[0].host, "10.0.0.7");
        assert_eq!(d[0].fallbacks, vec!["10.0.0.8", "10.0.0.9"]);
        assert_eq!(d[1].host, "10.0.0.20");
    }

    #[test]
    fn precedence_is_arg_then_env_then_table() {
        let all = ["10.0.0.7", "10.0.0.8", "10.0.0.9", "10.0.0.20"];
        assert_eq!(
            candidates(Some("castle-feather-s3"), None, TOML),
            all[..3].to_vec()
        );
        assert_eq!(
            candidates(Some("1.2.3.4"), Some("ignored"), TOML),
            ["1.2.3.4"]
        );
        assert_eq!(
            candidates(None, Some("spare, 1.2.3.4:81"), TOML),
            ["10.0.0.20", "1.2.3.4:81"]
        );
        assert_eq!(candidates(None, None, TOML), all.to_vec());
    }

    #[test]
    fn an_empty_castle_host_means_explicitly_no_castle() {
        assert!(candidates(None, Some(""), TOML).is_empty());
        assert_eq!(from_table(TOML).len(), 4);
    }

    const KEYED: &str = r#"
[porch]
host = "10.0.0.7"
key = "p0rch\"key\\#1"   # escapes, and a hash inside the string
fallbacks = ['porch.local', "10.0.0.8"]
[bench]
host = 'bench.local'
key = 'lit\eral'
[open]
host = "10.0.0.30"
"#;

    #[test]
    fn keys_are_toml_strings_escapes_and_all() {
        let d = parse_devices(KEYED);
        assert_eq!(d.len(), 3);
        assert_eq!(d[0].key, "p0rch\"key\\#1");
        assert_eq!(d[0].fallbacks, vec!["porch.local", "10.0.0.8"]);
        assert_eq!(
            (d[1].host.as_str(), d[1].key.as_str()),
            ("bench.local", "lit\\eral")
        );
        assert_eq!(d[2].key, "");
        let u = parse_devices("[a]\nhost = \"h\"\nkey = \"\\u0041\\U00000042\\t\"\n");
        assert_eq!(u[0].key, "AB\t");
    }

    #[test]
    fn a_malformed_string_is_no_devices_like_tomllib() {
        for bad in [
            "[a]\nhost = \"10.0.0.7\nkey = \"x\"\n",     // unterminated
            "[a]\nhost = \"10.0.0.7\"\nkey = \"\\q\"\n", // unknown escape
            "[a]\nhost = \"10.0.0.7\"\nkey = \"\\uD800\"\n", // a surrogate
            "[a]\nhost = '10.0.0.7\n",                   // unterminated literal
        ] {
            assert!(parse_devices(bad).is_empty(), "{bad:?}");
        }
    }

    #[test]
    fn the_key_follows_the_host_it_is_sent_to() {
        let k = |h: Option<&str>, env: Option<&str>| castle_key(h, env, KEYED);
        assert_eq!(k(Some("10.0.0.7"), None), "p0rch\"key\\#1");
        assert_eq!(k(Some("porch.local"), None), "p0rch\"key\\#1"); // a fallback
        assert_eq!(k(Some("bench.local"), None), "lit\\eral");
        assert_eq!(k(Some("10.0.0.30"), None), ""); // an open castle
        assert_eq!(k(Some("10.9.9.9"), None), ""); // a castle nobody named
        assert_eq!(k(None, None), "p0rch\"key\\#1"); // the first entry's
        // CASTLE_KEY wins, stripped like str.strip(); set-but-empty is none.
        assert_eq!(k(Some("10.0.0.7"), Some("  env-key\n")), "env-key");
        assert_eq!(k(Some("10.0.0.7"), Some("\u{1c}env-key")), "env-key");
        assert_eq!(k(Some("10.0.0.7"), Some("")), "");
    }

    #[test]
    fn only_a_key_the_firmware_could_hold_is_ever_sent() {
        assert!(valid_key("!~azAZ09\"\\#"));
        assert!(valid_key(&"k".repeat(KEY_MAX)));
        for bad in ["", "two words", "tab\there", "cr\rlf", "caf\u{e9}"] {
            assert!(!valid_key(bad), "{bad:?}");
            assert_eq!(castle_key(None, Some(bad), ""), "");
        }
        assert!(!valid_key(&"k".repeat(KEY_MAX + 1)));
        let toml = "[a]\nhost = \"h\"\nkey = \"line\\nbreak\"\n";
        assert_eq!(castle_key(Some("h"), None, toml), "");
    }
}
