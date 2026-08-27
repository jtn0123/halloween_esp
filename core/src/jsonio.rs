//! JSON, spoken CPython's way — the studio's wire format and tracks.json.
//!
//! Zero-dep by the crate's rule, and shaped for parity rather than
//! generality: `dumps` writes what Python's `json.dumps` writes (", " and
//! ": " separators, ensure_ascii escapes, floats in repr's shortest form)
//! and `dumps_pretty` its `indent=2, sort_keys=True` manifest form, so the
//! Rust studio's tracks.json is byte-identical to the Python studio's.
//! Floats never take Python's 1e16+ exponent form here — nothing the studio
//! serializes (durations, sizes, seconds) is anywhere near that range.

#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Num(f64),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    pub fn obj() -> Json {
        Json::Obj(Vec::new())
    }
    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(o) => o.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Json::Int(i) => Some(*i as f64),
            Json::Num(f) => Some(*f),
            _ => None,
        }
    }
    pub fn as_obj(&self) -> Option<&[(String, Json)]> {
        match self {
            Json::Obj(o) => Some(o),
            _ => None,
        }
    }
    /// `meta.get(key, "")` — the manifest readers' idiom.
    pub fn str_or(&self, key: &str, dflt: &str) -> String {
        self.get(key)
            .and_then(Json::as_str)
            .unwrap_or(dflt)
            .to_string()
    }
}

/// Merge `fields` into an object the way dict.update does: an existing key
/// keeps its position and takes the new value; a new key is appended.
pub fn obj_update(obj: &mut Vec<(String, Json)>, fields: Vec<(String, Json)>) {
    for (k, v) in fields {
        match obj.iter_mut().find(|(ek, _)| *ek == k) {
            Some(slot) => slot.1 = v,
            None => obj.push((k, v)),
        }
    }
}

/// json.dumps with its default separators.
pub fn dumps(v: &Json) -> String {
    let mut out = String::new();
    write_val(v, &mut out);
    out
}

/// json.dumps(indent=2, sort_keys=True) — the manifest's file form.
pub fn dumps_pretty(v: &Json) -> String {
    let mut out = String::new();
    write_pretty(v, &mut out, 0);
    out
}

fn write_val(v: &Json, out: &mut String) {
    match v {
        Json::Null => out.push_str("null"),
        Json::Bool(true) => out.push_str("true"),
        Json::Bool(false) => out.push_str("false"),
        Json::Int(i) => out.push_str(&i.to_string()),
        Json::Num(f) => out.push_str(&py_float(*f)),
        Json::Str(s) => write_str(s, out),
        Json::Arr(a) => {
            out.push('[');
            for (i, item) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_val(item, out);
            }
            out.push(']');
        }
        Json::Obj(o) => {
            out.push('{');
            for (i, (k, item)) in o.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_str(k, out);
                out.push_str(": ");
                write_val(item, out);
            }
            out.push('}');
        }
    }
}

fn write_pretty(v: &Json, out: &mut String, level: usize) {
    match v {
        Json::Arr(a) if !a.is_empty() => {
            out.push_str("[\n");
            for (i, item) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                indent(out, level + 1);
                write_pretty(item, out, level + 1);
            }
            out.push('\n');
            indent(out, level);
            out.push(']');
        }
        Json::Obj(o) if !o.is_empty() => {
            let mut order: Vec<usize> = (0..o.len()).collect();
            order.sort_by(|&a, &b| o[a].0.cmp(&o[b].0));
            out.push_str("{\n");
            for (i, &at) in order.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                indent(out, level + 1);
                write_str(&o[at].0, out);
                out.push_str(": ");
                write_pretty(&o[at].1, out, level + 1);
            }
            out.push('\n');
            indent(out, level);
            out.push('}');
        }
        other => write_val(other, out),
    }
}

fn indent(out: &mut String, level: usize) {
    for _ in 0..level * 2 {
        out.push(' ');
    }
}

/// repr()'s float text for the values the studio actually writes.
pub fn py_float(f: f64) -> String {
    if f.is_nan() {
        return "NaN".to_string();
    }
    if f.is_infinite() {
        return (if f > 0.0 { "Infinity" } else { "-Infinity" }).to_string();
    }
    let s = format!("{f}");
    if s.contains('.') || s.contains('e') || s.contains('E') {
        s
    } else {
        format!("{s}.0")
    }
}

fn write_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                push_u(out, c as u32);
            }
            c if (c as u32) < 0x7f => out.push(c),
            c => {
                // ensure_ascii: BMP as \uXXXX, astral as a surrogate pair.
                let cp = c as u32;
                if cp > 0xFFFF {
                    let v = cp - 0x10000;
                    push_u(out, 0xD800 + (v >> 10));
                    push_u(out, 0xDC00 + (v & 0x3FF));
                } else {
                    push_u(out, cp);
                }
            }
        }
    }
    out.push('"');
}

fn push_u(out: &mut String, cp: u32) {
    use std::fmt::Write as _;
    let _ = write!(out, "\\u{cp:04x}");
}

pub fn parse(s: &str) -> Result<Json, String> {
    let b = s.as_bytes();
    let mut i = 0usize;
    let v = parse_val(b, &mut i)?;
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

fn parse_val(b: &[u8], i: &mut usize) -> Result<Json, String> {
    skip_ws(b, i);
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
                let Json::Str(k) = parse_val(b, i)? else {
                    return Err("object key is not a string".to_string());
                };
                skip_ws(b, i);
                if b.get(*i) != Some(&b':') {
                    return Err("missing ':'".to_string());
                }
                *i += 1;
                o.push((k, parse_val(b, i)?));
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
                a.push(parse_val(b, i)?);
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

    #[test]
    fn dumps_matches_pythons_defaults() {
        let v = Json::Obj(vec![
            ("a".into(), Json::Int(1)),
            ("b".into(), Json::Arr(vec![Json::Num(2.5), Json::Null])),
            ("c".into(), Json::Str("x\"y\n🎃".into())),
        ]);
        assert_eq!(
            dumps(&v),
            "{\"a\": 1, \"b\": [2.5, null], \"c\": \"x\\\"y\\n\\ud83c\\udf83\"}"
        );
    }

    #[test]
    fn pretty_matches_indent2_sortkeys() {
        let v = Json::Obj(vec![
            ("b".into(), Json::Obj(vec![("z".into(), Json::Num(24.0))])),
            ("a".into(), Json::Arr(vec![])),
        ]);
        assert_eq!(
            dumps_pretty(&v),
            "{\n  \"a\": [],\n  \"b\": {\n    \"z\": 24.0\n  }\n}"
        );
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

    #[test]
    fn update_keeps_position_and_appends() {
        let mut o = vec![
            ("x".to_string(), Json::Int(1)),
            ("y".to_string(), Json::Int(2)),
        ];
        obj_update(
            &mut o,
            vec![("x".into(), Json::Int(9)), ("z".into(), Json::Int(3))],
        );
        assert_eq!(
            o,
            vec![
                ("x".to_string(), Json::Int(9)),
                ("y".to_string(), Json::Int(2)),
                ("z".to_string(), Json::Int(3)),
            ]
        );
    }
}
