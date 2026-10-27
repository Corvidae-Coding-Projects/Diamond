//! Canonical, single-decoding reactive routes and query fields.

use super::{Field, field_name};
use crate::error::{Error, Result};
use crate::hir::{CheckedProgram, Record};
use crate::scalar_input::Scalar;
use crate::value::{Value, ValueKind};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize)]
pub(in crate::project) enum Segment {
    Literal(String),
    Parameter(String, Field),
}

#[derive(Clone, Debug, Serialize)]
pub(in crate::project) struct Route {
    pub segments: Vec<Segment>,
    pub query: BTreeMap<String, Field>,
}

impl Route {
    pub(super) fn checked(
        source: &crate::syntax::ReactiveRoute,
        record: &Record,
        program: &CheckedProgram,
    ) -> Result<Self> {
        let invalid = || {
            Error::diagnostic(
                "D331",
                source.location,
                "route requires distinct supported scalar fields, a literal namespace prefix, and a canonical local path/query template",
            )
        };
        let (path, query) = source
            .path
            .split_once('?')
            .map_or((source.path.as_str(), None), |(path, query)| {
                (path, Some(query))
            });
        if path.len() > 2048
            || !path.starts_with('/')
            || path.ends_with('/')
            || path.contains(['%', '#', '\\'])
            || path.split('/').count() > 17
            || ["/_diamond", "/assets", "/api", "/v2"]
                .iter()
                .any(|prefix| path == *prefix || path.starts_with(&format!("{prefix}/")))
        {
            return Err(invalid());
        }
        let mut found = BTreeSet::new();
        let mut segments = Vec::new();
        for segment in path[1..].split('/') {
            if let Some(name) = parameter(segment) {
                let original = record.fields.get(name).ok_or_else(invalid)?;
                let field = Field::checked(original, program)?;
                if !field_name(name)
                    || !found.insert(name.to_owned())
                    || original.default.is_some()
                    || matches!(field.codec, Scalar::Optional { .. })
                    || !url_scalar(&field.codec)
                {
                    return Err(invalid());
                }
                segments.push(Segment::Parameter(name.into(), field));
            } else {
                if segment.is_empty()
                    || !segment
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
                {
                    return Err(invalid());
                }
                segments.push(Segment::Literal(segment.into()));
            }
        }
        if found.len() > 8 || !matches!(segments.first(), Some(Segment::Literal(_))) {
            return Err(invalid());
        }
        let mut fields = BTreeMap::new();
        if let Some(query) = query {
            for pair in query.split('&') {
                let (key, binding) = pair.split_once('=').ok_or_else(invalid)?;
                let name = parameter(binding)
                    .filter(|name| *name == key)
                    .ok_or_else(invalid)?;
                let original = record.fields.get(name).ok_or_else(invalid)?;
                let field = Field::checked(original, program)?;
                if matches!(field.codec, Scalar::Optional { .. })
                    && original
                        .default
                        .as_ref()
                        .is_some_and(|value| !matches!(value.0, ValueKind::None))
                {
                    return Err(invalid());
                }
                if !field_name(name)
                    || !found.insert(name.into())
                    || !url_scalar(&field.codec)
                    || field
                        .default
                        .as_ref()
                        .is_some_and(|raw| !safe_value(raw, false))
                {
                    return Err(invalid());
                }
                fields.insert(key.into(), field);
            }
        }
        if found.len() != record.fields.len() || fields.len() > 16 {
            return Err(invalid());
        }
        Ok(Self {
            segments,
            query: fields,
        })
    }

