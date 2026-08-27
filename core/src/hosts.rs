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
//! tables holding `host = "…"` and `fallbacks = ["…", …]`, with comments —
//! because a LAN inventory file does not justify a TOML dependency and the
//! parity test keeps this honest against Python's tomllib.

/// One devices.toml entry, normalized: the host plus its fallbacks.
pub struct Device {
    pub name: String,
    pub host: String,
    pub fallbacks: Vec<String>,
}

/// devices.toml's device tables, in file order. Malformed lines and tables
/// without a `host` are skipped, not errors — hosts.py's "no devices, not
/// a traceback".
pub fn parse_devices(text: &str) -> Vec<Device> {
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
            });
            continue;
        }
        let Some((key, val)) = line.split_once('=') else {
            continue;
        };
        let Some(d) = current.as_mut() else { continue };
        match key.trim() {
            "host" => {
                if let Some(s) = quoted(val) {
                    d.host = s;
                }
            }
            "fallbacks" => d.fallbacks = string_array(val),
            _ => {}
        }
    }
    if let Some(d) = current.take().filter(|d| !d.host.is_empty()) {
        out.push(d);
    }
    out
}

/// The text between the first pair of double quotes, if any.
fn quoted(val: &str) -> Option<String> {
    let (_, rest) = val.split_once('"')?;
    let (s, _) = rest.split_once('"')?;
    Some(s.to_string())
}

/// Every quoted string inside the first `[...]` of `val`, in order.
fn string_array(val: &str) -> Vec<String> {
    let Some((_, rest)) = val.split_once('[') else {
        return Vec::new();
    };
    let body = rest.split_once(']').map_or(rest, |(b, _)| b);
    let mut out = Vec::new();
    let mut parts = body.split('"');
    while let (Some(_), Some(s)) = (parts.next(), parts.next()) {
        out.push(s.to_string());
    }
    out
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

#[cfg(test)]
mod tests {
    use super::*;

    const TOML: &str = r#"
# a comment
[castle-sd]
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
        assert_eq!(d[0].name, "castle-sd");
        assert_eq!(d[0].host, "10.0.0.7");
        assert_eq!(d[0].fallbacks, vec!["10.0.0.8", "10.0.0.9"]);
        assert_eq!(d[1].host, "10.0.0.20");
    }

    #[test]
    fn precedence_is_arg_then_env_then_table() {
        let all = ["10.0.0.7", "10.0.0.8", "10.0.0.9", "10.0.0.20"];
        assert_eq!(candidates(Some("castle-sd"), None, TOML), all[..3].to_vec());
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
}
