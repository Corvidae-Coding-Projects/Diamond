//! A single-connection `SQLite` adapter with structural bindings and mandatory policies.

use crate::authority::{Effect, Scope, random_hex};
use crate::error::{Error, Result};
use crate::hir::{CheckedProgram, Record};
use crate::policy::{Action, Policy, sql_value};
use crate::value::{Type, VALUE_NODE_WEIGHT, Value, ValueBudget, ValueKind};
use rusqlite::{Connection, OptionalExtension, params_from_iter};
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug)]
pub(crate) struct Database {
    connection: Option<Connection>,
    program: CheckedProgram,
    active: bool,
    failed: bool,
    operation_id: Option<String>,
}

impl Database {
    pub fn open(path: &Path, program: &CheckedProgram) -> Result<Self> {
        let mut plans = Vec::new();
        for (resource, name) in &program.resources {
            let record = &program.records[name];
            let fields = record
                .fields
                .iter()
                .map(|(name, field)| column(name, &field.ty))
                .collect::<Result<Vec<_>>>()?;
            plans.push((
                resource.clone(),
                format!("CREATE TABLE \"{resource}\" ({})", fields.join(", ")),
            ));
        }
        let connection = Connection::open(path)?;
        connection.busy_timeout(std::time::Duration::from_secs(2))?;
        connection.execute_batch("PRAGMA foreign_keys=ON; PRAGMA trusted_schema=OFF;")?;
        let mut database = Self {
            connection: Some(connection),
            program: program.clone(),
            active: false,
            failed: false,
            operation_id: None,
        };
        database.atomic(|database| {
            for (resource, sql) in &plans {
                let existing: Option<String> = database
                    .connection()?
                    .query_row(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?1",
                        [resource],
                        |row| row.get(0),
                    )
                    .optional()?;
                match existing {
                    Some(existing) if existing != *sql => {
                        return Err(Error::Input(format!(
                            "schema for {resource} differs; automatic migration is not implemented"
                        )));
                    }
                    None => {
                        database.connection()?.execute(sql, [])?;
                    }
                    Some(_) => {}
                }
                let triggers: i64 = database.connection()?.query_row(
                    "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name=?1",
                    [resource],
                    |row| row.get(0),
                )?;
                if triggers != 0 {
                    return Err(Error::unsupported(
                        crate::Location::default(),
                        "database triggers on protected resources",
                    ));
                }
            }
            Ok(())
        })?;
        Ok(database)
    }

    fn connection(&self) -> Result<&Connection> {
        self.connection.as_ref().ok_or(Error::Denied)
    }

    pub fn begin(&mut self) -> Result<()> {
        self.begin_mode(true)
    }

    fn begin_mode(&mut self, write: bool) -> Result<()> {
        if self.active {
            return Err(Error::unsupported(
                crate::Location::default(),
                "nested transaction blocks",
            ));
        }
        let operation_id = random_hex()?;
        self.connection()?.execute_batch(if write {
            "BEGIN IMMEDIATE"
        } else {
            "BEGIN DEFERRED"
        })?;
        self.active = true;
        self.failed = false;
        self.operation_id = Some(operation_id);
        Ok(())
    }

    pub fn finish<T>(&mut self, result: Result<T>) -> Result<T> {
        if !self.active {
            return Err(Error::Denied);
        }
        self.active = false;
        let result = if self.failed && result.is_ok() {
            Err(Error::Aborted)
        } else {
            result
        };
        let operation_id = self.operation_id.take().ok_or(Error::Denied)?;
        let connection = self.connection()?;
        if result.is_err() {
            if connection.execute_batch("ROLLBACK").is_ok() {
                return result;
            }
            self.connection.take();
            return Err(Error::OutcomeUnknown(operation_id));
        }
        if let Ok(()) = connection.execute_batch("COMMIT") {
            result
        } else {
            if !connection.is_autocommit() && connection.execute_batch("ROLLBACK").is_ok() {
                return Err(Error::Aborted);
            }
            self.connection.take();
            Err(Error::OutcomeUnknown(operation_id))
        }
    }

