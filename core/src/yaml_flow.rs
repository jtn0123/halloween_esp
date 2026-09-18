//! Flow collections — `{a: b}` and `[1, 2]`, the spelling scenes.yaml and
//! the cue desk write most of a scene in. Split from [`crate::yaml_parse`]
//! at the repo's 500-line cap; the block parser calls in here whenever a
//! value opens with `{` or `[`, feeding it more lines until it closes.

use crate::yaml::{MAX_DEPTH, Yaml, plain_key, scalar};

struct Flow<'a> {
    s: &'a str,
    i: usize,
}

/// Parse one flow collection. `depth` is how many levels the block parser
/// already spent getting here, so `a: [[[…]]]` is measured from the top of
/// the document rather than from this bracket (grade report 2026-09-17 B1).
pub(crate) fn parse_flow(text: &str, depth: usize) -> Result<Yaml, String> {
    let mut f = Flow { s: text, i: 0 };
    let v = f.value(depth)?;
    f.ws();
    if f.i < f.s.len() {
        return Err("trailing text after a flow collection".into());
    }
    Ok(v)
}

impl Flow<'_> {
    fn ws(&mut self) {
        while self.s[self.i..].starts_with(' ') {
            self.i += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.s.as_bytes().get(self.i).copied()
    }

    fn value(&mut self, depth: usize) -> Result<Yaml, String> {
        self.ws();
        // Only a collection costs a level: a scalar leaf inside the deepest
        // allowed sequence is not itself another level of nesting.
        if depth >= MAX_DEPTH && matches!(self.peek(), Some(b'{' | b'[')) {
            return Err(format!("nested deeper than {MAX_DEPTH} levels"));
        }
        match self.peek() {
            Some(b'{') => self.mapping(depth + 1),
            Some(b'[') => self.sequence(depth + 1),
            _ => Ok(scalar(self.token()?)),
        }
    }

    fn sequence(&mut self, depth: usize) -> Result<Yaml, String> {
        self.i += 1;
        let mut out = Vec::new();
        loop {
            self.ws();
            match self.peek() {
                Some(b']') => {
                    self.i += 1;
                    return Ok(Yaml::List(out));
                }
                None => return Err("unterminated flow sequence".into()),
                _ => {}
            }
            out.push(self.value(depth)?);
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b']') => {}
                _ => return Err("expected \",\" or \"]\" in a flow sequence".into()),
            }
        }
    }

    fn mapping(&mut self, depth: usize) -> Result<Yaml, String> {
        self.i += 1;
        let mut out: Vec<(String, Yaml)> = Vec::new();
        loop {
            self.ws();
            match self.peek() {
                Some(b'}') => {
                    self.i += 1;
                    return Ok(Yaml::Map(out));
                }
                None => return Err("unterminated flow mapping".into()),
                _ => {}
            }
            let key = plain_key(self.token()?);
            self.ws();
            let val = if self.peek() == Some(b':') {
                self.i += 1;
                self.value(depth)?
            } else {
                Yaml::Null
            };
            match out.iter_mut().find(|(k, _)| *k == key) {
                Some(slot) => slot.1 = val,
                None => out.push((key, val)),
            }
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b'}') => {}
                _ => return Err("expected \",\" or \"}\" in a flow mapping".into()),
            }
        }
    }

    /// One scalar token: a quoted string, or plain text up to the next
    /// `,` `:` `}` `]` at this level.
    fn token(&mut self) -> Result<&'_ str, String> {
        self.ws();
        let start = self.i;
        let b = self.s.as_bytes();
        if let Some(q @ (b'"' | b'\'')) = self.peek() {
            self.i += 1;
            while self.i < b.len() {
                if q == b'"' && b[self.i] == b'\\' {
                    self.i += 2;
                    continue;
                }
                if b[self.i] == q {
                    self.i += 1;
                    return Ok(&self.s[start..self.i]);
                }
                self.i += 1;
            }
            return Err("unterminated quoted string".into());
        }
        while self.i < b.len() {
            let c = b[self.i];
            if c == b',' || c == b'}' || c == b']' {
                break;
            }
            if c == b':' && (self.i + 1 == b.len() || b[self.i + 1] == b' ') {
                break;
            }
            self.i += 1;
        }
        if self.i == start {
            return Err("expected a value".into());
        }
        Ok(self.s[start..self.i].trim_end())
    }
}

