//! A YAML subset — enough for a scene block, and for scenes.yaml itself.
//!
//! The crate has no dependencies by policy, so the scene validator that
//! replaced `tools/scene_check.py` (docs/RETIREMENT.md phase 2) brings its
//! own parser rather than a YAML crate. What it reads is the subset the
//! show is written in — block mappings and sequences, flow mappings and
//! sequences (which may run over several lines, as the `zones:` block
//! does), `>`/`|` block scalars, quoted and plain scalars, `#` comments —
//! resolved to types the way PyYAML's `safe_load` resolves them, because
//! the desk's refusal strings quote the values back and `got 0` and
//! `got '0'` are different sentences.
//!
//! Where it deliberately differs from PyYAML, it differs toward REFUSING:
//!
//! - a plain scalar may not run over several lines (PyYAML folds them; the
//!   desk writes `>` and scenes.yaml only ever uses `>`), so a continuation
//!   line is a parse error rather than a silent join;
//! - sexagesimal ints (`1:30` → 90) are plain strings here;
//! - an int too large for `i64` becomes a float, where Python has bigints;
//! - mapping keys are strings; a non-string key is read as its own text;
//! - `#` starts a comment everywhere, including inside a block scalar,
//!   whose text nothing validates.
//!
//! Anchors, aliases, tags, multiple documents and flow keys without a
//! space after the colon are not in the subset and are read as text or
//! refused. `tests/test_scene_schema_rust.py` drives this side and PyYAML
//! over the same corpus and compares the validator's sentences, so a
//! divergence that matters is a red test rather than a surprise on the
//! porch.

use std::fmt;

pub use crate::yaml_parse::parse;

/// One YAML value. `Int` and `Float` are separate because the validator's
/// messages repr the value back and Python's `0` is not `0.0`.
#[derive(Debug, Clone, PartialEq)]
pub enum Yaml {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<Yaml>),
    Map(Vec<(String, Yaml)>),
}

impl Yaml {
    pub fn get(&self, key: &str) -> Option<&Yaml> {
        match self {
            Yaml::Map(kv) => kv.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    /// An empty mapping — `{}` in a flow collection.
    pub fn obj_empty() -> Yaml {
        Yaml::Map(Vec::new())
    }

    pub fn has(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Yaml::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_map(&self) -> Option<&[(String, Yaml)]> {
        match self {
            Yaml::Map(kv) => Some(kv),
            _ => None,
        }
    }

    pub fn as_list(&self) -> Option<&[Yaml]> {
        match self {
            Yaml::List(v) => Some(v),
            _ => None,
        }
    }

    /// The value as a number, the way `scene_schema._num` sees one: ints
    /// and floats, never bools, never NaN or ±inf.
    pub fn finite_num(&self) -> Option<f64> {
        match self {
            Yaml::Int(i) => Some(*i as f64),
            Yaml::Float(f) if f.is_finite() => Some(*f),
            _ => None,
        }
    }

    /// Python's `repr`, for the messages that quote a value back.
    pub fn repr(&self) -> String {
        match self {
            Yaml::Null => "None".into(),
            Yaml::Bool(true) => "True".into(),
            Yaml::Bool(false) => "False".into(),
            Yaml::Int(i) => i.to_string(),
            Yaml::Float(f) => repr_float(*f),
            Yaml::Str(s) => repr_str(s),
            Yaml::List(v) => {
                let inner: Vec<String> = v.iter().map(Yaml::repr).collect();
                format!("[{}]", inner.join(", "))
            }
            Yaml::Map(kv) => {
                let inner: Vec<String> = kv
                    .iter()
                    .map(|(k, v)| format!("{}: {}", repr_str(k), v.repr()))
                    .collect();
                format!("{{{}}}", inner.join(", "))
            }
        }
    }
}

/// Python's `repr` for a str: single quotes unless the text holds one and
/// no double quote, control bytes spelled out.
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `repr` for a float: `9.0`, `nan`, `1e+16`.
pub fn repr_float(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f < 0.0 { "-inf".into() } else { "inf".into() };
    }
    fix_exponent(&format!("{f:?}"))
}

/// Python's `%g` / `f"{v:g}"`: six significant digits, an exponent only
/// outside `[1e-4, 1e6)`. The cue-past-the-end message is formatted this
/// way on both sides.
pub fn format_g(v: f64) -> String {
    if v.is_nan() {
        return "nan".into();
    }
    if v.is_infinite() {
        return if v < 0.0 { "-inf".into() } else { "inf".into() };
    }
    if v == 0.0 {
        return "0".into();
    }
    let sci = format!("{v:.*e}", 5);
    let exp: i32 = sci
        .rsplit_once('e')
        .and_then(|(_, e)| e.parse().ok())
        .unwrap_or(0);
    if !(-4..6).contains(&exp) {
        let mant = sci.split_once('e').map(|(m, _)| m).unwrap_or(&sci);
        let mant = trim_zeros(mant);
        return fix_exponent(&format!("{mant}e{exp}"));
    }
    let places = (5 - exp).max(0) as usize;
    trim_zeros(&format!("{v:.places$}")).to_string()
}

fn trim_zeros(s: &str) -> &str {
    if !s.contains('.') {
        return s;
    }
    s.trim_end_matches('0').trim_end_matches('.')
}

/// `1e16` → `1e+16`, `1e-5` → `1e-05`: Rust's exponent, spelled Python's way.
fn fix_exponent(s: &str) -> String {
    let Some((mant, exp)) = s.split_once('e') else {
        return s.to_string();
    };
    let (sign, digits) = match exp.strip_prefix('-') {
        Some(d) => ('-', d),
        None => ('+', exp.trim_start_matches('+')),
    };
    format!("{mant}e{sign}{digits:0>2}")
}

/// Where a document stopped making sense. `line` is 1-based.
#[derive(Debug, Clone, PartialEq)]
pub struct YamlError {
    pub line: usize,
    pub msg: String,
}

impl fmt::Display for YamlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "line {}: {}", self.line, self.msg)
    }
}

