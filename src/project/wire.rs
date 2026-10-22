//! Strict version-one codecs; data never carries identity or host authority.

use crate::error::{Error, Result};
use crate::hir::CheckedProgram;
use crate::value::{Type, Value, ValueKind};
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;

pub(super) fn scalar(ty: &Type) -> bool {
    matches!(
        ty,
        Type::Int | Type::Bool | Type::Text | Type::BoundedText(_, _)
    )
}

pub(super) fn encode(value: &Value) -> Result<Json> {
    value.weight()?;
    Ok(match &value.0 {
        ValueKind::Int(v) => Json::String(v.to_string()),
        ValueKind::Bool(v) => Json::Bool(*v),
        ValueKind::Text(v) => Json::String(v.clone()),
        ValueKind::Id(tag, value) => json!({"tag":tag,"value":value}),
        ValueKind::Record(_, fields) => fields
            .iter()
            .map(|(k, v)| Ok((k.clone(), encode(v)?)))
            .collect::<Result<serde_json::Map<_, _>>>()?
            .into(),
        ValueKind::None => json!({"tag":"None"}),
        ValueKind::Some(v) => json!({"tag":"Some","value":encode(v)?}),
        _ => return Err(Error::Input("unsupported wire value".into())),
    })
}

pub(super) fn decode(ty: &Type, value: &Json, program: &CheckedProgram) -> Result<Value> {
    let bad = || Error::Input("wire value does not match its declared type".into());
    let result = Value(match (ty, value) {
        (Type::Int, Json::String(v)) => {
            let n: i64 = v.parse().map_err(|_| bad())?;
            if n.to_string() != *v {
                return Err(bad());
            }
            ValueKind::Int(n)
        }
        (Type::Bool, Json::Bool(v)) => ValueKind::Bool(*v),
        (Type::Text | Type::BoundedText(_, _), Json::String(v))
            if v.len() <= crate::MAX_SOURCE_BYTES =>
        {
            ValueKind::Text(v.clone())
        }
        (Type::Id(expected), Json::Object(v))
            if v.len() == 2 && v.get("tag").and_then(Json::as_str) == Some(expected) =>
        {
            ValueKind::Id(
                expected.clone(),
                v.get("value")
                    .and_then(Json::as_str)
                    .ok_or_else(bad)?
                    .into(),
            )
        }
        (Type::Option(inner), Json::Object(v)) => match v.get("tag").and_then(Json::as_str) {
            Some("None") if v.len() == 1 => ValueKind::None,
            Some("Some") if v.len() == 2 => ValueKind::Some(Box::new(decode(
                inner,
                v.get("value").ok_or_else(bad)?,
                program,
            )?)),
            _ => return Err(bad()),
        },
        (Type::Record(name), Json::Object(v)) => {
            let record = program.records.get(name).ok_or_else(bad)?;
            if record.fields.len() != v.len() {
                return Err(bad());
            }
            let fields = record
                .fields
                .iter()
                .map(|(k, f)| {
                    Ok((
                        k.clone(),
                        decode(&f.ty, v.get(k).ok_or_else(bad)?, program)?,
                    ))
                })
                .collect::<Result<BTreeMap<_, _>>>()?;
            ValueKind::Record(name.clone(), fields)
        }
        _ => return Err(bad()),
    });
    if !result.fits(ty) {
        return Err(bad());
    }
    result.weight()?;
    Ok(result)
}