/// The index just past the flow collection that starts at byte 0, or None
/// when it has not closed yet — how the block half knows whether to feed
/// another line in before calling [`parse_flow`]. It lives here rather than
/// next door because it is the same brackets-and-quotes scan, spelled
/// iteratively: nothing about it can run out of stack.
pub(crate) fn flow_end(s: &str) -> Option<usize> {
    let b = s.as_bytes();
    let mut depth = 0usize;
    let mut quote: Option<u8> = None;
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        match quote {
            Some(q) => {
                if q == b'"' && c == b'\\' {
                    i += 2;
                    continue;
                }
                if c == q {
                    quote = None;
                }
            }
            None => match c {
                b'"' | b'\'' => quote = Some(c),
                b'{' | b'[' => depth += 1,
                b'}' | b']' => {
                    depth -= 1;
                    if depth == 0 {
                        return Some(i + 1);
                    }
                }
                _ => {}
            },
        }
        i += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(pairs: &[(&str, Yaml)]) -> Yaml {
        Yaml::Map(
            pairs
                .iter()
                .map(|(k, v)| ((*k).into(), v.clone()))
                .collect(),
        )
    }

    /// A flow collection at the top of a document: nothing above it has
    /// spent a level yet.
    fn flow(text: &str) -> Result<Yaml, String> {
        parse_flow(text, 0)
    }

    /// grade report 2026-09-17 B1: `value` recursed once per bracket with
    /// nothing counting, so `a: [[[…]]]` twenty thousand deep — 40 KB of a
    /// POST body — overflowed the stack and ABORTED the studio, every other
    /// connection in flight with it. A refusal is a Result the route turns
    /// into a 400. Mirrors the JSON reader's boundary test.
    #[test]
    fn nesting_is_bounded_rather_than_fatal() {
        let deep = |n: usize| format!("{}{}", "[".repeat(n), "]".repeat(n));
        assert!(flow(&deep(MAX_DEPTH)).is_ok());
        for text in [
            deep(MAX_DEPTH + 1),
            "[".repeat(100_000), // the shape that actually arrived
            "{a: ".repeat(MAX_DEPTH + 1),
        ] {
            let err = flow(&text).expect_err("refused");
            assert!(
                err.contains("nested deeper than 200 levels"),
                "want a depth refusal, got {err}"
            );
        }
        // The count carries on from the block parser's, so a flow value is
        // not a fresh 200 levels hanging off an already-deep document.
        assert!(flow_at(MAX_DEPTH - 1, "[1]").is_ok());
        assert!(flow_at(MAX_DEPTH, "[1]").is_err());
    }

    fn flow_at(depth: usize, text: &str) -> Result<Yaml, String> {
        parse_flow(text, depth)
    }

    #[test]
    fn the_spellings_a_scene_is_written_in() {
        assert_eq!(
            flow("{towerL: candle, door: ember}"),
            Ok(m(&[
                ("towerL", Yaml::Str("candle".into())),
                ("door", Yaml::Str("ember".into())),
            ]))
        );
        assert_eq!(
            flow("[0.66, 0.2, 1.0, 0.1]"),
            Ok(Yaml::List(vec![
                Yaml::Float(0.66),
                Yaml::Float(0.2),
                Yaml::Float(1.0),
                Yaml::Float(0.1),
            ]))
        );
        assert_eq!(flow("[]"), Ok(Yaml::List(vec![])));
        assert_eq!(flow("{}"), Ok(Yaml::obj_empty()));
        // Nested, and a quoted value carrying the separators.
        assert_eq!(
            flow("{colors: [[1, 0], [0, 1]], note: \"roll, left: on\"}"),
            Ok(m(&[
                (
                    "colors",
                    Yaml::List(vec![
                        Yaml::List(vec![Yaml::Int(1), Yaml::Int(0)]),
                        Yaml::List(vec![Yaml::Int(0), Yaml::Int(1)]),
                    ])
                ),
                ("note", Yaml::Str("roll, left: on".into())),
            ]))
        );
        // A key with no value is null, exactly as PyYAML reads `{a}`, and
        // a plain scalar may hold spaces: PyYAML reads `[1 2]` as ['1 2'].
        assert_eq!(flow("{a}"), Ok(m(&[("a", Yaml::Null)])));
        assert_eq!(flow("[1 2]"), Ok(Yaml::List(vec![Yaml::Str("1 2".into())])));
    }

    #[test]
    fn an_unfinished_flow_is_a_complaint_not_a_guess() {
        assert!(flow("[1, 2").is_err());
        assert!(flow("{a: 1").is_err());
        assert!(flow("{a: \"x}").is_err());
        assert!(flow("[1] extra").is_err());
    }
}