/// A mapping key, as text: quotes come off, everything else is itself.
pub(crate) fn plain_key(s: &str) -> String {
    match scalar(s) {
        Yaml::Str(t) => t,
        _ => s.trim().to_string(),
    }
}

const NULLS: [&str; 4] = ["", "~", "null", "Null"];
const TRUES: [&str; 9] = [
    "yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON",
];
const FALSES: [&str; 9] = [
    "no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF",
];

/// A scalar's text, resolved the way PyYAML's implicit resolver does.
pub fn scalar(raw: &str) -> Yaml {
    let s = raw.trim();
    if s.len() >= 2 && s.starts_with('"') && s.ends_with('"') {
        return Yaml::Str(unescape_double(&s[1..s.len() - 1]));
    }
    if s.len() >= 2 && s.starts_with('\'') && s.ends_with('\'') {
        return Yaml::Str(s[1..s.len() - 1].replace("''", "'"));
    }
    if NULLS.contains(&s) || s == "NULL" {
        return Yaml::Null;
    }
    if TRUES.contains(&s) {
        return Yaml::Bool(true);
    }
    if FALSES.contains(&s) {
        return Yaml::Bool(false);
    }
    if let Some(i) = parse_int(s) {
        return i;
    }
    if let Some(f) = parse_float(s) {
        return Yaml::Float(f);
    }
    Yaml::Str(s.to_string())
}

fn sign_of(s: &str) -> (f64, &str) {
    match s.strip_prefix('-') {
        Some(r) => (-1.0, r),
        None => (1.0, s.strip_prefix('+').unwrap_or(s)),
    }
}

/// PyYAML's int resolver, minus the sexagesimal form (see the header).
fn parse_int(s: &str) -> Option<Yaml> {
    let (sign, body) = sign_of(s);
    let (radix, digits) = if let Some(d) = body.strip_prefix("0x") {
        (16, d)
    } else if let Some(d) = body.strip_prefix("0b") {
        (2, d)
    } else if body.len() > 1 && body.starts_with('0') {
        (8, &body[1..])
    } else {
        (10, body)
    };
    let clean: String = digits.chars().filter(|c| *c != '_').collect();
    if clean.is_empty() || !clean.chars().all(|c| c.is_digit(radix)) {
        return None;
    }
    if radix == 10 && clean.len() > 1 && clean.starts_with('0') {
        return None; // "08" is a string to PyYAML, not an int
    }
    match i64::from_str_radix(&clean, radix) {
        Ok(v) => Some(Yaml::Int(if sign < 0.0 { -v } else { v })),
        // Python has bigints; the nearest float is the honest stand-in.
        Err(_) => u128::from_str_radix(&clean, radix)
            .ok()
            .map(|v| Yaml::Float(sign * v as f64)),
    }
}

/// PyYAML's float resolver: a `.` is required, and an exponent needs a sign.
fn parse_float(s: &str) -> Option<f64> {
    let (sign, body) = sign_of(s);
    if body == ".inf" || body == ".Inf" || body == ".INF" {
        return Some(sign * f64::INFINITY);
    }
    if s == ".nan" || s == ".NaN" || s == ".NAN" {
        return Some(f64::NAN);
    }
    let (num, exp) = match body.find(['e', 'E']) {
        Some(i) => {
            let e = &body[i + 1..];
            if !(e.starts_with('+') || e.starts_with('-'))
                || e.len() < 2
                || !e[1..].chars().all(|c| c.is_ascii_digit())
            {
                return None;
            }
            (&body[..i], &body[i..])
        }
        None => (body, ""),
    };
    let (int, frac) = num.split_once('.')?;
    let int: String = int.chars().filter(|c| *c != '_').collect();
    let frac: String = frac.chars().filter(|c| *c != '_').collect();
    if !int.chars().all(|c| c.is_ascii_digit()) || !frac.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    // `.5` is a float; `-.5` is not one to PyYAML, which is why the
    // sign-carrying form must have digits before the point.
    if int.is_empty() && (frac.is_empty() || sign < 0.0 || s.starts_with('+')) {
        return None;
    }
    format!(
        "{}{int}.{}{exp}",
        if sign < 0.0 { "-" } else { "" },
        if frac.is_empty() { "0" } else { &frac }
    )
    .parse()
    .ok()
}