    fn atomic<T>(&mut self, operation: impl FnOnce(&Self) -> Result<T>) -> Result<T> {
        self.scoped(true, operation)
    }

    fn scoped<T>(&mut self, write: bool, operation: impl FnOnce(&Self) -> Result<T>) -> Result<T> {
        if self.active {
            let result = operation(self);
            if result.is_err() {
                self.failed = true;
            }
            return result;
        }
        self.begin_mode(write)?;
        let result = operation(self);
        self.finish(result)
    }

    pub fn mark_failed(&mut self) {
        if self.active {
            self.failed = true;
        }
    }

    fn definition(&self, resource: &str) -> Result<(&str, &Record, &Policy)> {
        let name = self.program.resources.get(resource).ok_or(Error::Denied)?;
        Ok((
            name,
            self.program.records.get(name).ok_or(Error::Denied)?,
            self.program.policies.get(resource).ok_or(Error::Denied)?,
        ))
    }

    pub fn get(
        &self,
        scope: &Scope<'_>,
        resource: &str,
        id: &Value,
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        scope.require(&Effect::Read(resource.into()))?;
        let (_, record, policy) = self.definition(resource)?;
        validate_id(record, id)?;
        let (predicate, mut bindings) = policy.read_sql(scope.principal())?;
        bindings.insert(0, sql_value(id)?);
        let sql = format!(
            "SELECT {} FROM \"{resource}\" WHERE \"id\" = ? AND ({predicate}) LIMIT 1",
            columns(record)
        );
        let value = self
            .query_record(resource, record, &sql, &bindings, budget)?
            .ok_or(Error::Denied)?;
        let ValueKind::Record(_, fields) = &value.0 else {
            return Err(Error::Denied);
        };
        policy.check(Action::Read, &[fields], scope.principal())?;
        Ok(value)
    }

    pub fn select(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        arguments: &BTreeMap<String, Value>,
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        scope.require(&Effect::Read(resource.into()))?;
        self.scoped(false, |database| {
            database.select_snapshot(scope, resource, arguments, budget)
        })
    }

    fn select_snapshot(
        &self,
        scope: &Scope<'_>,
        resource: &str,
        arguments: &BTreeMap<String, Value>,
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        let (_, record, policy) = self.definition(resource)?;
        let Some(Value(ValueKind::Int(limit @ 1..=100))) = arguments.get("limit") else {
            return Err(Error::Input("selection limit must be within 1..100".into()));
        };
        let offset = match arguments.get("offset") {
            Some(Value(ValueKind::Int(offset @ 0..=10_000))) => *offset,
            None => 0,
            _ => {
                return Err(Error::Input(
                    "selection offset must be within 0..10000".into(),
                ));
            }
        };
        self.validate_relation(resource, record, budget)?;
        let (predicate, mut bindings) = policy.read_sql(scope.principal())?;
        let mut predicates = vec![format!("({predicate})")];
        for (field, value) in arguments {
            if ["limit", "offset"].contains(&field.as_str()) {
                continue;
            }
            let definition = record.fields.get(field).ok_or(Error::Denied)?;
            if !value.fits(&definition.ty) {
                return Err(Error::Input(
                    "selection filter violates its declared type".into(),
                ));
            }
            predicates.push(format!("\"{field}\" IS ?"));
            bindings.push(sql_value(value)?);
        }
        bindings.extend([
            rusqlite::types::Value::Integer(*limit),
            rusqlite::types::Value::Integer(offset),
        ]);
        let sql = format!(
            "SELECT {} FROM \"{resource}\" WHERE {} ORDER BY \"id\" COLLATE BINARY ASC LIMIT ? OFFSET ?",
            columns(record),
            predicates.join(" AND ")
        );
        let mut statement = self.connection()?.prepare(&sql)?;
        let mut rows = statement.query(params_from_iter(&bindings))?;
        let mut output = Vec::new();
        let mut weight = VALUE_NODE_WEIGHT;
        while let Some(row) = rows.next()? {
            let name = &self.program.resources[resource];
            let fields = read_record(row, name, record, budget)?;
            policy.check(Action::Read, &[&fields], scope.principal())?;
            let value = Value(ValueKind::Record(name.clone(), fields));
            weight = weight.saturating_add(value.weight()?);
            ValueBudget::check_weight(weight)?;
            output.push(value);
        }
        Ok(Value(ValueKind::List(output)))
    }

