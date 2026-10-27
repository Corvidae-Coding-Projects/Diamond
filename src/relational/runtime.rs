//! Serializable mutations with reference pre/post/return-read authorization.
use super::{
    invalid, query,
    schema::table,
    types::{Parameters, quote},
};
use crate::{
    authority::{Effect, Scope, random_hex},
    error::{Error, Result},
    hir::CheckedProgram,
    host::HostRuntime,
    policy::Action,
    syntax::Expr,
    value::{Type, Value, ValueBudget, ValueKind},
};
use std::collections::BTreeMap;
pub(crate) struct Context<'a, 'scope> {
    pub connection: &'a mut HostRuntime,
    pub program: &'a CheckedProgram,
    pub app: &'a str,
    pub scope: &'a Scope<'scope>,
}
impl Context<'_, '_> {
    pub(crate) fn attachment(&mut self, resource: &str, parent: &str, write: bool) -> Result<()> {
        let record = &self.program.records[&self.program.resources[resource]];
        let Type::Id(tag) = &record.fields["id"].ty else {
            return Err(Error::Denied);
        };
        let id = Value(ValueKind::Id(tag.clone(), parent.into()));
        let value = self.operation(resource, "get", &[], &[id], &mut ValueBudget::new())?;
        if write {
            self.scope.require(&Effect::Write(resource.into()))?;
            let ValueKind::Record(_, fields) = value.0 else {
                return Err(Error::Denied);
            };
            self.authorize(resource, Action::Update, &[&fields, &fields])?;
        }
        Ok(())
    }

    fn authorize(
        &mut self,
        r: &str,
        action: Action,
        rows: &[&BTreeMap<String, Value>],
    ) -> Result<()> {
        let Self {
            connection: c,
            program: p,
            app,
            scope,
        } = self;
        super::restriction::check(scope, p, r, rows)?;
        p.policies.get(r).ok_or(Error::Denied)?.check_with(
            action,
            rows,
            scope.principal(),
            &mut |name, key, role| {
                let Some(id) = scope.principal().id() else {
                    return Ok(false);
                };
                let lookup = &p.storage.as_ref().ok_or(Error::Denied)?.lookups[name];
                let record = &p.records[&p.resources[&lookup.resource]];
                let mut params = Parameters::default();
                let mut conditions = vec![
                    format!(
                        "{} = {}",
                        quote(&lookup.scope),
                        params.add(key, &record.fields[&lookup.scope].ty)?
                    ),
                    format!("{} = {}", quote(&lookup.principal), params.raw(id)),
                ];
                if let Some(role) = role {
                    let field = lookup.role.as_ref().ok_or(Error::Denied)?;
                    let ty = &record.fields[field].ty;
                    let v = if let Type::Enum(n) = ty {
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
                        Value(ValueKind::Text(role.into()))
                    };
                    conditions.push(format!("{} = {}", quote(field), params.add(&v, ty)?));
                }
                Ok(!c
                    .relational_query(
                        &format!(
                            "SELECT 1 FROM {} WHERE {} LIMIT 1",
                            table(app, &lookup.resource),
                            conditions.join(" AND ")
                        ),
                        &params.bindings(),
                    )?
                    .is_empty())
            },
        )
    }

