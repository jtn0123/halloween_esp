//! The reader half of `jsonio` — JSON in, `Json` out.
//!
//! Split from `jsonio.rs` when the depth guard below took that file past the
//! 500-line rule, on the seam the module already had: everything next door
//! writes Python's bytes, everything here reads someone else's. The writer
//! is the parity surface (byte-for-byte with `json.dumps`); the reader is
//! the attack surface, because every POST body and every castle reply
//! arrives through it. `jsonio` re-exports `parse` and `MAX_DEPTH`, so no
//! caller in the crate names this module.

use crate::jsonio::Json;

/// How deep a document may nest before the parser gives up on it.
///
/// `parse_val` recurses once per level, and a blown stack is not an error a
/// server can answer: it aborts the process, and every other connection in
/// flight dies with it (`catch_unwind` cannot catch an abort, and MAX_BODY
/// is five thousand times larger than the ~20 KB that did it). One POST body
/// of `[` repeated is all it took — grade report 2026-09-06 B1, where the
/// Python twin answered 500 and kept serving.
///
/// 200 is generous for what this parser is actually handed: scene blocks,
/// cue lists, tracks.json, a castle's reply — a handful of levels each.
/// CPython refuses at 1000 with a far larger frame, so a document this
/// parser accepts is one json.loads accepts too.
pub const MAX_DEPTH: usize = 200;

pub fn parse(s: &str) -> Result<Json, String> {
    let b = s.as_bytes();
    let mut i = 0usize;
    let v = parse_val(b, &mut i, 0)?;
    skip_ws(b, &mut i);
    if i != b.len() {
        return Err(format!("trailing data at byte {i}"));
    }
    Ok(v)
}

fn skip_ws(b: &[u8], i: &mut usize) {
    while *i < b.len() && matches!(b[*i], b' ' | b'\t' | b'\n' | b'\r') {
        *i += 1;
    }
}

fn parse_val(b: &[u8], i: &mut usize, depth: usize) -> Result<Json, String> {
    skip_ws(b, i);
    // Only a container costs a level: a scalar leaf inside the deepest
    // allowed array is not itself another level of nesting.
    if depth >= MAX_DEPTH && matches!(b.get(*i), Some(b'{' | b'[')) {
        return Err(format!(
            "nested deeper than {MAX_DEPTH} levels at byte {}",
            *i
        ));
    }
    match b.get(*i) {
        None => Err("unexpected end".to_string()),
        Some(b'{') => {
            *i += 1;
            let mut o = Vec::new();
            skip_ws(b, i);
            if b.get(*i) == Some(&b'}') {
                *i += 1;
                return Ok(Json::Obj(o));
            }
            loop {
                skip_ws(b, i);
                // The key is a string and cannot recurse: same depth.
                let Json::Str(k) = parse_val(b, i, depth)? else {
                    return Err("object key is not a string".to_string());
                };
                skip_ws(b, i);
                if b.get(*i) != Some(&b':') {
                    return Err("missing ':'".to_string());
                }
                *i += 1;
                o.push((k, parse_val(b, i, depth + 1)?));
                skip_ws(b, i);
                match b.get(*i) {
                    Some(b',') => *i += 1,
                    Some(b'}') => {
                        *i += 1;
                        return Ok(Json::Obj(o));
                    }
                    _ => return Err("missing ',' or '}'".to_string()),
                }
            }
        }
        Some(b'[') => {
            *i += 1;
            let mut a = Vec::new();
            skip_ws(b, i);
            if b.get(*i) == Some(&b']') {
                *i += 1;
                return Ok(Json::Arr(a));
            }
            loop {
                a.push(parse_val(b, i, depth + 1)?);
                skip_ws(b, i);
                match b.get(*i) {
                    Some(b',') => *i += 1,
                    Some(b']') => {
                        *i += 1;
                        return Ok(Json::Arr(a));
                    }
                    _ => return Err("missing ',' or ']'".to_string()),
                }
            }
        }
        Some(b'"') => parse_str(b, i),
        Some(b't') if b[*i..].starts_with(b"true") => {
            *i += 4;
            Ok(Json::Bool(true))
        }
        Some(b'f') if b[*i..].starts_with(b"false") => {
            *i += 5;
            Ok(Json::Bool(false))
        }
        Some(b'n') if b[*i..].starts_with(b"null") => {
            *i += 4;
            Ok(Json::Null)
        }
        Some(b'N') if b[*i..].starts_with(b"NaN") => {
            *i += 3;
            Ok(Json::Num(f64::NAN))
        }
        Some(b'I') if b[*i..].starts_with(b"Infinity") => {
            *i += 8;
            Ok(Json::Num(f64::INFINITY))
        }
        Some(b'-') if b[*i..].starts_with(b"-Infinity") => {
            *i += 9;
            Ok(Json::Num(f64::NEG_INFINITY))
        }
        Some(_) => {
            let start = *i;
            while *i < b.len() && matches!(b[*i], b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9') {
                *i += 1;
            }
            let tok = std::str::from_utf8(&b[start..*i]).unwrap_or("");
            if tok.is_empty() {
                return Err(format!("unexpected byte at {start}"));
            }
            if tok.contains(['.', 'e', 'E']) {
                tok.parse().map(Json::Num).map_err(|e| e.to_string())
            } else {
                match tok.parse::<i64>() {
                    Ok(v) => Ok(Json::Int(v)),
                    Err(_) => tok.parse().map(Json::Num).map_err(|e| e.to_string()),
                }
            }
        }
    }
}