    fn validate_relation(
        &self,
        resource: &str,
        record: &Record,
        budget: &mut ValueBudget,
    ) -> Result<()> {
        let sql = format!("SELECT {} FROM \"{resource}\" LIMIT 10001", columns(record));
        let mut statement = self.connection()?.prepare(&sql)?;
        let mut rows = statement.query([])?;
        let mut count = 0;
        while let Some(row) = rows.next()? {
            count += 1;
            if count > 10_000 {
                return Err(Error::Limit("selection relation validation rows"));
            }
            read_record(row, &self.program.resources[resource], record, budget)?;
        }
        Ok(())
    }

    pub fn create(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        input: &Value,
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        scope.require(&Effect::Write(resource.into()))?;
        scope.require(&Effect::Read(resource.into()))?;
        self.atomic(|database| {
            let (name, record, policy) = database.definition(resource)?;
            let ValueKind::Record(_, input) = &input.0 else {
                return Err(Error::Input("create expects a record".into()));
            };
            let mut fields = BTreeMap::new();
            for (field_name, field) in &record.fields {
                let value = if field_name == "id" {
                    let Type::Id(tag) = &field.ty else {
                        return Err(Error::Denied);
                    };
                    Value(ValueKind::Id(tag.clone(), random_hex()?))
                } else if field.created_by {
                    Value(ValueKind::Id(
                        "user".into(),
                        scope.principal().id().ok_or(Error::Denied)?.into(),
                    ))
                } else {
                    let value = input
                        .get(field_name)
                        .or(field.default.as_ref())
                        .ok_or_else(|| {
                            Error::Input(format!("missing create field {field_name}"))
                        })?;
                    budget.copy(value)?
                };
                fields.insert(field_name.clone(), value);
            }
            validate_input(record, input)?;
            validate_record(record, &fields)?;
            policy.check(Action::Create, &[&fields], scope.principal())?;
            let value = Value(ValueKind::Record(name.into(), fields));
            budget.charge(&value)?;
            let ValueKind::Record(_, fields) = &value.0 else {
                return Err(Error::Denied);
            };
            budget.charge(&value)?;
            let bindings = fields.values().map(sql_value).collect::<Result<Vec<_>>>()?;
            let placeholders = vec!["?"; fields.len()].join(", ");
            let sql = format!(
                "INSERT INTO \"{resource}\" ({}) VALUES ({placeholders})",
                columns(record)
            );
            database
                .connection()?
                .execute(&sql, params_from_iter(&bindings))?;
            policy.check(Action::Read, &[fields], scope.principal())?;
            Ok(value)
        })
    }

    pub fn update(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        id: &Value,
        input: &Value,
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        scope.require(&Effect::Write(resource.into()))?;
        scope.require(&Effect::Read(resource.into()))?;
        self.atomic(|database| {
            let (name, record, policy) = database.definition(resource)?;
            let before = database.lookup(resource, record, id, budget)?;
            let ValueKind::Record(_, input) = &input.0 else {
                return Err(Error::Input("update expects a record".into()));
            };
            validate_input(record, input)?;
            let mut after = budget.copy_fields(&before)?;
            after.extend(budget.copy_fields(input)?);
            validate_record(record, &after)?;
            policy.check(Action::Update, &[&before, &after], scope.principal())?;
            let value = Value(ValueKind::Record(name.into(), after));
            budget.charge(&value)?;
            let ValueKind::Record(_, after) = &value.0 else {
                return Err(Error::Denied);
            };
            budget.charge(&value)?;
            let assignments = after
                .keys()
                .filter(|name| *name != "id")
                .map(|name| format!("\"{name}\" = ?"))
                .collect::<Vec<_>>()
                .join(", ");
            let mut bindings = after
                .iter()
                .filter(|(name, _)| *name != "id")
                .map(|(_, value)| sql_value(value))
                .collect::<Result<Vec<_>>>()?;
            bindings.push(sql_value(id)?);
            let sql = format!("UPDATE \"{resource}\" SET {assignments} WHERE \"id\" = ?");
            if database
                .connection()?
                .execute(&sql, params_from_iter(&bindings))?
                != 1
            {
                return Err(Error::Denied);
            }
            policy.check(Action::Read, &[after], scope.principal())?;
            Ok(value)
        })
    }

