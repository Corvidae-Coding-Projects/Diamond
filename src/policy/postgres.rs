//! Relational policy lowering. Every value is bound and every identifier is checked.
use super::{Action, Operand, Policy, Predicate};
use crate::authority::Principal;
use crate::error::{Error, Result};
use crate::hir::CheckedProgram;
use crate::relational::{
    schema::table,
    types::{Parameters, quote},
};
use crate::value::{Type, Value, ValueKind};

impl Policy {
    pub(crate) fn postgres(
        &self,
        action: Action,
        principal: &Principal,
        alias: &str,
        params: &mut Parameters,
        program: &CheckedProgram,
        app: &str,
    ) -> Result<String> {
        self.rules
            .get(&action)
            .ok_or(Error::Denied)?
            .postgres(principal, alias, params, program, app)
    }
}
impl Operand {
    fn pg(&self, alias: &str, params: &mut Parameters) -> Result<String> {
        match self {
            Self::Field(0, name, _) => Ok(format!("{alias}.{}", quote(name))),
            Self::Field(..) => Err(Error::Denied),
            Self::Constant(Value(ValueKind::None)) => Ok("NULL".into()),
            Self::Constant(v) => params.add(
                v,
                &match &v.0 {
                    ValueKind::Int(_) => Type::Int,
                    ValueKind::Bool(_) => Type::Bool,
                    _ => Type::Text,
                },
            ),
        }
    }
}
impl Predicate {
    fn postgres(
        &self,
        principal: &Principal,
        alias: &str,
        params: &mut Parameters,
        p: &CheckedProgram,
        app: &str,
    ) -> Result<String> {
        Ok(match self {
            Self::Truth(Operand::Constant(Value(ValueKind::Bool(v)))) => v.to_string(),
            Self::Truth(v) => v.pg(alias, params)?,
            Self::Authenticated => principal.id().is_some().to_string(),
            Self::HasRole(role) => principal.has_role(role).to_string(),
            Self::IsUser(v) => {
                if let Some(id) = principal.id() {
                    format!("({} = {})", v.pg(alias, params)?, params.raw(id))
                } else {
                    "FALSE".into()
                }
            }
            Self::Lookup(name, scope, role) => {
                let Some(id) = principal.id() else {
                    return Ok("FALSE".into());
                };
                let spec = &p.storage.as_ref().ok_or(Error::Denied)?.lookups[name];
                let mut conditions = vec![
                    format!("m.{} = {}", quote(&spec.scope), scope.pg(alias, params)?),
                    format!("m.{} = {}", quote(&spec.principal), params.raw(id)),
                ];
                if let Some(role) = role {
                    let field = spec.role.as_ref().ok_or(Error::Denied)?;
                    let ty = &p.records[&p.resources[&spec.resource]].fields[field].ty;
                    let value = if let Type::Enum(n) = ty {
                        Value(ValueKind::Variant(
                            n.clone(),
                            p.enums[n]
                                .variants
                                .keys()
                                .find(|k| crate::project::resolve::public_name(k) == role)
                                .ok_or(Error::Denied)?
                                .clone(),
                            vec![],
                        ))
                    } else {
                        Value(ValueKind::Text(role.clone()))
                    };
                    conditions.push(format!("m.{} = {}", quote(field), params.add(&value, ty)?));
                }
                format!(
                    "EXISTS (SELECT 1 FROM {} AS m WHERE {})",
                    table(app, &spec.resource),
                    conditions.join(" AND ")
                )
            }
            Self::Not(a) => format!("(NOT {})", a.postgres(principal, alias, params, p, app)?),
            Self::And(a, b) | Self::Or(a, b) => format!(
                "({} {} {})",
                a.postgres(principal, alias, params, p, app)?,
                if matches!(self, Self::And(..)) {
                    "AND"
                } else {
                    "OR"
                },
                b.postgres(principal, alias, params, p, app)?
            ),
            Self::Compare(a, op, b) => format!(
                "({} {} {})",
                a.pg(alias, params)?,
                match op {
                    crate::syntax::Binary::Equal => "IS NOT DISTINCT FROM",
                    crate::syntax::Binary::NotEqual => "IS DISTINCT FROM",
                    _ => super::operator_sql(*op)?,
                },
                b.pg(alias, params)?
            ),
        })
    }
}
