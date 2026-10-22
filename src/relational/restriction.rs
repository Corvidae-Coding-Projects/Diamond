//! Credential attenuation is applied to every base relation and mutation state.
use super::types::{Parameters, quote};
use crate::{
    authority::Scope,
    error::{Error, Result},
    hir::CheckedProgram,
    value::{Type, Value, ValueKind},
};
use std::collections::BTreeMap;
fn field<'a>(p: &'a CheckedProgram, r: &str) -> Result<Option<(&'a str, &'a str)>> {
    let config = &p.storage.as_ref().ok_or(Error::Denied)?.resources[r];
    config
        .scope
        .as_deref()
        .map(|f| {
            let Type::Id(tag) = &p.records[&p.resources[r]].fields[f].ty else {
                return Err(Error::Denied);
            };
            Ok((f, tag.as_str()))
        })
        .transpose()
}
pub(super) fn sql(
    scope: &Scope<'_>,
    p: &CheckedProgram,
    r: &str,
    alias: &str,
    params: &mut Parameters,
) -> Result<String> {
    let Some(limits) = scope.restrictions() else {
        return Ok("TRUE".into());
    };
    let Some((field, tag)) = field(p, r)? else {
        return Ok(if scope.allows_unscoped() {
            "TRUE"
        } else {
            "FALSE"
        }
        .into());
    };
    let Some(ids) = limits.get(tag).filter(|ids| !ids.is_empty()) else {
        return Ok("FALSE".into());
    };
    Ok(format!(
        "{alias}.{} IN ({})",
        quote(field),
        ids.iter()
            .map(|id| params.raw(id))
            .collect::<Vec<_>>()
            .join(",")
    ))
}
pub(super) fn check(
    scope: &Scope<'_>,
    p: &CheckedProgram,
    r: &str,
    rows: &[&BTreeMap<String, Value>],
) -> Result<()> {
    let Some(limits) = scope.restrictions() else {
        return Ok(());
    };
    let Some((field, tag)) = field(p, r)? else {
        return if scope.allows_unscoped() {
            Ok(())
        } else {
            Err(Error::Denied)
        };
    };
    let allowed = limits.get(tag).ok_or(Error::Denied)?;
    for row in rows {
        if !matches!(row.get(field),Some(Value(ValueKind::Id(actual,id))) if actual==tag && allowed.contains(id))
        {
            return Err(Error::Denied);
        }
    }
    Ok(())
}
