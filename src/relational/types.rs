use super::{config::Backfill, invalid};
use crate::error::Result;
use crate::hir::CheckedProgram;
use crate::value::{Type, Value, ValueKind};
use std::fmt::Write;

pub(crate) fn quote(n: &str) -> String {
    format!("\"{}\"", n.replace('"', "\"\""))
}
pub(crate) fn text_hex(s: &str) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(s.len() * 2);
    for b in s.bytes() {
        out.push(char::from(DIGITS[usize::from(b >> 4)]));
        out.push(char::from(DIGITS[usize::from(b & 15)]));
    }
    out
}
pub(crate) fn unhex(s: &str) -> Result<String> {
    if s.len() > 2 * crate::MAX_SOURCE_BYTES || !s.len().is_multiple_of(2) {
        return Err(invalid("invalid stored text size"));
    }
    let digit = |b: u8| match b {
        b'0'..=b'9' => Ok(b - b'0'),
        b'a'..=b'f' => Ok(b - b'a' + 10),
        _ => Err(invalid("invalid stored text encoding")),
    };
    let bytes = s
        .as_bytes()
        .chunks_exact(2)
        .map(|b| Ok(digit(b[0])? * 16 + digit(b[1])?))
        .collect::<Result<Vec<_>>>()?;
    String::from_utf8(bytes).map_err(|_| invalid("invalid stored Unicode"))
}
pub(crate) fn sql_type(ty: &Type) -> &'static str {
    match ty {
        Type::Option(t) => sql_type(t),
        Type::Int | Type::Decimal | Type::Timestamp | Type::Duration => "bigint",
        Type::Bool => "boolean",
        _ => "text",
    }
}
pub(crate) fn encoded(v: &Value) -> Result<Option<String>> {
    Ok(match &v.0 {
        ValueKind::None => None,
        ValueKind::Some(v) => return encoded(v),
        ValueKind::Int(n)
        | ValueKind::Decimal(n)
        | ValueKind::Timestamp(n)
        | ValueKind::Duration(n) => Some(n.to_string()),
        ValueKind::Bool(b) => Some(b.to_string()),
        ValueKind::Id(_, s) => Some(s.clone()),
        ValueKind::Text(s) => Some(text_hex(s)),
        ValueKind::Variant(_, name, values) if values.is_empty() => {
            Some(crate::project::resolve::public_name(name).into())
        }
        _ => return Err(invalid("non-scalar relational value")),
    })
}
pub(crate) fn decoded(text: Option<String>, ty: &Type, p: &CheckedProgram) -> Result<Value> {
    if let Type::Option(t) = ty {
        return text.map_or(Ok(Value(ValueKind::None)), |v| {
            Ok(Value(ValueKind::Some(Box::new(decoded(Some(v), t, p)?))))
        });
    }
    let s = text.ok_or_else(|| invalid("unexpected stored NULL"))?;
    let integer = || {
        s.parse::<i64>()
            .map_err(|_| invalid("invalid stored integer"))
    };
    let v = Value(match ty {
        Type::Int => ValueKind::Int(integer()?),
        Type::Decimal => ValueKind::Decimal(integer()?),
        Type::Timestamp => ValueKind::Timestamp(integer()?),
        Type::Duration => ValueKind::Duration(integer()?),
        Type::Bool => ValueKind::Bool(match s.as_str() {
            "true" => true,
            "false" => false,
            _ => return Err(invalid("invalid stored Boolean")),
        }),
        Type::Text | Type::BoundedText(..) => ValueKind::Text(unhex(&s)?),
        Type::Id(tag) => {
            if !crate::auth::valid_id(&s) {
                return Err(invalid("invalid stored Id"));
            }
            ValueKind::Id(tag.clone(), s)
        }
        Type::Enum(name) => {
            let tag = p.enums[name]
                .variants
                .iter()
                .find_map(|(tag, args)| {
                    (args.is_empty() && crate::project::resolve::public_name(tag) == s)
                        .then(|| tag.clone())
                })
                .ok_or_else(|| invalid("invalid stored enum"))?;
            ValueKind::Variant(name.clone(), tag, vec![])
        }
        _ => return Err(invalid("unsupported relational field")),
    });
    if !v.fits(ty) {
        return Err(invalid("stored field violates its declared bounds"));
    }
    Ok(v)
}
pub(crate) fn literal(v: &Value, ty: &Type) -> Result<String> {
    Ok(encoded(v)?.map_or_else(
        || "NULL".into(),
        |s| format!("'{}'::{}", s.replace('\'', "''"), sql_type(ty)),
    ))
}