fn unescape_double(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match it.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('r') => out.push('\r'),
            Some('0') => out.push('\0'),
            Some(other) => out.push(other),
            None => out.push('\\'),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// PyYAML's implicit resolver, quirks included: `1e5` has no signed
    /// exponent so it is a STRING, `08` is not octal, and `yes` is a bool.
    /// The validator quotes these back, so the kind is the message.
    #[test]
    fn a_plain_scalar_resolves_the_way_pyyaml_resolves_one() {
        assert_eq!(scalar(""), Yaml::Null);
        assert_eq!(scalar("~"), Yaml::Null);
        assert_eq!(scalar("null"), Yaml::Null);
        assert_eq!(scalar("NULL"), Yaml::Null);
        assert_eq!(scalar("yes"), Yaml::Bool(true));
        assert_eq!(scalar("Off"), Yaml::Bool(false));
        assert_eq!(scalar("maybe"), Yaml::Str("maybe".into()));
        assert_eq!(scalar("0"), Yaml::Int(0));
        assert_eq!(scalar("-3"), Yaml::Int(-3));
        assert_eq!(scalar("1_000"), Yaml::Int(1000));
        assert_eq!(scalar("0x1f"), Yaml::Int(31));
        assert_eq!(scalar("010"), Yaml::Int(8));
        assert_eq!(scalar("08"), Yaml::Str("08".into()));
        assert_eq!(scalar("0.4"), Yaml::Float(0.4));
        assert_eq!(scalar(".5"), Yaml::Float(0.5));
        assert_eq!(scalar("1.5e+3"), Yaml::Float(1500.0));
        assert_eq!(scalar("1e5"), Yaml::Str("1e5".into()));
        assert_eq!(scalar("-.5"), Yaml::Str("-.5".into()));
        assert!(matches!(scalar(".nan"), Yaml::Float(f) if f.is_nan()));
        assert_eq!(scalar("-.inf"), Yaml::Float(f64::NEG_INFINITY));
        assert_eq!(scalar("'off'"), Yaml::Str("off".into()));
        assert_eq!(scalar("\"a\\nb\""), Yaml::Str("a\nb".into()));
        assert_eq!(scalar("'it''s'"), Yaml::Str("it's".into()));
        assert_eq!(scalar("a: b"), Yaml::Str("a: b".into()));
    }

    /// `_num` in the Python: a bool is not a number, and neither is an
    /// infinity — which is why `duration_ms: .inf` is refused.
    #[test]
    fn only_finite_ints_and_floats_are_numbers() {
        assert_eq!(Yaml::Int(3).finite_num(), Some(3.0));
        assert_eq!(Yaml::Float(0.5).finite_num(), Some(0.5));
        assert_eq!(Yaml::Bool(true).finite_num(), None);
        assert_eq!(Yaml::Float(f64::INFINITY).finite_num(), None);
        assert_eq!(Yaml::Float(f64::NAN).finite_num(), None);
        assert_eq!(Yaml::Str("3".into()).finite_num(), None);
    }

    /// The messages quote the value back with Python's `repr`.
    #[test]
    fn repr_is_pythons_repr() {
        assert_eq!(Yaml::Null.repr(), "None");
        assert_eq!(Yaml::Bool(true).repr(), "True");
        assert_eq!(Yaml::Int(0).repr(), "0");
        assert_eq!(Yaml::Float(9.0).repr(), "9.0");
        assert_eq!(Yaml::Float(-3.5).repr(), "-3.5");
        assert_eq!(Yaml::Float(f64::NAN).repr(), "nan");
        assert_eq!(Yaml::Float(1e16).repr(), "1e+16");
        assert_eq!(Yaml::Float(1e-5).repr(), "1e-05");
        assert_eq!(Yaml::Str("maybe".into()).repr(), "'maybe'");
        assert_eq!(Yaml::Str("it's".into()).repr(), "\"it's\"");
        assert_eq!(Yaml::Str("a\nb".into()).repr(), "'a\\nb'");
        assert_eq!(
            Yaml::List(vec![Yaml::Int(1), Yaml::Str("x".into())]).repr(),
            "[1, 'x']"
        );
        assert_eq!(
            Yaml::Map(vec![("a".into(), Yaml::Int(1))]).repr(),
            "{'a': 1}"
        );
    }

    /// `f"{t:g}"` — six significant digits, an exponent only outside
    /// [1e-4, 1e6). The cue-past-the-end sentence is formatted this way.
    #[test]
    fn format_g_matches_pythons_g() {
        assert_eq!(format_g(0.0), "0");
        assert_eq!(format_g(5.0), "5");
        assert_eq!(format_g(99999.0), "99999");
        assert_eq!(format_g(1200.0), "1200");
        assert_eq!(format_g(0.5), "0.5");
        assert_eq!(format_g(1.0), "1");
        assert_eq!(format_g(4.0), "4");
        assert_eq!(format_g(1234567.0), "1.23457e+06");
        assert_eq!(format_g(0.000012), "1.2e-05");
        assert_eq!(format_g(-2.5), "-2.5");
    }
}
