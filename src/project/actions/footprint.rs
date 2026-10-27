//! Automatically captured CRUD facts; never inferred from the result's shape.
use crate::{
    authority::Scope,
    error::{Error, Result},
    project::{input, storage::PostgresStore, wire},
    syntax::Expr,
    value::{Type, Value, ValueBudget, ValueKind},
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(in crate::project) struct Access {
    resource: String,
    method: String,
    before: Option<serde_json::Value>,
    after: Option<serde_json::Value>,
}
impl PostgresStore {
    fn raw_operation(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        method: &str,
        arguments: &[(Option<String>, Expr)],
        values: &[Value],
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        crate::relational::Context {
            connection: self.connection.runtime()?,
            program: &self.program,
            app: &self.app,
            scope,
        }
        .operation(resource, method, arguments, values, budget)
    }
    pub(in crate::project) fn tracked_operation(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        method: &str,
        arguments: &[(Option<String>, Expr)],
        values: &[Value],
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        if !["get", "create", "update", "delete"].contains(&method) {
            return Err(input(
                "transactional actions require bounded CRUD dependencies",
            ));
        }
        let before = if ["update", "delete"].contains(&method) {
            Some(wire::encode(&self.raw_operation(
                scope,
                resource,
                "get",
                &[],
                &values[..1],
                budget,
            )?)?)
        } else {
            None
        };
        let value = self.raw_operation(scope, resource, method, arguments, values, budget)?;
        let after = if method == "delete" {
            None
        } else {
            Some(wire::encode(&value)?)
        };
        let access = Access {
            resource: resource.into(),
            method: method.into(),
            before,
            after,
        };
        let accesses = self.action_access.as_mut().ok_or(Error::Denied)?;
        if !accesses.contains(&access) {
            accesses.push(access);
        }
        if accesses.len() > 64
            || serde_json::to_vec(accesses)
                .map_err(|_| Error::Denied)?
                .len()
                > 262_144
        {
            return Err(Error::Limit("action receipt footprint"));
        }
        Ok(value)
    }
}
fn record(access: &Access, json: &serde_json::Value, store: &PostgresStore) -> Result<Value> {
    let name = store
        .program
        .resources
        .get(&access.resource)
        .ok_or(Error::Denied)?;
    let value = wire::decode(&Type::Record(name.clone()), json, &store.program)?;
    let ValueKind::Record(_, fields) = &value.0 else {
        return Err(Error::Denied);
    };
    crate::data::validate_record(&store.program.records[name], fields)?;
    Ok(value)
}
fn id(value: &Value) -> Result<&str> {
    let ValueKind::Record(_, fields) = &value.0 else {
        return Err(Error::Denied);
    };
    let Some(Value(ValueKind::Id(_, id))) = fields.get("id") else {
        return Err(Error::Denied);
    };
    Ok(id)
}
pub(super) fn reauthorize(
    store: &mut PostgresStore,
    scope: &Scope<'_>,
    accesses: &[Access],
    succeeded: bool,
) -> Result<()> {
    if accesses.len() > 64 {
        return Err(Error::Denied);
    }
    let decoded = accesses
        .iter()
        .map(|access| {
            Ok((
                access,
                access
                    .before
                    .as_ref()
                    .map(|v| record(access, v, store))
                    .transpose()?,
                access
                    .after
                    .as_ref()
                    .map(|v| record(access, v, store))
                    .transpose()?,
            ))
        })
        .collect::<Result<Vec<_>>>()?;
    for (access, before, after) in &decoded {
        let value = before.as_ref().or(after.as_ref()).ok_or(Error::Denied)?;
        let identifier = id(value)?;
        let absent = decoded.iter().any(|(other, old, new)| {
            other.resource == access.resource
                && ((succeeded && other.method == "delete")
                    || (!succeeded && other.method == "create"))
                && old.as_ref().or(new.as_ref()).and_then(|v| id(v).ok()) == Some(identifier)
        });
        crate::relational::Context {
            connection: store.connection.runtime()?,
            program: &store.program,
            app: &store.app,
            scope,
        }
        .receipt_check(
            &access.resource,
            &access.method,
            before.as_ref(),
            after.as_ref(),
            absent,
        )?;
    }
    Ok(())
}

pub(super) fn deleted_parent(
    store: &PostgresStore,
    accesses: &[Access],
    resource: &str,
    parent: &str,
) -> Result<bool> {
    for access in accesses {
        if access.resource == resource
            && access.method == "delete"
            && access.after.is_none()
            && let Some(before) = &access.before
            && id(&record(access, before, store)?)? == parent
        {
            return Ok(true);
        }
    }
    Ok(false)
}
