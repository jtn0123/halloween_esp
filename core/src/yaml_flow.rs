//! Flow collections — `{a: b}` and `[1, 2]`, the spelling scenes.yaml and
//! the cue desk write most of a scene in. Split from [`crate::yaml_parse`]
//! at the repo's 500-line cap; the block parser calls in here whenever a
//! value opens with `{` or `[`, feeding it more lines until it closes.

use crate::yaml::{Yaml, plain_key, scalar};

struct Flow<'a> {
    s: &'a str,
    i: usize,
}

pub(crate) fn parse_flow(text: &str) -> Result<Yaml, String> {
    let mut f = Flow { s: text, i: 0 };
    let v = f.value()?;
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

    fn value(&mut self) -> Result<Yaml, String> {
        self.ws();
        match self.peek() {
            Some(b'{') => self.mapping(),
            Some(b'[') => self.sequence(),
            _ => Ok(scalar(self.token()?)),
        }
    }

    fn sequence(&mut self) -> Result<Yaml, String> {
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
            out.push(self.value()?);
            self.ws();
            match self.peek() {
                Some(b',') => self.i += 1,
                Some(b']') => {}
                _ => return Err("expected \",\" or \"]\" in a flow sequence".into()),
            }
        }
    }

    fn mapping(&mut self) -> Result<Yaml, String> {
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
                self.value()?
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

    #[test]
    fn the_spellings_a_scene_is_written_in() {
        assert_eq!(
            parse_flow("{towerL: candle, door: ember}"),
            Ok(m(&[
                ("towerL", Yaml::Str("candle".into())),
                ("door", Yaml::Str("ember".into())),
            ]))
        );
        assert_eq!(
            parse_flow("[0.66, 0.2, 1.0, 0.1]"),
            Ok(Yaml::List(vec![
                Yaml::Float(0.66),
                Yaml::Float(0.2),
                Yaml::Float(1.0),
                Yaml::Float(0.1),
            ]))
        );
        assert_eq!(parse_flow("[]"), Ok(Yaml::List(vec![])));
        assert_eq!(parse_flow("{}"), Ok(Yaml::obj_empty()));
        // Nested, and a quoted value carrying the separators.
        assert_eq!(
            parse_flow("{colors: [[1, 0], [0, 1]], note: \"roll, left: on\"}"),
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
        assert_eq!(parse_flow("{a}"), Ok(m(&[("a", Yaml::Null)])));
        assert_eq!(
            parse_flow("[1 2]"),
            Ok(Yaml::List(vec![Yaml::Str("1 2".into())]))
        );
    }

    #[test]
    fn an_unfinished_flow_is_a_complaint_not_a_guess() {
        assert!(parse_flow("[1, 2").is_err());
        assert!(parse_flow("{a: 1").is_err());
        assert!(parse_flow("{a: \"x}").is_err());
        assert!(parse_flow("[1] extra").is_err());
    }
}
