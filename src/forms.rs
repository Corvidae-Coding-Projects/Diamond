//! Strict bounded form decoding derived from checked record declarations.

use crate::error::{Error, Result};
use crate::hir::Record;
use crate::value::{Type, Value, ValueKind};
use std::collections::BTreeMap;

pub(crate) fn parse(body: &[u8], name: &str, record: &Record) -> Result<Value> {
    if body.len() > 65_536 {
        return Err(Error::Limit("form body"));
    }
    let mut input = fields(body)?;
    // The runtime owns CSRF verification; this reserved field is not domain input.
    input.remove("_diamond_csrf");
    if input.keys().any(|name| !record.fields.contains_key(name)) {
        return Err(Error::Input("unknown form field".into()));
    }
    record_value(&input, name, record)
}

pub(crate) fn fields(body: &[u8]) -> Result<BTreeMap<String, String>> {
    if body.len() > 65_536 {
        return Err(Error::Limit("form body"));
    }
    let source =
        std::str::from_utf8(body).map_err(|_| Error::Input("form body must be UTF-8".into()))?;
    let mut input = BTreeMap::new();
    if !source.is_empty() {
        for pair in source.split('&') {
            let (key, value) = pair
                .split_once('=')
                .ok_or_else(|| Error::Input("form field requires =".into()))?;
            let key = decode(key)?;
            if input.insert(key, decode(value)?).is_some() {
                return Err(Error::Input("duplicate form field".into()));
            }
        }
    }
    Ok(input)
}

fn record_value(input: &BTreeMap<String, String>, name: &str, record: &Record) -> Result<Value> {
    let mut fields = BTreeMap::new();
    for (name, field) in &record.fields {
        if field.created_by {
            return Err(Error::Input(
                "forms cannot contain server-derived fields".into(),
            ));
        }
        let value = match input.get(name) {
            Some(value) => scalar(value, &field.ty)?,
            None => field
                .default
                .clone()
                .or({
                    if matches!(field.ty, Type::Option(_)) {
                        Some(Value(ValueKind::None))
                    } else {
                        None
                    }
                })
                .ok_or_else(|| Error::Input(format!("missing form field {name}")))?,
        };
        fields.insert(name.clone(), value);
    }
    Ok(Value(ValueKind::Record(name.into(), fields)))
}

pub(crate) fn is_form(content_type: Option<&str>) -> bool {
    content_type.is_some_and(|value| {
        value.split(';').next().is_some_and(|kind| {
            kind.trim()
                .eq_ignore_ascii_case("application/x-www-form-urlencoded")
        })
    })
}

pub(crate) fn csrf_hidden(token: &str) -> String {
    format!(
        "<input type=\"hidden\" name=\"_diamond_csrf\" value=\"{}\">",
        crate::template::escape(token)
    )
}

fn scalar(raw: &str, ty: &Type) -> Result<Value> {
    let value = Value(match ty {
        Type::Text | Type::BoundedText(_, _) => ValueKind::Text(raw.into()),
        Type::Int => ValueKind::Int(
            raw.parse()
                .map_err(|_| Error::Input("invalid integer field".into()))?,
        ),
        Type::Bool => match raw {
            "true" => ValueKind::Bool(true),
            "false" => ValueKind::Bool(false),
            _ => return Err(Error::Input("Boolean field requires true or false".into())),
        },
        Type::Id(tag) => ValueKind::Id(tag.clone(), raw.into()),
        Type::Option(inner) => {
            return if raw.is_empty() {
                Ok(Value(ValueKind::None))
            } else {
                Ok(Value(ValueKind::Some(Box::new(scalar(raw, inner)?))))
            };
        }
        _ => {
            return Err(Error::unsupported(
                crate::Location::default(),
                format!("form field type {ty}"),
            ));
        }
    });
    if !value.fits(ty) {
        return Err(Error::Input("form field violates declared bounds".into()));
    }
    Ok(value)
}

pub(crate) fn decode(value: &str) -> Result<String> {
    let mut output = Vec::with_capacity(value.len());
    let mut bytes = value.bytes();
    while let Some(byte) = bytes.next() {
        match byte {
            b'+' => output.push(b' '),
            b'%' => {
                let a = bytes
                    .next()
                    .and_then(|c| char::from(c).to_digit(16))
                    .ok_or_else(|| Error::Input("invalid percent encoding".into()))?;
                let b = bytes
                    .next()
                    .and_then(|c| char::from(c).to_digit(16))
                    .ok_or_else(|| Error::Input("invalid percent encoding".into()))?;
                output.push(
                    u8::try_from(a * 16 + b)
                        .map_err(|_| Error::Input("invalid percent encoding".into()))?,
                );
            }
            _ => output.push(byte),
        }
    }
    String::from_utf8(output).map_err(|_| Error::Input("invalid UTF-8 field".into()))
}
