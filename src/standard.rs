//! Pure bounded utilities shared by reference and browser evaluation.

use crate::error::{Error, Result};
use crate::value::{Type, Value, ValueKind};

pub(crate) const NAMES: &[&str] = &[
    "decimal_units",
    "decimal_text",
    "decimal_add",
    "decimal_subtract",
    "decimal_multiply",
    "decimal_divide",
    "timestamp",
    "duration",
    "time_millis",
    "duration_millis",
    "timestamp_add",
    "timestamp_diff",
    "text_slice",
    "text_contains",
    "text_trim",
    "text_replace",
    "list_append",
    "list_reverse",
    "list_slice",
    "list_get",
    "int_text",
    "join_text",
    "id_text",
    "markdown",
    "stream",
];
pub(crate) fn is_builtin(name: &str) -> bool {
    NAMES.contains(&name)
}
pub(crate) fn signature(name: &str, args: &[Type]) -> Option<Type> {
    use Type::{Bool, Decimal, Duration, Html, Int, List, Option as Optional, Text, Timestamp};
    let fixed = match name {
        "decimal_units" => (vec![Int], Decimal),
        "decimal_text" => (vec![Decimal], Text),
        "decimal_add" | "decimal_subtract" => (vec![Decimal, Decimal], Decimal),
        "decimal_multiply" | "decimal_divide" => (vec![Decimal, Decimal, Text], Decimal),
        "timestamp" => (vec![Int], Timestamp),
        "duration" => (vec![Int], Duration),
        "time_millis" => (vec![Timestamp], Int),
        "duration_millis" => (vec![Duration], Int),
        "timestamp_add" => (vec![Timestamp, Duration], Timestamp),
        "timestamp_diff" => (vec![Timestamp, Timestamp], Duration),
        "text_slice" => (vec![Text, Int, Int], Text),
        "text_contains" => (vec![Text, Text], Bool),
        "text_trim" => (vec![Text], Text),
        "text_replace" => (vec![Text, Text, Text], Text),
        "int_text" => (vec![Int], Text),
        "join_text" => (vec![List(Box::new(Text)), Text], Text),
        "id_text" if matches!(args, [Type::Id(_)]) => return Some(Text),
        "markdown" => (vec![Text], Html),
        "list_append" => {
            let [List(inner), value] = args else {
                return None;
            };
            return inner.accepts(value).then(|| {
                List(Box::new(if **inner == Type::Never {
                    value.clone()
                } else {
                    (**inner).clone()
                }))
            });
        }
        "list_reverse" if matches!(args, [List(_)]) => return args.first().cloned(),
        "list_slice" if matches!(args, [List(_), Int, Int]) => return args.first().cloned(),
        "list_get" => {
            let [List(inner), Int] = args else {
                return None;
            };
            return Some(Optional(inner.clone()));
        }
        _ => return None,
    };
    (args.len() == fixed.0.len() && fixed.0.iter().zip(args).all(|(a, b)| a.accepts(b)))
        .then_some(fixed.1)
}
fn invalid() -> Error {
    Error::Input("invalid utility input, rounding mode, bounds or arithmetic overflow".into())
}
fn int(v: &Value) -> Result<i64> {
    if let ValueKind::Int(v) = v.0 {
        Ok(v)
    } else {
        Err(invalid())
    }
}
fn numeric(v: &Value) -> Result<i64> {
    match v.0 {
        ValueKind::Decimal(v) | ValueKind::Timestamp(v) | ValueKind::Duration(v) => Ok(v),
        _ => Err(invalid()),
    }
}
fn text(v: &Value) -> Result<&str> {
    if let ValueKind::Text(v) = &v.0 {
        Ok(v)
    } else {
        Err(invalid())
    }
}
fn list(v: &Value) -> Result<&[Value]> {
    if let ValueKind::List(v) = &v.0 {
        Ok(v)
    } else {
        Err(invalid())
    }
}
fn rounded(n: i128, d: i128, mode: &str) -> Result<i64> {
    if d == 0 || !["half_even", "half_away"].contains(&mode) {
        return Err(invalid());
    }
    let mut q = n / d;
    let r = n % d;
    let twice = r.abs() * 2;
    let denom = d.abs();
    if twice > denom || twice == denom && (mode == "half_away" || q % 2 != 0) {
        q += if (n < 0) == (d < 0) { 1 } else { -1 };
    }
    i64::try_from(q).map_err(|_| invalid())
}
fn range(start: &Value, count: &Value, length: usize) -> Result<(usize, usize)> {
    let a = usize::try_from(int(start)?).map_err(|_| invalid())?;
    let n = usize::try_from(int(count)?).map_err(|_| invalid())?;
    let b = a
        .checked_add(n)
        .filter(|b| *b <= length)
        .ok_or_else(invalid)?;
    Ok((a, b))
}
pub(crate) fn evaluate(name: &str, args: &[Value]) -> Result<Value> {
    let a = || args.first().ok_or_else(invalid);
    let b = || args.get(1).ok_or_else(invalid);
    let c = || args.get(2).ok_or_else(invalid);
    let kind = match name {
        "decimal_units" => ValueKind::Decimal(int(a()?)?),
        "decimal_text" => {
            let v = i128::from(numeric(a()?)?);
            ValueKind::Text(format!(
                "{}{}.{:06}",
                if v < 0 { "-" } else { "" },
                v.abs() / 1_000_000,
                v.abs() % 1_000_000
            ))
        }
        "decimal_add" | "decimal_subtract" => ValueKind::Decimal(
            if name == "decimal_add" {
                numeric(a()?)?.checked_add(numeric(b()?)?)
            } else {
                numeric(a()?)?.checked_sub(numeric(b()?)?)
            }
            .ok_or_else(invalid)?,
        ),
        "decimal_multiply" => ValueKind::Decimal(rounded(
            i128::from(numeric(a()?)?) * i128::from(numeric(b()?)?),
            1_000_000,
            text(c()?)?,
        )?),
        "decimal_divide" => ValueKind::Decimal(rounded(
            i128::from(numeric(a()?)?) * 1_000_000,
            i128::from(numeric(b()?)?),
            text(c()?)?,
        )?),
        "timestamp" => ValueKind::Timestamp(int(a()?)?),
        "duration" => ValueKind::Duration(int(a()?)?),
        "time_millis" | "duration_millis" => ValueKind::Int(numeric(a()?)?),
        "timestamp_add" => ValueKind::Timestamp(
            numeric(a()?)?
                .checked_add(numeric(b()?)?)
                .ok_or_else(invalid)?,
        ),
        "timestamp_diff" => ValueKind::Duration(
            numeric(a()?)?
                .checked_sub(numeric(b()?)?)
                .ok_or_else(invalid)?,
        ),
        "int_text" => ValueKind::Text(int(a()?)?.to_string()),
        "id_text" => {
            let ValueKind::Id(_, v) = &a()?.0 else {
                return Err(invalid());
            };
            ValueKind::Text(v.clone())
        }
        "markdown" => ValueKind::Html(crate::rich_text::render(text(a()?)?)?),
        "stream" => {
            let channel = text(a()?)?;
            let parent = text(b()?)?;
            if !crate::project::resolve::identifier(channel)
                || !(parent.is_empty() || crate::auth::valid_id(parent))
            {
                return Err(invalid());
            }
            ValueKind::Stream(channel.into(), parent.into())
        }
        "text_slice" | "text_contains" | "text_trim" | "text_replace" | "join_text" => {
            text_operation(name, args)?
        }
        "list_reverse" => ValueKind::List(list(a()?)?.iter().rev().cloned().collect()),
        "list_append" => {
            let mut values = list(a()?)?.to_vec();
            if values.len() >= 1000 {
                return Err(Error::Limit("list length"));
            }
            values.push(b()?.clone());
            ValueKind::List(values)
        }
        "list_slice" => {
            let values = list(a()?)?;
            let (a, b) = range(b()?, c()?, values.len())?;
            ValueKind::List(values[a..b].to_vec())
        }
        "list_get" => {
            let values = list(a()?)?;
            let i = int(b()?)?;
            if let Ok(i) = usize::try_from(i) {
                values
                    .get(i)
                    .map_or(ValueKind::None, |v| ValueKind::Some(Box::new(v.clone())))
            } else {
                ValueKind::None
            }
        }
        _ => return Err(invalid()),
    };
    let value = Value(kind);
    value.weight()?;
    Ok(value)
}

