//! URL import reaches the internet, not the LAN — unless you ARE the
//! studio. tools/netguard.py: a loopback caller may fetch anything;
//! anyone else only hosts whose EVERY resolved address is public. The
//! classifications mirror CPython's ipaddress is_global-and-friends for
//! the ranges that matter (tests/test_netguard.py's corpus, re-run here).

use std::net::{IpAddr, Ipv4Addr, ToSocketAddrs};

/// Is this client address the studio's own machine?
pub fn is_loopback(ip: &str) -> bool {
    ip.split('%')
        .next()
        .unwrap_or("")
        .parse::<IpAddr>()
        .map(|a| match a {
            IpAddr::V4(v) => v.is_loopback(),
            IpAddr::V6(v) => v.is_loopback(),
        })
        .unwrap_or(false)
}

fn v4_public(a: Ipv4Addr) -> bool {
    let o = a.octets();
    !(a.is_private()
        || a.is_loopback()
        || a.is_link_local()
        || a.is_multicast()
        || o[0] == 0                                  // 0.0.0.0/8
        || o[0] >= 240                                // reserved + broadcast
        || (o[0] == 100 && (o[1] & 0xC0) == 64)       // 100.64/10 shared
        || (o[0] == 192 && o[1] == 0 && o[2] == 0)    // 192.0.0.0/24
        || (o[0] == 192 && o[1] == 0 && o[2] == 2)    // TEST-NET-1
        || (o[0] == 198 && (o[1] & 0xFE) == 18)       // 198.18/15 benchmark
        || (o[0] == 198 && o[1] == 51 && o[2] == 100) // TEST-NET-2
        || (o[0] == 203 && o[1] == 0 && o[2] == 113)) // TEST-NET-3
}

/// Globally routable, and nothing else.
pub fn is_public(a: &IpAddr) -> bool {
    match a {
        IpAddr::V4(v) => v4_public(*v),
        IpAddr::V6(v) => {
            let s = v.segments();
            if s[..5] == [0, 0, 0, 0, 0] && s[5] == 0xFFFF {
                // v4-mapped judges as its v4 self, like the Python.
                let o = v.octets();
                return v4_public(Ipv4Addr::new(o[12], o[13], o[14], o[15]));
            }
            if v.is_loopback() || v.is_unspecified() {
                return false;
            }
            if (s[0] & 0xFFC0) == 0xFE80 || (s[0] & 0xFE00) == 0xFC00 || (s[0] & 0xFF00) == 0xFF00 {
                return false; // link-local, ULA, multicast
            }
            (s[0] & 0xE000) == 0x2000 // 2000::/3, the global unicast range
        }
    }
}

/// Every address the name stands for; an IP literal is itself; an
/// unresolvable name is empty (yt-dlp will say so better).
pub fn resolve(host: &str) -> Vec<IpAddr> {
    if let Ok(a) = host.parse() {
        return vec![a];
    }
    (host, 0u16)
        .to_socket_addrs()
        .map(|it| it.map(|sa| sa.ip()).collect())
        .unwrap_or_default()
}

/// urlsplit(url).hostname — lowercased, portless, or Err for a shape
/// urlsplit itself refuses (an unclosed bracket).
fn url_host(url: &str) -> Result<String, ()> {
    let Some(at) = url.find("//") else {
        return Ok(String::new());
    };
    let auth = &url[at + 2..];
    let auth = auth.split(['/', '?', '#']).next().unwrap_or("");
    let auth = auth.rsplit('@').next().unwrap_or("");
    if let Some(stripped) = auth.strip_prefix('[') {
        let Some(end) = stripped.find(']') else {
            return Err(());
        };
        return Ok(stripped[..end].to_lowercase());
    }
    Ok(auth.split(':').next().unwrap_or("").to_lowercase())
}

/// Why this caller may not fetch this URL — or None if they may.
pub fn refuse_reason(url: &str, client_ip: &str) -> Option<String> {
    if is_loopback(client_ip) {
        return None;
    }
    let host = match url_host(url) {
        Err(()) => return Some("that link has no usable host".to_string()),
        Ok(h) => h,
    };
    if host.is_empty() {
        return Some("that link has no host".to_string());
    }
    if host == "localhost" || host == "localhost.localdomain" || host.ends_with(".local") {
        return Some(format!("{host} is not a public address"));
    }
    let addrs = resolve(&host);
    if let Some(bad) = addrs.iter().find(|a| !is_public(a)) {
        return Some(format!(
            "{host} is not a public address ({bad}) — only the studio's own \
             machine may fetch from the LAN"
        ));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classification_matches_the_python_corpus() {
        for s in ["8.8.8.8", "142.250.72.14", "2607:f8b0::1"] {
            assert!(is_public(&s.parse().unwrap()), "{s}");
        }
        for s in [
            "127.0.0.1",
            "10.27.27.7",
            "192.168.1.1",
            "172.16.0.9",
            "169.254.1.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "fd00::1",
            "::ffff:192.168.0.1",
            "100.64.0.1",
        ] {
            assert!(!is_public(&s.parse().unwrap()), "{s}");
        }
    }

    #[test]
    fn loopback_callers_and_zone_suffixes() {
        assert!(is_loopback("127.0.0.1"));
        assert!(is_loopback("::1"));
        assert!(!is_loopback("fe80::1%lo0"));
        assert!(!is_loopback("10.0.0.2"));
        assert!(!is_loopback("garbage"));
    }

    #[test]
    fn refusals_carry_the_pythons_words() {
        assert_eq!(refuse_reason("http://10.0.0.7/x", "127.0.0.1"), None);
        assert_eq!(
            refuse_reason("http://localhost/x", "10.0.0.2").as_deref(),
            Some("localhost is not a public address")
        );
        assert_eq!(
            refuse_reason("http://printer.local/x", "10.0.0.2").as_deref(),
            Some("printer.local is not a public address")
        );
        assert_eq!(
            refuse_reason("notalink", "10.0.0.2").as_deref(),
            Some("that link has no host")
        );
        assert_eq!(
            refuse_reason("http://192.168.1.5/x", "10.0.0.2").as_deref(),
            Some(
                "192.168.1.5 is not a public address (192.168.1.5) — only the \
                 studio's own machine may fetch from the LAN"
            )
        );
    }
}
