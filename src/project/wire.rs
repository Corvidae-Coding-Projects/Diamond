//! Strict, bounded managed-value codecs. Authority is never a serializable value.
use crate::error::{Error, Result};
use crate::hir::CheckedProgram;
use crate::value::{Failure, Type, Value, ValueKind};
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;

pub(crate) fn safe_type(ty: &Type, p: &CheckedProgram) -> bool {
    crate::security::type_security(ty, p).client_wire_safe
}
pub(crate) fn encode(value: &Value) -> Result<Json> {
    value.weight()?;
    encode_inner(value, 0)
}
fn encode_inner(value: &Value, depth: usize) -> Result<Json> {
    if depth > 32 {
        return Err(Error::Limit("wire depth"));
    }
    Ok(match &value.0 {
        ValueKind::Int(v) => Json::String(v.to_string()),
        ValueKind::Bool(v) => Json::Bool(*v),
        ValueKind::Text(v) => Json::String(v.clone()),
        ValueKind::Decimal(v) => json!({"tag":"Decimal","value":v.to_string()}),
        ValueKind::Timestamp(v) => json!({"tag":"Timestamp","value":v.to_string()}),
        ValueKind::Duration(v) => json!({"tag":"Duration","value":v.to_string()}),
        ValueKind::Unit => json!({"tag":"Unit"}),
        ValueKind::Id(tag, v) => json!({"tag":tag,"value":v}),
        ValueKind::Record(_, fields) => fields
            .iter()
            .map(|(k, v)| Ok((k.clone(), encode_inner(v, depth + 1)?)))
            .collect::<Result<serde_json::Map<_, _>>>()?
            .into(),
        ValueKind::Variant(_, name, fields) => {
            json!({"tag":super::resolve::public_name(name),"fields":fields.iter().map(|v|encode_inner(v,depth+1)).collect::<Result<Vec<_>>>()?})
        }
        ValueKind::List(values) => Json::Array(
            values
                .iter()
                .map(|v| encode_inner(v, depth + 1))
                .collect::<Result<_>>()?,
        ),
        ValueKind::None => json!({"tag":"None"}),
        ValueKind::Some(v) => json!({"tag":"Some","value":encode_inner(v,depth+1)?}),
        ValueKind::Ok(v) => json!({"tag":"Ok","value":encode_inner(v,depth+1)?}),
        ValueKind::AppErr(v) => json!({"tag":"Err","value":encode_inner(v,depth+1)?}),
        ValueKind::Err(f) => json!({"tag":"Err","value":failure_code(f)}),
        _ => return Err(Error::Input("unsupported wire value".into())),
    })
}
fn failure_code(f: &Failure) -> &str {
    match f {
        Failure::Denied => "Denied",
        Failure::Aborted => "Aborted",
        Failure::Conflict => "RetryableAbort",
        Failure::InvalidInput => "InvalidInput",
        Failure::Message(_) => "ApplicationFailure",
    }
}
fn bad() -> Error {
    Error::Input("wire value does not match its declared type".into())
}
fn integer(v: &Json) -> Result<i64> {
    let s = v.as_str().ok_or_else(bad)?;
    let n: i64 = s.parse().map_err(|_| bad())?;
    if n.to_string() != *s {
        return Err(bad());
    }
    Ok(n)
}
pub(crate) fn decode(ty: &Type, value: &Json, p: &CheckedProgram) -> Result<Value> {
    let v = decode_inner(ty, value, p, 0)?;
    v.weight()?;
    Ok(v)
}
fn decode_inner(ty: &Type, value: &Json, p: &CheckedProgram, depth: usize) -> Result<Value> {
    if depth > 32 {
        return Err(Error::Limit("wire depth"));
    }
    let object = |tag: &str, size: usize| {
        value
            .as_object()
            .filter(|v| v.len() == size && v.get("tag").and_then(Json::as_str) == Some(tag))
            .ok_or_else(bad)
    };
    let result = Value(match ty {
        Type::Int => ValueKind::Int(integer(value)?),
        Type::Bool => ValueKind::Bool(value.as_bool().ok_or_else(bad)?),
        Type::Text | Type::BoundedText(..) => {
            let s = value
                .as_str()
                .filter(|s| s.len() <= crate::MAX_SOURCE_BYTES)
                .ok_or_else(bad)?;
            ValueKind::Text(s.into())
        }
        Type::Decimal | Type::Timestamp | Type::Duration => {
            let tag = ty.to_string();
            let v = integer(object(&tag, 2)?.get("value").ok_or_else(bad)?)?;
            match ty {
                Type::Decimal => ValueKind::Decimal(v),
                Type::Timestamp => ValueKind::Timestamp(v),
                _ => ValueKind::Duration(v),
            }
        }
        Type::Unit => {
            object("Unit", 1)?;
            ValueKind::Unit
        }
        Type::Id(tag) => {
            let v = object(tag, 2)?
                .get("value")
                .and_then(Json::as_str)
                .ok_or_else(bad)?;
            ValueKind::Id(tag.clone(), v.into())
        }
        Type::List(inner) => {
            let vs = value
                .as_array()
                .filter(|v| v.len() <= 1000)
                .ok_or_else(bad)?;
            ValueKind::List(
                vs.iter()
                    .map(|v| decode_inner(inner, v, p, depth + 1))
                    .collect::<Result<_>>()?,
            )
        }
        Type::Option(inner) => {
            if object("None", 1).is_ok() {
                ValueKind::None
            } else {
                ValueKind::Some(Box::new(decode_inner(
                    inner,
                    object("Some", 2)?.get("value").ok_or_else(bad)?,
                    p,
                    depth + 1,
                )?))
            }
        }
        Type::Result(inner) | Type::AppResult(inner, _) => {
            if let Ok(v) = object("Ok", 2) {
                ValueKind::Ok(Box::new(decode_inner(
                    inner,
                    v.get("value").ok_or_else(bad)?,
                    p,
                    depth + 1,
                )?))
            } else {
                let v = object("Err", 2)?.get("value").ok_or_else(bad)?;
                if let Type::AppResult(_, error) = ty {
                    ValueKind::AppErr(Box::new(decode_inner(error, v, p, depth + 1)?))
                } else {
                    ValueKind::Err(match v.as_str() {
                        Some("Denied") => Failure::Denied,
                        Some("InvalidInput") => Failure::InvalidInput,
                        Some("Aborted") => Failure::Aborted,
                        Some("RetryableAbort") => Failure::Conflict,
                        Some("ApplicationFailure") => {
                            Failure::Message("Application failed.".into())
                        }
                        _ => return Err(bad()),
                    })
                }
            }
        }
        Type::Record(n) => decode_record(n, value, p, depth)?,
        Type::Enum(n) => decode_enum(n, value, p, depth)?,
        Type::Action(success, error) => decode_action(success, error, value, p, depth)?,
        _ => return Err(bad()),
    });
    if !result.fits(ty) {
        return Err(bad());
    }
    Ok(result)
}

