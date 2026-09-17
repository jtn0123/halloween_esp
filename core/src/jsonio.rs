//! JSON, spoken CPython's way — the studio's wire format and tracks.json.
//!
//! Zero-dep by the crate's rule, and shaped for parity rather than
//! generality: `dumps` writes what Python's `json.dumps` writes (", " and
//! ": " separators, ensure_ascii escapes, floats in repr's shortest form)
//! and `dumps_pretty` its `indent=2, sort_keys=True` manifest form, so the
//! Rust studio's tracks.json is byte-identical to the Python studio's.
//! Floats never take Python's 1e16+ exponent form here — nothing the studio
//! serializes (durations, sizes, seconds) is anywhere near that range.
//!
//! This file is the TYPE and the writer; the reader is `jsonio_parse`,
//! re-exported below so `jsonio::parse` still means what it always did.

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

/// The reader half lives next door (`jsonio_parse`), split out when the
/// nesting guard took this file past the 500-line rule. It is re-exported
/// here so every caller keeps saying `jsonio::parse`.
pub use crate::jsonio_parse::{MAX_DEPTH, parse};

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
