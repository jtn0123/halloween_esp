//! URL import reaches the internet, not the LAN — unless you ARE the
//! studio. tools/netguard.py holds the rules in prose and is the oracle;
//! this is the twin the studio actually runs. In short: a loopback caller
//! may fetch anything, anyone else only hosts whose EVERY resolved address
//! is public, and a name that resolves to NOTHING is refused (fail closed
//! — "no answer" and "no answer yet" are the same bytes). v4-mapped and
//! NAT64 addresses are judged by the IPv4 they carry. The classifications
//! are CPython's `ipaddress` private/reserved ranges written out below,
//! range by range, rather than approximated by `2000::/3` — that shortcut
//! called `2002::1`, `2001:db8::1` and `2001::1` public. The corpus that
//! holds the two sides together is tests/test_netguard_rust.py.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, ToSocketAddrs};

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

/// A v6 network as eight segments and a prefix length.
type Net6 = ([u16; 8], u32);

/// `ipaddress._IPv6Constants._private_networks`, minus the two entries this
/// file answers earlier and differently: `::ffff:0:0/96` and
/// `64:ff9b:1::/48`'s public sibling `64:ff9b::/96` (both judged by the v4
/// they carry). CPython 3.13.
const V6_PRIVATE: &[Net6] = &[
    ([0, 0, 0, 0, 0, 0, 0, 1], 128),         // ::1/128 loopback
    ([0, 0, 0, 0, 0, 0, 0, 0], 128),         // ::/128 unspecified
    ([0x64, 0xff9b, 1, 0, 0, 0, 0, 0], 48),  // local-use NAT64
    ([0x100, 0, 0, 0, 0, 0, 0, 0], 64),      // 100::/64 discard-only
    ([0x2001, 0, 0, 0, 0, 0, 0, 0], 23),     // IETF protocol assignments
    ([0x2001, 0xdb8, 0, 0, 0, 0, 0, 0], 32), // documentation
    ([0x2002, 0, 0, 0, 0, 0, 0, 0], 16),     // 6to4
    ([0x3fff, 0, 0, 0, 0, 0, 0, 0], 20),     // documentation (RFC 9637)
    ([0xfc00, 0, 0, 0, 0, 0, 0, 0], 7),      // unique-local
    ([0xfe80, 0, 0, 0, 0, 0, 0, 0], 10),     // link-local
];

/// `_private_networks_exceptions` — global assignments carved out of
/// `2001::/23`, which the list above would otherwise swallow.
const V6_PRIVATE_EXCEPTIONS: &[Net6] = &[
    ([0x2001, 1, 0, 0, 0, 0, 0, 1], 128),
    ([0x2001, 1, 0, 0, 0, 0, 0, 2], 128),
    ([0x2001, 3, 0, 0, 0, 0, 0, 0], 32),
    ([0x2001, 4, 0x112, 0, 0, 0, 0, 0], 48),
    ([0x2001, 0x20, 0, 0, 0, 0, 0, 0], 28),
    ([0x2001, 0x30, 0, 0, 0, 0, 0, 0], 28),
];

/// `_reserved_networks`: everything IANA has not handed out, which is every
/// /3 but `2000::` plus the odd corners. The Python's `is_public` refuses
/// these on top of `is_global`, so this twin must too.
const V6_RESERVED: &[Net6] = &[
    ([0, 0, 0, 0, 0, 0, 0, 0], 8),
    ([0x100, 0, 0, 0, 0, 0, 0, 0], 8),
    ([0x200, 0, 0, 0, 0, 0, 0, 0], 7),
    ([0x400, 0, 0, 0, 0, 0, 0, 0], 6),
    ([0x800, 0, 0, 0, 0, 0, 0, 0], 5),
    ([0x1000, 0, 0, 0, 0, 0, 0, 0], 4),
    ([0x4000, 0, 0, 0, 0, 0, 0, 0], 3),
    ([0x6000, 0, 0, 0, 0, 0, 0, 0], 3),
    ([0x8000, 0, 0, 0, 0, 0, 0, 0], 3),
    ([0xa000, 0, 0, 0, 0, 0, 0, 0], 3),
    ([0xc000, 0, 0, 0, 0, 0, 0, 0], 3),
    ([0xe000, 0, 0, 0, 0, 0, 0, 0], 4),
    ([0xf000, 0, 0, 0, 0, 0, 0, 0], 5),
    ([0xf800, 0, 0, 0, 0, 0, 0, 0], 6),
    ([0xfe00, 0, 0, 0, 0, 0, 0, 0], 9),
];