    pub fn delete(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        id: &Value,
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        scope.require(&Effect::Write(resource.into()))?;
        self.atomic(|database| {
            let (_, record, policy) = database.definition(resource)?;
            let before = database.lookup(resource, record, id, budget)?;
            policy.check(Action::Delete, &[&before], scope.principal())?;
            budget.charge(id)?;
            let sql = format!("DELETE FROM \"{resource}\" WHERE \"id\" = ?1");
            if database.connection()?.execute(&sql, [sql_value(id)?])? != 1 {
                return Err(Error::Denied);
            }
            Ok(Value(ValueKind::Unit))
        })
    }

    fn lookup(
        &self,
        resource: &str,
        record: &Record,
        id: &Value,
        budget: &mut ValueBudget,
    ) -> Result<BTreeMap<String, Value>> {
        validate_id(record, id)?;
        let sql = format!(
            "SELECT {} FROM \"{resource}\" WHERE \"id\" = ?1 LIMIT 1",
            columns(record)
        );
        let value = self
            .query_record(resource, record, &sql, &[sql_value(id)?], budget)?
            .ok_or(Error::Denied)?;
        match value.0 {
            ValueKind::Record(_, fields) => Ok(fields),
            _ => Err(Error::Denied),
        }
    }

    fn query_record(
        &self,
        resource: &str,
        record: &Record,
        sql: &str,
        bindings: &[rusqlite::types::Value],
        budget: &mut ValueBudget,
    ) -> Result<Option<Value>> {
        let mut statement = self.connection()?.prepare(sql)?;
        let mut rows = statement.query(params_from_iter(bindings))?;
        let Some(row) = rows.next()? else {
            return Ok(None);
        };
        let fields = read_record(row, &self.program.resources[resource], record, budget)?;
        Ok(Some(Value(ValueKind::Record(
            self.program.resources[resource].clone(),
            fields,
        ))))
    }
}

fn read_record(
    row: &rusqlite::Row<'_>,
    name: &str,
    record: &Record,
    budget: &mut ValueBudget,
) -> Result<BTreeMap<String, Value>> {
    // Inspect SQLite's borrowed cells before allocating any managed strings. A
    // row must fit on its own even when its individual fields are legal.
    let mut weight = VALUE_NODE_WEIGHT + name.len();
    for (index, (name, field)) in record.fields.iter().enumerate() {
        let cell = row.get_ref(index)?;
        weight = weight.saturating_add(name.len() + VALUE_NODE_WEIGHT);
        let base = if let Type::Option(inner) = &field.ty {
            if cell.data_type() != rusqlite::types::Type::Null {
                weight = weight.saturating_add(VALUE_NODE_WEIGHT);
            }
            inner.as_ref()
        } else {
            &field.ty
        };
        if let Type::Id(tag) = base
            && cell.data_type() != rusqlite::types::Type::Null
        {
            weight = weight.saturating_add(tag.len());
        }
        if let rusqlite::types::ValueRef::Text(bytes) = cell {
            if bytes.len() > 1_048_576 {
                return Err(Error::Limit("stored text size"));
            }
            weight = weight.saturating_add(bytes.len());
        }
        ValueBudget::check_weight(weight)?;
    }
    budget.spend(weight)?;
    let mut fields = BTreeMap::new();
    for (index, (name, field)) in record.fields.iter().enumerate() {
        fields.insert(name.clone(), read_value(row, index, &field.ty)?);
    }
    validate_record(record, &fields)?;
    Ok(fields)
}