fn decode_action(
    success: &Type,
    error: &Type,
    value: &Json,
    p: &CheckedProgram,
    depth: usize,
) -> Result<ValueKind> {
    let object = value
        .as_object()
        .filter(|value| value.len() == 2)
        .ok_or_else(bad)?;
    let tag = object
        .get("tag")
        .and_then(Json::as_str)
        .filter(|tag| !matches!(*tag, "ActionIdle" | "ActionSubmitting"))
        .ok_or_else(bad)?;
    let fields = object
        .get("fields")
        .and_then(Json::as_array)
        .ok_or_else(bad)?;
    let cases = crate::action::cases(success, error);
    let types = cases
        .get(tag)
        .filter(|types| types.len() == fields.len())
        .ok_or_else(bad)?;
    Ok(ValueKind::Variant(
        crate::action::ENUM.into(),
        tag.into(),
        types
            .iter()
            .zip(fields)
            .map(|(ty, value)| decode_inner(ty, value, p, depth + 1))
            .collect::<Result<_>>()?,
    ))
}

fn decode_record(n: &str, value: &Json, p: &CheckedProgram, depth: usize) -> Result<ValueKind> {
    Ok({
        let def = p.records.get(n).ok_or_else(bad)?;
        let v = value
            .as_object()
            .filter(|v| v.len() == def.fields.len())
            .ok_or_else(bad)?;
        ValueKind::Record(
            n.into(),
            def.fields
                .iter()
                .map(|(n, f)| {
                    Ok((
                        n.clone(),
                        decode_inner(&f.ty, v.get(n).ok_or_else(bad)?, p, depth + 1)?,
                    ))
                })
                .collect::<Result<BTreeMap<_, _>>>()?,
        )
    })
}
fn decode_enum(n: &str, value: &Json, p: &CheckedProgram, depth: usize) -> Result<ValueKind> {
    Ok({
        let v = value.as_object().filter(|v| v.len() == 2).ok_or_else(bad)?;
        let tag = v.get("tag").and_then(Json::as_str).ok_or_else(bad)?;
        let (internal, def) = p
            .enums
            .get(n)
            .and_then(|e| {
                e.variants
                    .iter()
                    .find(|(n, _)| super::resolve::public_name(n) == tag)
            })
            .ok_or_else(bad)?;
        let fields = v
            .get("fields")
            .and_then(Json::as_array)
            .filter(|f| f.len() == def.len())
            .ok_or_else(bad)?;
        ValueKind::Variant(
            n.into(),
            internal.clone(),
            def.iter()
                .zip(fields)
                .map(|((_, t), v)| decode_inner(t, v, p, depth + 1))
                .collect::<Result<_>>()?,
        )
    })
}

