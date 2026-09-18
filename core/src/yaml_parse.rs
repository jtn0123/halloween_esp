//! The block half of the YAML subset — lines, indentation, `key: value`,
//! `- item`, `>`/`|` scalars. The value type, the scalar resolver and the
//! prose about what the subset is are in [`crate::yaml`]; flow collections
//! (`{a: b}`, `[1, 2]`, which may run over several lines) are in
//! [`crate::yaml_flow`]. Split three ways at the repo's 500-line cap.

use crate::yaml::{MAX_DEPTH, Yaml, YamlError, plain_key, scalar};
use crate::yaml_flow::{flow_end, parse_flow};
use crate::yaml_lines::{Line, key_colon, split_lines};

// --------------------------------------------------------------------------
// The block parser
// --------------------------------------------------------------------------

struct Parser {
    lines: Vec<Line>,
    at: usize,
}

/// Parse a whole document. An empty one is `Null`, as `yaml.safe_load("")` is.
pub fn parse(text: &str) -> Result<Yaml, YamlError> {
    let mut p = Parser {
        lines: split_lines(text)?,
        at: 0,
    };
    p.skip_blank();
    let Some(l) = p.lines.get(p.at) else {
        return Ok(Yaml::Null);
    };
    let indent = l.indent;
    // A document that is one bare scalar — `yaml.safe_load("just text")`
    // is a string, not a mapping, and the validator has a sentence for it.
    let bare = !(l.text == "-" || l.text.starts_with("- ")) && key_colon(&l.text).is_none();
    let v = if bare {
        let first = l.text.clone();
        p.scalar_value(first, 0)?
    } else {
        p.node(indent, 1)?
    };
    p.skip_blank();
    if let Some(l) = p.lines.get(p.at) {
        return Err(YamlError {
            line: l.no,
            msg: "unexpected indentation".into(),
        });
    }
    Ok(v)
}

impl Parser {
    fn skip_blank(&mut self) {
        while matches!(self.lines.get(self.at), Some(l) if l.blank) {
            self.at += 1;
        }
    }

    fn node(&mut self, indent: usize, depth: usize) -> Result<Yaml, YamlError> {
        let l = &self.lines[self.at];
        if l.text == "-" || l.text.starts_with("- ") {
            self.seq(indent, depth)
        } else {
            self.map(indent, depth)
        }
    }

    /// The level this collection sits at, against the ceiling neither half
    /// of the parser may recurse past (grade report 2026-09-17 B1). Block
    /// nesting is as reachable as flow nesting and cheaper to write: every
    /// two bytes of `- - - - …` on ONE line opens another sequence, and
    /// deeper indentation opens another mapping.
    fn too_deep(&self, depth: usize) -> Option<YamlError> {
        (depth > MAX_DEPTH).then(|| YamlError {
            line: self.lines.get(self.at).map_or(0, |l| l.no),
            msg: format!("nested deeper than {MAX_DEPTH} levels"),
        })
    }

    fn seq(&mut self, indent: usize, depth: usize) -> Result<Yaml, YamlError> {
        if let Some(e) = self.too_deep(depth) {
            return Err(e);
        }
        let mut out = Vec::new();
        loop {
            self.skip_blank();
            let Some(l) = self.lines.get(self.at) else {
                break;
            };
            if l.indent < indent {
                break;
            }
            if l.indent > indent {
                return Err(YamlError {
                    line: l.no,
                    msg: "unexpected indentation".into(),
                });
            }
            if !(l.text == "-" || l.text.starts_with("- ")) {
                break;
            }
            let after = l.text[1..].trim_start().to_string();
            let col = l.indent + (l.text.len() - after.len());
            if after.is_empty() {
                self.at += 1;
                self.skip_blank();
                match self.lines.get(self.at) {
                    Some(n) if n.indent > indent => {
                        let ni = n.indent;
                        out.push(self.node(ni, depth + 1)?);
                    }
                    _ => out.push(Yaml::Null),
                }
            } else if after == "-" || after.starts_with("- ") {
                // `- - 1`: a nested sequence opened on its parent's line.
                self.lines[self.at].indent = col;
                self.lines[self.at].text = after;
                out.push(self.seq(col, depth + 1)?);
            } else if key_colon(&after).is_some() {
                self.lines[self.at].indent = col;
                self.lines[self.at].text = after;
                out.push(self.map(col, depth + 1)?);
            } else {
                out.push(self.scalar_value(after, depth)?);
            }
        }
        Ok(Yaml::List(out))
    }