    pub(crate) fn receipt_check(
        &mut self,
        r: &str,
        method: &str,
        before: Option<&Value>,
        after: Option<&Value>,
        allow_missing: bool,
    ) -> Result<()> {
        let fields = |value: Option<&Value>| match value {
            Some(Value(ValueKind::Record(_, fields))) => Ok(fields.clone()),
            _ => Err(Error::Denied),
        };
        let first = fields(before.or(after))?;
        let identifier = first.get("id").ok_or(Error::Denied)?;
        let mut params = Parameters::default();
        let value = params.add(
            identifier,
            &self.program.records[&self.program.resources[r]].fields["id"].ty,
        )?;
        let exists = !self
            .connection
            .relational_query(
                &format!("SELECT 1 FROM {} WHERE id={value}", table(self.app, r)),
                &params.bindings(),
            )?
            .is_empty();
        if exists {
            self.operation(
                r,
                "get",
                &[],
                std::slice::from_ref(identifier),
                &mut ValueBudget::new(),
            )?;
        } else if !allow_missing {
            return Err(Error::Denied);
        }
        self.scope.require(&Effect::Read(r.into()))?;
        self.authorize(r, Action::Read, &[&first])?;
        match method {
            "get" => {
                if before.is_some() || after.is_none() {
                    return Err(Error::Denied);
                }
            }
            "create" => {
                if before.is_some() || after.is_none() {
                    return Err(Error::Denied);
                }
                self.scope.require(&Effect::Write(r.into()))?;
                self.authorize(r, Action::Create, &[&first])?;
            }
            "update" => {
                let next = fields(after)?;
                self.scope.require(&Effect::Write(r.into()))?;
                self.authorize(r, Action::Read, &[&next])?;
                self.authorize(r, Action::Update, &[&first, &next])?;
            }
            "delete" => {
                if before.is_none() || after.is_some() {
                    return Err(Error::Denied);
                }
                self.scope.require(&Effect::Write(r.into()))?;
                self.authorize(r, Action::Delete, &[&first])?;
            }
            _ => return Err(Error::Denied),
        }
        Ok(())
    }
    fn create(&mut self, r: &str, values: &[Value], budget: &mut ValueBudget) -> Result<Value> {
        let p = self.program;
        let app = self.app;
        let scope = self.scope;
        let name = &p.resources[r];
        let record = &p.records[name];
        let Some(Value(ValueKind::Record(_, input))) = values.first() else {
            return Err(Error::Denied);
        };
        crate::data::validate_input(record, input)?;
        let mut fields = BTreeMap::new();
        for (key, f) in &record.fields {
            let v = if key == "id" {
                let Type::Id(tag) = &f.ty else {
                    return Err(Error::Denied);
                };
                Value(ValueKind::Id(tag.clone(), random_hex()?))
            } else if f.created_by {
                Value(ValueKind::Id(
                    "user".into(),
                    scope.principal().id().ok_or(Error::Denied)?.into(),
                ))
            } else {
                budget.copy(
                    input
                        .get(key)
                        .or(f.default.as_ref())
                        .ok_or_else(|| invalid("missing create field"))?,
                )?
            };
            fields.insert(key.clone(), v);
        }
        crate::data::validate_record(record, &fields)?;
        self.authorize(r, Action::Create, &[&fields])?;
        self.authorize(r, Action::Read, &[&fields])?;
        let mut params = Parameters::default();
        let mut bound = Vec::new();
        for (key, v) in &fields {
            bound.push(params.add(v, &record.fields[key].ty)?);
        }
        self.connection.relational_query(
            &format!(
                "INSERT INTO {} ({}) VALUES ({})",
                table(app, r),
                fields
                    .keys()
                    .map(|n| quote(n))
                    .collect::<Vec<_>>()
                    .join(","),
                bound.join(",")
            ),
            &params.bindings(),
        )?;
        let result = Value(ValueKind::Record(name.clone(), fields));
        budget.charge(&result)?;
        Ok(result)
    }
    pub(crate) fn operation(
        &mut self,
        r: &str,
        method: &str,
        args: &[(Option<String>, Expr)],
        values: &[Value],
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        if ["select", "page", "count", "sum", "join", "cursor"].contains(&method) {
            return query::execute(self, r, method, args, values, budget);
        }
        let p = self.program;
        let app = self.app;
        let scope = self.scope;
        scope.require(&if method == "get" {
            Effect::Read(r.into())
        } else {
            Effect::Write(r.into())
        })?;
        let name = p.resources.get(r).ok_or(Error::Denied)?;
        let record = &p.records[name];
        if ["create", "update"].contains(&method) {
            scope.require(&Effect::Read(r.into()))?;
        }
        if method == "create" {
            return self.create(r, values, budget);
        }
        let id = values.first().ok_or(Error::Denied)?;
        crate::data::validate_id(record, id)?;
        let mut params = Parameters::default();
        let id_sql = params.add(id, &record.fields["id"].ty)?;
        let restriction = super::restriction::sql(scope, p, r, "a", &mut params)?;
        let read_policy = if method == "get" {
            format!(
                " AND {}",
                p.policies[r].postgres(
                    Action::Read,
                    scope.principal(),
                    "a",
                    &mut params,
                    p,
                    app
                )?
            )
        } else {
            String::new()
        };
        let rows = self.connection.relational_query(
            &format!(
                "SELECT {} FROM {} AS a WHERE a.\"id\"={id_sql} AND {restriction}{read_policy}{}",
                query::projection(p, r, "a"),
                table(app, r),
                if method == "get" { "" } else { " FOR UPDATE" }
            ),
            &params.bindings(),
        )?;
        let before = query::read(rows.first().ok_or(Error::Denied)?, 0, p, r, budget)?;
        let ValueKind::Record(_, fields) = &before.0 else {
            return Err(Error::Denied);
        };
        // The row lookup bound its credential/policy predicates. Mutation SQL has
        // its own parameter list; reusing lookup-only bindings would shift fields
        // or send parameters the mutation statement does not accept.
        let mut params = Parameters::default();
        let id_sql = params.add(id, &record.fields["id"].ty)?;
        match method {
            "get" => {
                self.authorize(r, Action::Read, &[fields])?;
                Ok(before)
            }
            "update" => {
                let Some(Value(ValueKind::Record(_, input))) = values.get(1) else {
                    return Err(Error::Denied);
                };
                crate::data::validate_input(record, input)?;
                let mut after = budget.copy_fields(fields)?;
                after.extend(budget.copy_fields(input)?);
                crate::data::validate_record(record, &after)?;
                self.authorize(r, Action::Update, &[fields, &after])?;
                self.authorize(r, Action::Read, &[&after])?;
                let mut set = Vec::new();
                for (key, v) in input {
                    set.push(format!(
                        "{}={}",
                        quote(key),
                        params.add(v, &record.fields[key].ty)?
                    ));
                }
                if !set.is_empty() {
                    self.connection.relational_query(
                        &format!(
                            "UPDATE {} SET {} WHERE \"id\"={id_sql}",
                            table(app, r),
                            set.join(",")
                        ),
                        &params.bindings(),
                    )?;
                }
                Ok(Value(ValueKind::Record(name.clone(), after)))
            }
            "delete" => {
                self.authorize(r, Action::Delete, &[fields])?;
                self.connection.relational_query(
                    &format!("DELETE FROM {} WHERE \"id\"={id_sql}", table(app, r)),
                    &params.bindings(),
                )?;
                Ok(Value(ValueKind::Unit))
            }
            _ => Err(Error::Denied),
        }
    }
}