    pub(super) fn decode(&self, name: &str, target: &str) -> Option<Value> {
        if target.len() > 2048 || !target.starts_with('/') {
            return None;
        }
        let (path, query) = target
            .split_once('?')
            .map_or((target, None), |(p, q)| (p, Some(q)));
        let parts: Vec<_> = path[1..].split('/').collect();
        if parts.len() != self.segments.len() {
            return None;
        }
        let mut fields = BTreeMap::new();
        for (part, segment) in parts.iter().zip(&self.segments) {
            match segment {
                Segment::Literal(literal) if part == literal => {}
                Segment::Parameter(name, field) => {
                    let raw = decode(part)?;
                    if !safe_value(&raw, true) {
                        return None;
                    }
                    fields.insert(name.clone(), field.codec.decode(&raw).ok()?);
                }
                Segment::Literal(_) => return None,
            }
        }
        let mut supplied = BTreeMap::new();
        if let Some(query) = query {
            for pair in query.split('&') {
                let (key, value) = pair.split_once('=')?;
                let key = decode(key)?;
                if !self.query.contains_key(&key) || supplied.insert(key, decode(value)?).is_some()
                {
                    return None;
                }
            }
        }
        for (name, field) in &self.query {
            let value = if let Some(raw) = supplied.get(name) {
                if !safe_value(raw, false)
                    || matches!(field.codec, Scalar::Optional { .. }) && raw.is_empty()
                {
                    return None;
                }
                field.codec.decode(raw).ok()?
            } else {
                field.absent()?
            };
            fields.insert(name.clone(), value);
        }
        if self.url(&fields)?.as_str() != target {
            return None;
        }
        Some(Value(ValueKind::Record(name.into(), fields)))
    }

    pub(super) fn url(&self, fields: &BTreeMap<String, Value>) -> Option<String> {
        let mut output = String::new();
        for segment in &self.segments {
            output.push('/');
            match segment {
                Segment::Literal(value) => output.push_str(value),
                Segment::Parameter(name, field) => {
                    let value = fields.get(name).filter(|value| value.fits(&field.ty))?;
                    let raw = field.codec.format(value)?;
                    if !safe_value(&raw, true) {
                        return None;
                    }
                    output.push_str(&encode(&raw));
                }
            }
        }
        let mut separator = '?';
        for (name, field) in &self.query {
            let value = fields.get(name).filter(|value| value.fits(&field.ty))?;
            if matches!(value.0, ValueKind::None) {
                continue;
            }
            let raw = field.codec.format(value)?;
            if !safe_value(&raw, false)
                || matches!(field.codec, Scalar::Optional { .. }) && raw.is_empty()
            {
                return None;
            }
            if field.default.as_ref() == Some(&raw) {
                continue;
            }
            output.push(separator);
            separator = '&';
            output.push_str(name);
            output.push('=');
            output.push_str(&encode(&raw));
        }
        (output.len() <= 2048).then_some(output)
    }

    pub(super) fn overlaps(&self, other: &Self) -> bool {
        self.segments.len() == other.segments.len()
            && self
                .segments
                .iter()
                .zip(&other.segments)
                .all(|(a, b)| !matches!((a,b),(Segment::Literal(a),Segment::Literal(b)) if a != b))
    }

    pub(super) fn overlaps_literal(&self, path: &str) -> bool {
        let parts: Vec<_> = path.trim_start_matches('/').split('/').collect();
        self.segments.len() == parts.len()
            && self.segments.iter().zip(parts).all(
                |(segment, part)| !matches!(segment, Segment::Literal(literal) if literal != part),
            )
    }
}

fn parameter(value: &str) -> Option<&str> {
    value.strip_prefix('{')?.strip_suffix('}')
}

fn url_scalar(codec: &Scalar) -> bool {
    match codec {
        Scalar::Text { max, .. } => *max <= 256,
        Scalar::Optional { inner } => url_scalar(inner),
        _ => true,
    }
}

fn safe_value(raw: &str, path: bool) -> bool {
    (!path || !raw.is_empty() && ![".", ".."].contains(&raw))
        && !raw
            .chars()
            .any(|c| c.is_control() || ['/', '\\', '%', '#', '?'].contains(&c))
}

fn encode(value: &str) -> String {
    use std::fmt::Write;
    let mut output = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            output.push(char::from(byte));
        } else {
            let _ = write!(output, "%{byte:02X}");
        }
    }
    output
}

fn decode(value: &str) -> Option<String> {
    // Form decoding maps '+' to space. URL canonicality forbids '+' outright.
    if value.contains('+') {
        return None;
    }
    let decoded = crate::forms::decode(value).ok()?;
    (encode(&decoded) == value).then_some(decoded)
}