    fn map(&mut self, indent: usize, depth: usize) -> Result<Yaml, YamlError> {
        if let Some(e) = self.too_deep(depth) {
            return Err(e);
        }
        let mut out: Vec<(String, Yaml)> = Vec::new();
        loop {
            self.skip_blank();
            let Some(l) = self.lines.get(self.at) else {
                break;
            };
            if l.indent < indent {
                break;
            }
            if l.indent > indent {
                return Err(YamlError {
                    line: l.no,
                    msg: "unexpected indentation".into(),
                });
            }
            if l.text == "-" || l.text.starts_with("- ") {
                break;
            }
            let (no, text) = (l.no, l.text.clone());
            let Some(ci) = key_colon(&text) else {
                return Err(YamlError {
                    line: no,
                    msg: "expected \"key: value\"".into(),
                });
            };
            let key = plain_key(text[..ci].trim_end());
            let rest = text[ci + 1..].trim().to_string();
            let val = if rest.is_empty() {
                self.at += 1;
                self.skip_blank();
                match self.lines.get(self.at) {
                    Some(n) if n.indent > indent => {
                        let ni = n.indent;
                        self.node(ni, depth + 1)?
                    }
                    // A block sequence may sit at its own key's
                    // indentation — which is how PyYAML dumps one.
                    Some(n)
                        if n.indent == indent && (n.text == "-" || n.text.starts_with("- ")) =>
                    {
                        self.seq(indent, depth + 1)?
                    }
                    _ => Yaml::Null,
                }
            } else if rest.starts_with('>') || rest.starts_with('|') {
                Yaml::Str(self.block_scalar(indent, &rest))
            } else {
                self.scalar_value(rest, depth)?
            };
            match out.iter_mut().find(|(k, _)| *k == key) {
                Some(slot) => slot.1 = val, // last one wins, in the first one's place
                None => out.push((key, val)),
            }
        }
        Ok(Yaml::Map(out))
    }

    /// `key: >` / `key: |`, with the usual chomping indicators.
    fn block_scalar(&mut self, indent: usize, header: &str) -> String {
        let folded = header.starts_with('>');
        let chomp = header.chars().nth(1).unwrap_or(' ');
        self.at += 1;
        let mut body: Vec<Option<String>> = Vec::new();
        let mut base = usize::MAX;
        while let Some(l) = self.lines.get(self.at) {
            if l.blank {
                body.push(None);
                self.at += 1;
                continue;
            }
            if l.indent <= indent {
                break;
            }
            base = base.min(l.indent);
            body.push(Some(" ".repeat(l.indent) + &l.text));
            self.at += 1;
        }
        while matches!(body.last(), Some(None)) {
            body.pop();
        }
        let base = if base == usize::MAX { 0 } else { base };
        let mut out = String::new();
        for (i, line) in body.iter().enumerate() {
            match line {
                None => out.push('\n'),
                Some(t) => {
                    if i > 0 && folded && !out.ends_with('\n') {
                        out.push(' ');
                    } else if i > 0 && !folded {
                        out.push('\n');
                    }
                    out.push_str(&t[base.min(t.len())..]);
                }
            }
        }
        match chomp {
            '-' => out,
            _ if out.is_empty() => out,
            _ => out + "\n",
        }
    }

