//! Indentation-aware tokenization with a separate typed-template entry point.

use crate::error::{Error, Location, Result};

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Kind {
    Name(String),
    Text(String),
    Int(i64),
    Symbol(String),
    Html(String),
    Newline,
    Indent,
    Dedent,
    End,
}

#[derive(Clone, Debug)]
pub(crate) struct Token {
    pub kind: Kind,
    pub location: Location,
}

pub(crate) fn lex(source: &str) -> Result<Vec<Token>> {
    if source.len() > crate::MAX_SOURCE_BYTES {
        return Err(Error::Limit("source size"));
    }
    let lines: Vec<_> = source.lines().collect();
    let mut tokens = Vec::new();
    let mut indentation = vec![0];
    let mut index = 0;
    while index < lines.len() {
        let line = lines[index];
        let number = index + 1;
        index += 1;
        if let Some(tab) = line.chars().position(|character| character == '\t') {
            return Err(Error::diagnostic(
                "D001",
                Location {
                    line: number,
                    column: tab + 1,
                },
                "tabs are not supported; indent with spaces",
            ));
        }
        let trimmed = line.trim_start();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let spaces = line.len() - trimmed.len();
        let location = Location {
            line: number,
            column: spaces + 1,
        };
        if spaces > *indentation.last().ok_or(Error::Limit("indentation"))? {
            if indentation.len() >= 64 {
                return Err(Error::Limit("indentation depth"));
            }
            indentation.push(spaces);
            tokens.push(Token {
                kind: Kind::Indent,
                location,
            });
        } else {
            while spaces < *indentation.last().ok_or(Error::Limit("indentation"))? {
                indentation.pop();
                tokens.push(Token {
                    kind: Kind::Dedent,
                    location,
                });
            }
            if indentation.last() != Some(&spaces) {
                return Err(Error::diagnostic(
                    "D002",
                    location,
                    "indentation does not match an enclosing block",
                ));
            }
        }
        if trimmed == "html:" {
            tokens.push(Token {
                kind: Kind::Html(collect_template(&lines, &mut index, spaces, location)?),
                location,
            });
        } else {
            tokenize_line(trimmed, location, &mut tokens)?;
        }
        tokens.push(Token {
            kind: Kind::Newline,
            location: Location {
                line: number,
                column: line.chars().count() + 1,
            },
        });
        if tokens.len() > 200_000 {
            return Err(Error::Limit("token count"));
        }
    }
    let location = Location {
        line: lines.len() + 1,
        column: 1,
    };
    for _ in 1..indentation.len() {
        tokens.push(Token {
            kind: Kind::Dedent,
            location,
        });
    }
    tokens.push(Token {
        kind: Kind::End,
        location,
    });
    Ok(tokens)
}

fn collect_template(
    lines: &[&str],
    index: &mut usize,
    spaces: usize,
    location: Location,
) -> Result<String> {
    let start = *index;
    while *index < lines.len() {
        let next = lines[*index];
        if !next.trim().is_empty() && next.len() - next.trim_start().len() <= spaces {
            break;
        }
        if let Some(tab) = next.chars().position(|character| character == '\t') {
            return Err(Error::diagnostic(
                "D001",
                Location {
                    line: *index + 1,
                    column: tab + 1,
                },
                "tabs are not supported; indent with spaces",
            ));
        }
        *index += 1;
    }
    if start == *index {
        return Err(Error::diagnostic(
            "D003",
            location,
            "html requires an indented template",
        ));
    }
    Ok(lines[start..*index].join("\n"))
}

fn tokenize_line(line: &str, location: Location, tokens: &mut Vec<Token>) -> Result<()> {
    let chars: Vec<char> = line.chars().collect();
    let mut cursor = 0;
    while cursor < chars.len() {
        let c = chars[cursor];
        let start = cursor;
        cursor += 1;
        let here = Location {
            line: location.line,
            column: location.column + start,
        };
        if c.is_whitespace() {
            continue;
        }
        if c == '#' {
            break;
        }
        let kind = if c.is_ascii_alphabetic() || c == '_' {
            while cursor < chars.len()
                && (chars[cursor].is_ascii_alphanumeric() || chars[cursor] == '_')
            {
                cursor += 1;
            }
            Kind::Name(chars[start..cursor].iter().collect())
        } else if c.is_ascii_digit() {
            while cursor < chars.len() && (chars[cursor].is_ascii_digit() || chars[cursor] == '_') {
                cursor += 1;
            }
            if let Some(invalid) = (start..cursor).find(|index| {
                chars[*index] == '_'
                    && (!index
                        .checked_sub(1)
                        .and_then(|previous| chars.get(previous))
                        .is_some_and(char::is_ascii_digit)
                        || !chars.get(*index + 1).is_some_and(char::is_ascii_digit))
            }) {
                return Err(Error::diagnostic(
                    "D004",
                    Location {
                        line: here.line,
                        column: location.column + invalid,
                    },
                    "integer separators must be single underscores between digits",
                ));
            }
            let raw: String = chars[start..cursor].iter().filter(|c| **c != '_').collect();
            Kind::Int(raw.parse().map_err(|_| {
                Error::diagnostic("D004", here, "integer exceeds signed 64-bit range")
            })?)
        } else if c == '"' {
            Kind::Text(read_string(&chars, &mut cursor, here)?)
        } else if "[]():,=.<>+-*/?!@|".contains(c) {
            let mut symbol = c.to_string();
            if let Some(next) = chars.get(cursor) {
                let pair = format!("{c}{next}");
                if ["==", "!=", "<=", ">=", "->", "..", "=>"].contains(&pair.as_str()) {
                    symbol = pair;
                    cursor += 1;
                }
            }
            Kind::Symbol(symbol)
        } else {
            return Err(Error::unsupported(here, format!("token {c:?}")));
        };
        tokens.push(Token {
            kind,
            location: here,
        });
    }
    Ok(())
}

fn read_string(chars: &[char], cursor: &mut usize, location: Location) -> Result<String> {
    let mut result = String::new();
    while let Some(c) = chars.get(*cursor) {
        *cursor += 1;
        match c {
            '"' => return Ok(result),
            '\\' => {
                let escaped = chars.get(*cursor).ok_or_else(|| {
                    Error::diagnostic("D005", location, "unfinished string escape")
                })?;
                *cursor += 1;
                result.push(match escaped {
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    '"' => '"',
                    '\\' => '\\',
                    _ => {
                        return Err(Error::unsupported(
                            location,
                            "string escape; supported escapes are n, r, t, quote, and backslash",
                        ));
                    }
                });
            }
            _ => result.push(*c),
        }
    }
    Err(Error::diagnostic(
        "D005",
        location,
        "unterminated string literal",
    ))
}