/// Is `a` inside `net`/`bits`?
fn in_net6(a: &Ipv6Addr, net: Net6) -> bool {
    let (base, bits) = net;
    let s = a.segments();
    let mut left = bits;
    for i in 0..8 {
        if left == 0 {
            return true;
        }
        let take = left.min(16);
        let mask = if take == 16 {
            0xFFFFu16
        } else {
            !0u16 << (16 - take)
        };
        if s[i] & mask != base[i] & mask {
            return false;
        }
        left -= take;
    }
    true
}

fn in_any6(a: &Ipv6Addr, nets: &[Net6]) -> bool {
    nets.iter().any(|n| in_net6(a, *n))
}

/// The IPv4 this v6 address really reaches, when it wraps one: a v4-mapped
/// form (`::ffff:0:0/96`) or a NAT64 translation (`64:ff9b::/96`, RFC 6052,
/// whose low 32 bits are the destination). `64:ff9b:1::/48` is the
/// LOCAL-use prefix and deliberately not here — it stays non-public.
fn embedded_v4(v: &Ipv6Addr) -> Option<Ipv4Addr> {
    let s = v.segments();
    let o = v.octets();
    let last4 = Ipv4Addr::new(o[12], o[13], o[14], o[15]);
    if s[..5] == [0, 0, 0, 0, 0] && s[5] == 0xFFFF {
        return Some(last4);
    }
    if s[..6] == [0x64, 0xff9b, 0, 0, 0, 0] {
        return Some(last4);
    }
    None
}

/// Globally routable, and nothing else — CPython's `is_global` and the
/// extra refusals tools/netguard.py stacks on it, range by range.
pub fn is_public(a: &IpAddr) -> bool {
    match a {
        IpAddr::V4(v) => v4_public(*v),
        IpAddr::V6(v) => {
            if let Some(v4) = embedded_v4(v) {
                // v4-mapped and NAT64 judge as their v4 self, like the Python.
                return v4_public(v4);
            }
            if v.is_multicast() {
                return false; // ff00::/8; is_global allows it, is_public does not
            }
            if in_any6(v, V6_PRIVATE) && !in_any6(v, V6_PRIVATE_EXCEPTIONS) {
                return false;
            }
            !in_any6(v, V6_RESERVED)
        }
    }
}

/// Every address the name stands for; an IP literal is itself; a name that
/// does not resolve is empty — and `refuse_reason_with` refuses that.
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
    refuse_reason_with(url, client_ip, &resolve)
}

/// The same decision over a resolver you supply. DNS is the one input the
/// Python's tests mock and a binary cannot: tests/test_netguard_rust.py
/// drives both implementations over one corpus by handing this a table
/// (bin/netguard_dump.rs), which is what makes the SSRF guard a
/// differential gate like every other port rather than a copied snapshot.
pub fn refuse_reason_with(
    url: &str,
    client_ip: &str,
    resolver: &dyn Fn(&str) -> Vec<IpAddr>,
) -> Option<String> {
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
    let addrs = resolver(&host);
    if addrs.is_empty() {
        // Fail closed: an empty answer is indistinguishable from an answer
        // that arrives on the retry, so the guard must not wave it through.
        return Some(format!(
            "{host} does not resolve — only the studio's own machine may fetch \
             a name this one cannot look up"
        ));
    }
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
        for s in [
            "8.8.8.8",
            "142.250.72.14",
            "2607:f8b0::1",
            // 64:ff9b::/96 NAT64 judged by the v4 it carries.
            "64:ff9b::808:808",
            // ...and the global carve-outs inside 2001::/23.
            "2001:1::1",
            "2001:20::1",
        ] {
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
            // One row per range the 2000::/3 shortcut used to call public
            // (grade report 2026-09-17 pm E1).
            "2002::1",
            "2001:db8::1",
            "2001::1",
            "3fff::1",
            "100::1",
            "64:ff9b::7f00:1",
            "64:ff9b:1::808:808",
            "ff02::1",
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

    #[test]
    fn an_unresolvable_name_is_refused_for_a_lan_caller() {
        let nothing = |_: &str| -> Vec<IpAddr> { Vec::new() };
        assert_eq!(
            refuse_reason_with("https://nope.test/v", "10.0.0.2", &nothing).as_deref(),
            Some(
                "nope.test does not resolve — only the studio's own machine may \
                 fetch a name this one cannot look up"
            )
        );
        // ...and the studio's own machine is still exempt, before DNS.
        assert_eq!(
            refuse_reason_with("https://nope.test/v", "127.0.0.1", &nothing),
            None
        );
    }
}