    /// A value that begins on this line: a flow collection (which may run
    /// on) or a single scalar. `depth` is what the block half has already
    /// spent, so the flow half carries the count on rather than starting
    /// a fresh allowance at the bracket.
    fn scalar_value(&mut self, first: String, depth: usize) -> Result<Yaml, YamlError> {
        let no = self.lines[self.at].no;
        if !(first.starts_with('{') || first.starts_with('[')) {
            let q = first.chars().next().filter(|c| *c == '"' || *c == '\'');
            if let Some(q) = q {
                if first.chars().count() < 2 || !first.ends_with(q) {
                    return Err(YamlError {
                        line: no,
                        msg: "unterminated quoted string".into(),
                    });
                }
            }
            self.at += 1;
            return Ok(scalar(&first));
        }
        let mut buf = first;
        loop {
            if let Some(end) = flow_end(&buf) {
                if !buf[end..].trim().is_empty() {
                    return Err(YamlError {
                        line: no,
                        msg: "trailing text after a flow collection".into(),
                    });
                }
                self.at += 1;
                return parse_flow(&buf[..end], depth).map_err(|msg| YamlError { line: no, msg });
            }
            self.at += 1;
            match self.lines.get(self.at) {
                Some(l) if !l.blank => {
                    buf.push(' ');
                    buf.push_str(&l.text);
                }
                _ => {
                    return Err(YamlError {
                        line: no,
                        msg: "unterminated flow collection".into(),
                    });
                }
            }
        }
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

    /// The shape the desk writes and scenes.yaml holds: a one-item block
    /// sequence whose item is a mapping, flow values inside it, a folded
    /// blurb, and comments anywhere.
    #[test]
    fn a_scene_block_parses_the_way_the_desk_writes_one() {
        let text = "  - id: trial   # the id\n\
                    \x20   # a whole-line comment\n\
                    \x20   name: Trial\n\
                    \x20   loop: true\n\
                    \x20   blurb: >\n\
                    \x20     Two folded lines that mean\n\
                    \x20     one sentence.\n\
                    \x20   base: {towerL: candle, door: 'off'}\n\
                    \x20   zones:\n\
                    \x20     towerL: {center: ember}\n\
                    \x20   cues: []\n";
        let want = Yaml::List(vec![m(&[
            ("id", Yaml::Str("trial".into())),
            ("name", Yaml::Str("Trial".into())),
            ("loop", Yaml::Bool(true)),
            (
                "blurb",
                Yaml::Str("Two folded lines that mean one sentence.\n".into()),
            ),
            (
                "base",
                m(&[
                    ("towerL", Yaml::Str("candle".into())),
                    ("door", Yaml::Str("off".into())),
                ]),
            ),
            (
                "zones",
                m(&[("towerL", m(&[("center", Yaml::Str("ember".into()))]))]),
            ),
            ("cues", Yaml::List(vec![])),
        ])]);
        assert_eq!(parse(text), Ok(want));
    }

    /// A block sequence may sit at its own key's indentation — which is
    /// how PyYAML dumps one, so the corpus is full of it — or indented
    /// under it, which is how the show is hand-written.
    #[test]
    fn a_sequence_may_share_its_keys_indentation_or_not() {
        let flat = "pulse:\n- synth: toll\n  zones:\n  - door\n";
        let deep = "pulse:\n  - synth: toll\n    zones:\n      - door\n";
        let want = m(&[(
            "pulse",
            Yaml::List(vec![m(&[
                ("synth", Yaml::Str("toll".into())),
                ("zones", Yaml::List(vec![Yaml::Str("door".into())])),
            ])]),
        )]);
        assert_eq!(parse(flat), Ok(want.clone()));
        assert_eq!(parse(deep), Ok(want));
        // A sequence of sequences opened on one line — PyYAML's spelling
        // for `colors: [[1, 0], [0, 1]]` when it dumps in block style.
        assert_eq!(
            parse("colors:\n- - 1\n  - 0\n- - 0\n  - 1\n"),
            Ok(m(&[(
                "colors",
                Yaml::List(vec![
                    Yaml::List(vec![Yaml::Int(1), Yaml::Int(0)]),
                    Yaml::List(vec![Yaml::Int(0), Yaml::Int(1)]),
                ])
            )]))
        );
    }

    /// A flow collection may run over several lines — the `zones:` block
    /// in scenes.yaml does, and it is the rig the whole show aims at.
    #[test]
    fn a_flow_value_may_run_past_the_end_of_its_line() {
        let text = "zones:\n  - {id: towerL, channel: 1, name: \"Tower, left\",\n     \
                    pin: 18, rgbw: true}\n";
        let parsed = parse(text).expect("parses");
        let zones = parsed.get("zones").and_then(Yaml::as_list).expect("a list");
        assert_eq!(zones.len(), 1);
        assert_eq!(zones[0].get("id").and_then(Yaml::as_str), Some("towerL"));
        assert_eq!(zones[0].get("rgbw"), Some(&Yaml::Bool(true)));
        assert_eq!(
            zones[0].get("name").and_then(Yaml::as_str),
            Some("Tower, left")
        );
    }

    /// A key written twice is the last one's value in the first one's
    /// place — Python's dict, which is what the error order follows.
    #[test]
    fn a_repeated_key_keeps_its_position_and_takes_the_last_value() {
        assert_eq!(
            parse("a: 1\nb: 2\na: 3\n"),
            Ok(m(&[("a", Yaml::Int(3)), ("b", Yaml::Int(2))]))
        );
    }

    /// An empty document is null; a bare scalar is itself. Both are things
    /// the splice route must answer for rather than crash on.
    #[test]
    fn the_degenerate_documents_still_parse() {
        assert_eq!(parse(""), Ok(Yaml::Null));
        assert_eq!(parse("\n# only a comment\n"), Ok(Yaml::Null));
        assert_eq!(parse("just text\n"), Ok(Yaml::Str("just text".into())));
        assert_eq!(parse("key:\n"), Ok(m(&[("key", Yaml::Null)])));
    }

    /// What it refuses, and where. The line number is what the desk shows
    /// after "scene is not valid YAML:".
    #[test]
    fn a_document_it_cannot_read_names_the_line() {
        let err = |t: &str| parse(t).expect_err("refused");
        assert_eq!(
            err("nonsense: [").to_string(),
            "line 1: unterminated flow collection"
        );
        assert_eq!(err("a: 1\n\tb: 2\n").line, 2);
        assert_eq!(
            err("a: 1\n  b: 2\n").to_string(),
            "line 2: unexpected indentation"
        );
        assert_eq!(
            err("a: 1\nnot a pair\n").to_string(),
            "line 2: expected \"key: value\""
        );
        assert_eq!(
            err("a: \"unclosed\n").to_string(),
            "line 1: unterminated quoted string"
        );
        assert_eq!(err("a: [1] extra\n").line, 1);
    }

    /// grade report 2026-09-17 B1: `node`/`seq`/`map` recursed once per
    /// level with nothing counting, and a blown stack ABORTS the studio
    /// rather than failing one request. Two bytes of `- ` per level is the
    /// cheapest way in — no indentation needed, all on one line — and the
    /// flow half shares the count, so a deep block cannot hand a deep flow
    /// value a fresh allowance.
    #[test]
    fn nesting_is_bounded_rather_than_fatal() {
        let dashes = |n: usize| format!("{}1\n", "- ".repeat(n));
        assert!(parse(&dashes(MAX_DEPTH)).is_ok());
        for text in [
            dashes(MAX_DEPTH + 1),
            dashes(100_000), // the cheap shape: 200 KB, no indentation
            format!("a: {}1{}\n", "[".repeat(100_000), "]".repeat(100_000)),
            // The indentation-driven shape, which costs 2 bytes more each
            // level rather than 2 flat, but arrives at the same cliff.
            (0..=MAX_DEPTH)
                .map(|i| format!("{}k:\n", " ".repeat(i * 2)))
                .collect(),
        ] {
            let err = parse(&text).expect_err("refused");
            assert!(
                err.msg.contains("nested deeper than 200 levels"),
                "want a depth refusal, got {err}"
            );
        }
        // A flow value one level below the ceiling still parses; the same
        // value one level deeper is refused by the flow half's copy of the
        // count, not by a second allowance.
        assert!(parse(&format!("{}[1]\n", "- ".repeat(MAX_DEPTH - 1))).is_ok());
        assert_eq!(
            parse(&format!("{}[1]\n", "- ".repeat(MAX_DEPTH)))
                .expect_err("refused")
                .msg,
            "nested deeper than 200 levels"
        );
    }
}