// The ASCII hex domain makes PostgreSQL text lossless for all Diamond Unicode,
// including NUL. Hex order in C collation equals UTF-8/scalar lexical order.
const UTF8_HEX: &str = "^([0-7][0-9a-f]|(c[2-9a-f]|d[0-9a-f])[89ab][0-9a-f]|e0[ab][0-9a-f][89ab][0-9a-f]|e[1-9abc][89ab][0-9a-f][89ab][0-9a-f]|ed[89][0-9a-f][89ab][0-9a-f]|e[ef][89ab][0-9a-f][89ab][0-9a-f]|f0[9ab][0-9a-f][89ab][0-9a-f][89ab][0-9a-f]|f[123][89ab][0-9a-f][89ab][0-9a-f][89ab][0-9a-f]|f48[0-9a-f][89ab][0-9a-f][89ab][0-9a-f])*$";
pub(crate) fn check(column: &str, ty: &Type, p: &CheckedProgram) -> Result<String> {
    let q = quote(column);
    Ok(match ty {
        Type::Option(t) => format!("({q} IS NULL OR ({}))", check(column, t, p)?),
        Type::Text | Type::BoundedText(..) => {
            let mut rule = format!(
                "octet_length({q}) <= {} AND {q} ~ '{UTF8_HEX}'",
                2 * crate::MAX_SOURCE_BYTES
            );
            if let Type::BoundedText(min, max) = ty {
                write!(rule," AND (length(regexp_replace({q}, '([0-7c-f][0-9a-f])|([89ab][0-9a-f])', '\\1', 'g')) / 2) BETWEEN {min} AND {max}").map_err(|_|invalid("constraint formatting"))?;
            }
            rule
        }
        Type::Id(_) => format!("{q} ~ '^[-_A-Za-z0-9]{{1,128}}$'"),
        Type::Enum(n) => {
            let values = p.enums[n]
                .variants
                .keys()
                .map(|t| {
                    format!(
                        "'{}'",
                        crate::project::resolve::public_name(t).replace('\'', "''")
                    )
                })
                .collect::<Vec<_>>()
                .join(",");
            format!("{q} IN ({values})")
        }
        Type::Int | Type::Decimal | Type::Timestamp | Type::Duration | Type::Bool => "TRUE".into(),
        _ => return Err(invalid("unsupported column constraint")),
    })
}
pub(crate) fn backfill_value(b: &Backfill, ty: &Type, p: &CheckedProgram) -> Result<Value> {
    let Type::Option(t) = ty else {
        return Err(invalid("backfill requires optional field"));
    };
    let v = match (&b.value, t.as_ref()) {
        (toml::Value::Integer(n), Type::Int) => Value(ValueKind::Int(*n)),
        (toml::Value::Integer(n), Type::Decimal) => Value(ValueKind::Decimal(*n)),
        (toml::Value::Integer(n), Type::Timestamp) => Value(ValueKind::Timestamp(*n)),
        (toml::Value::Integer(n), Type::Duration) => Value(ValueKind::Duration(*n)),
        (toml::Value::Boolean(v), Type::Bool) => Value(ValueKind::Bool(*v)),
        (toml::Value::String(v), Type::Text | Type::BoundedText(..)) => {
            Value(ValueKind::Text(v.clone()))
        }
        (toml::Value::String(v), Type::Id(tag)) if crate::auth::valid_id(v) => {
            Value(ValueKind::Id(tag.clone(), v.clone()))
        }
        (toml::Value::String(v), Type::Enum(_)) => decoded(Some(v.clone()), t, p)?,
        _ => return Err(invalid("backfill constant does not match the field")),
    };
    if !v.fits(t) {
        return Err(invalid("backfill constant violates bounds"));
    }
    Ok(Value(ValueKind::Some(Box::new(v))))
}
#[derive(Default)]
pub(crate) struct Parameters(pub Vec<Option<String>>);
impl Parameters {
    pub fn add(&mut self, value: &Value, ty: &Type) -> Result<String> {
        self.0.push(encoded(value)?);
        Ok(format!("${}::text::{}", self.0.len(), sql_type(ty)))
    }
    pub fn raw(&mut self, value: &str) -> String {
        self.0.push(Some(value.into()));
        format!("${}::text", self.0.len())
    }
    pub fn bindings(&self) -> Vec<&(dyn tokio_postgres::types::ToSql + Sync)> {
        self.0
            .iter()
            .map(|v| v as &(dyn tokio_postgres::types::ToSql + Sync))
            .collect()
    }
}