fn text_operation(name: &str, args: &[Value]) -> Result<ValueKind> {
    let a = || args.first().ok_or_else(invalid);
    let b = || args.get(1).ok_or_else(invalid);
    let c = || args.get(2).ok_or_else(invalid);
    Ok(match name {
        "text_slice" => {
            let chars: Vec<_> = text(a()?)?.chars().collect();
            let (a, b) = range(b()?, c()?, chars.len())?;
            ValueKind::Text(chars[a..b].iter().collect())
        }
        "text_contains" => ValueKind::Bool(text(a()?)?.contains(text(b()?)?)),
        "text_trim" => ValueKind::Text(text(a()?)?.trim_matches(char::is_whitespace).into()),
        "text_replace" => {
            let input = text(a()?)?;
            let from = text(b()?)?;
            let to = text(c()?)?;
            let count = input.matches(from).count();
            let output_bytes = input
                .len()
                .saturating_sub(count.saturating_mul(from.len()))
                .saturating_add(count.saturating_mul(to.len()));
            if output_bytes > crate::MAX_SOURCE_BYTES {
                return Err(Error::Limit("text utility"));
            }
            ValueKind::Text(input.replace(from, to))
        }
        "join_text" => {
            let parts = list(a()?)?;
            let sep = text(b()?)?;
            let bytes = parts
                .iter()
                .try_fold(0usize, |n, v| {
                    Ok::<_, Error>(n.saturating_add(text(v)?.len()))
                })?
                .saturating_add(parts.len().saturating_sub(1).saturating_mul(sep.len()));
            if bytes > crate::MAX_SOURCE_BYTES {
                return Err(Error::Limit("text utility"));
            }
            ValueKind::Text(
                parts
                    .iter()
                    .map(text)
                    .collect::<Result<Vec<_>>>()?
                    .join(sep),
            )
        }
        _ => return Err(invalid()),
    })
}