/// Reject repeated object keys at every level before typed decoding.
pub(crate) fn strict_from_slice<T: serde::de::DeserializeOwned>(
    bytes: &[u8],
) -> std::result::Result<T, serde_json::Error> {
    use serde::Deserialize;
    struct Strict(Json);
    impl<'de> Deserialize<'de> for Strict {
        fn deserialize<D: serde::Deserializer<'de>>(
            deserializer: D,
        ) -> std::result::Result<Self, D::Error> {
            struct Visitor;
            impl<'de> serde::de::Visitor<'de> for Visitor {
                type Value = Strict;
                fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                    f.write_str("JSON with unique object keys")
                }
                fn visit_bool<E: serde::de::Error>(
                    self,
                    v: bool,
                ) -> std::result::Result<Strict, E> {
                    Ok(Strict(Json::Bool(v)))
                }
                fn visit_i64<E: serde::de::Error>(self, v: i64) -> std::result::Result<Strict, E> {
                    Ok(Strict(v.into()))
                }
                fn visit_u64<E: serde::de::Error>(self, v: u64) -> std::result::Result<Strict, E> {
                    Ok(Strict(v.into()))
                }
                fn visit_f64<E: serde::de::Error>(self, v: f64) -> std::result::Result<Strict, E> {
                    Ok(Strict(
                        serde_json::Number::from_f64(v)
                            .ok_or_else(|| E::custom("nonfinite JSON"))?
                            .into(),
                    ))
                }
                fn visit_str<E: serde::de::Error>(self, v: &str) -> std::result::Result<Strict, E> {
                    Ok(Strict(v.into()))
                }
                fn visit_string<E: serde::de::Error>(
                    self,
                    v: String,
                ) -> std::result::Result<Strict, E> {
                    Ok(Strict(v.into()))
                }
                fn visit_unit<E: serde::de::Error>(self) -> std::result::Result<Strict, E> {
                    Ok(Strict(Json::Null))
                }
                fn visit_seq<A: serde::de::SeqAccess<'de>>(
                    self,
                    mut seq: A,
                ) -> std::result::Result<Strict, A::Error> {
                    let mut vs = Vec::new();
                    while let Some(Strict(v)) = seq.next_element()? {
                        vs.push(v);
                    }
                    Ok(Strict(Json::Array(vs)))
                }
                fn visit_map<A: serde::de::MapAccess<'de>>(
                    self,
                    mut map: A,
                ) -> std::result::Result<Strict, A::Error> {
                    let mut vs = serde_json::Map::new();
                    while let Some((k, Strict(v))) = map.next_entry::<String, Strict>()? {
                        if vs.insert(k, v).is_some() {
                            return Err(serde::de::Error::custom("duplicate JSON key"));
                        }
                    }
                    Ok(Strict(vs.into()))
                }
            }
            deserializer.deserialize_any(Visitor)
        }
    }
    let value: Strict = serde_json::from_slice(bytes)?;
    serde_json::from_value(value.0)
}