impl Drop for Database {
    fn drop(&mut self) {
        if self.active
            && let Some(connection) = &self.connection
        {
            let _ = connection.execute_batch("ROLLBACK");
        }
        // Connection ownership is affine; dropping an unusable handle never pools it.
    }
}

fn columns(record: &Record) -> String {
    record
        .fields
        .keys()
        .map(|name| format!("\"{name}\""))
        .collect::<Vec<_>>()
        .join(", ")
}

fn column(name: &str, ty: &Type) -> Result<String> {
    let (base, nullable) = match ty {
        Type::Option(inner) => (inner.as_ref(), true),
        _ => (ty, false),
    };
    let sql_type = match base {
        Type::Int | Type::Bool => "INTEGER",
        Type::Text | Type::BoundedText(_, _) | Type::Id(_) => "TEXT COLLATE BINARY",
        _ => {
            return Err(Error::unsupported(
                crate::Location::default(),
                format!("database field type {ty}"),
            ));
        }
    };
    let boolean = if *base == Type::Bool {
        format!(" CHECK (\"{name}\" IN (0, 1))")
    } else {
        String::new()
    };
    Ok(format!(
        "\"{name}\" {sql_type}{}{}{boolean}",
        if nullable { "" } else { " NOT NULL" },
        if name == "id" { " PRIMARY KEY" } else { "" }
    ))
}

pub(crate) fn validate_id(record: &Record, id: &Value) -> Result<()> {
    if !id.fits(&record.fields["id"].ty) {
        return Err(Error::Denied);
    }
    if !matches!(&id.0, ValueKind::Id(_, value) if crate::auth::valid_id(value)) {
        return Err(Error::Denied);
    }
    Ok(())
}

pub(crate) fn validate_input(record: &Record, input: &BTreeMap<String, Value>) -> Result<()> {
    for name in input.keys() {
        if name == "id" || record.fields.get(name).is_none_or(|field| field.created_by) {
            return Err(Error::Input("unknown or server-derived input field".into()));
        }
    }
    Ok(())
}

pub(crate) fn validate_record(record: &Record, fields: &BTreeMap<String, Value>) -> Result<()> {
    if record.fields.len() != fields.len() {
        return Err(Error::Input("record field count mismatch".into()));
    }
    for (name, field) in &record.fields {
        if !fields.get(name).is_some_and(|value| value.fits(&field.ty)) {
            return Err(Error::Input(format!(
                "field {name} violates its type or bounds"
            )));
        }
    }
    Ok(())
}

fn read_value(row: &rusqlite::Row<'_>, index: usize, ty: &Type) -> Result<Value> {
    if matches!(row.get_ref(index)?, rusqlite::types::ValueRef::Text(bytes) if bytes.len() > 1_048_576)
    {
        return Err(Error::Limit("stored text size"));
    }
    Ok(Value(match ty {
        Type::Int => ValueKind::Int(row.get(index)?),
        Type::Bool => match row.get::<_, i64>(index)? {
            0 => ValueKind::Bool(false),
            1 => ValueKind::Bool(true),
            _ => return Err(Error::Input("invalid stored Boolean".into())),
        },
        Type::Text | Type::BoundedText(_, _) => ValueKind::Text(row.get(index)?),
        Type::Id(tag) => ValueKind::Id(tag.clone(), row.get(index)?),
        Type::Option(inner) => {
            if row.get_ref(index)?.data_type() == rusqlite::types::Type::Null {
                ValueKind::None
            } else {
                ValueKind::Some(Box::new(read_value(row, index, inner)?))
            }
        }
        _ => {
            return Err(Error::unsupported(
                crate::Location::default(),
                "stored non-scalar field",
            ));
        }
    }))
}