fn parse_str(b: &[u8], i: &mut usize) -> Result<Json, String> {
    *i += 1; // opening quote
    let mut out = String::new();
    loop {
        match b.get(*i) {
            None => return Err("unterminated string".to_string()),
            Some(b'"') => {
                *i += 1;
                return Ok(Json::Str(out));
            }
            Some(b'\\') => {
                *i += 1;
                match b.get(*i) {
                    Some(b'"') => out.push('"'),
                    Some(b'\\') => out.push('\\'),
                    Some(b'/') => out.push('/'),
                    Some(b'b') => out.push('\u{8}'),
                    Some(b'f') => out.push('\u{c}'),
                    Some(b'n') => out.push('\n'),
                    Some(b'r') => out.push('\r'),
                    Some(b't') => out.push('\t'),
                    Some(b'u') => {
                        let hi = hex4(b, *i + 1)?;
                        *i += 4;
                        let cp = if (0xD800..0xDC00).contains(&hi)
                            && b.get(*i + 1) == Some(&b'\\')
                            && b.get(*i + 2) == Some(&b'u')
                        {
                            let lo = hex4(b, *i + 3)?;
                            if (0xDC00..0xE000).contains(&lo) {
                                *i += 6;
                                0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)
                            } else {
                                hi
                            }
                        } else {
                            hi
                        };
                        out.push(char::from_u32(cp).unwrap_or('\u{fffd}'));
                    }
                    _ => return Err("bad escape".to_string()),
                }
                *i += 1;
            }
            Some(_) => {
                // Copy one UTF-8 scalar, however many bytes it takes.
                let start = *i;
                *i += 1;
                while *i < b.len() && (b[*i] & 0xC0) == 0x80 {
                    *i += 1;
                }
                out.push_str(&String::from_utf8_lossy(&b[start..*i]));
            }
        }
    }
}

fn hex4(b: &[u8], at: usize) -> Result<u32, String> {
    if at + 4 > b.len() {
        return Err("short \\u escape".to_string());
    }
    let s = std::str::from_utf8(&b[at..at + 4]).map_err(|e| e.to_string())?;
    u32::from_str_radix(s, 16).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jsonio::dumps;

    #[test]
    fn nesting_is_bounded_rather_than_fatal() {
        // grade report 2026-09-06 B1: parse_val recursed once per level with
        // nothing counting, so a POST body of 100 KB of '[' overflowed the
        // stack and ABORTED the studio — every other connection with it.
        // A refusal is a Result the route turns into a 400.
        let deep = |n: usize| format!("{}{}", "[".repeat(n), "]".repeat(n));
        assert!(parse(&deep(MAX_DEPTH)).is_ok());
        for body in [
            deep(MAX_DEPTH + 1),
            "[".repeat(100_000), // the shape that actually arrived
            "{\"a\":".repeat(MAX_DEPTH + 1),
        ] {
            let err = parse(&body).unwrap_err();
            assert!(
                err.contains("nested deeper than 200 levels"),
                "want a depth refusal, got {err}"
            );
        }
        // An object 200 deep still parses: the key is a string and does not
        // spend a level of its own.
        let nested = format!("{}1{}", "{\"a\":".repeat(MAX_DEPTH), "}".repeat(MAX_DEPTH));
        assert!(parse(&nested).is_ok(), "{nested:.40}…");
    }

    #[test]
    fn parse_round_trips_and_reads_surrogates() {
        let v = parse("{\"t\": \"\\ud83c\\udf83\", \"n\": 288000, \"f\": 1.5}").unwrap();
        assert_eq!(v.get("t").unwrap().as_str(), Some("🎃"));
        assert_eq!(v.get("n"), Some(&Json::Int(288000)));
        assert_eq!(v.get("f"), Some(&Json::Num(1.5)));
        let text = dumps(&v);
        assert_eq!(parse(&text).unwrap(), v);
    }
}
