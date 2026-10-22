//! W2 bounded `PostgreSQL` document-row profile with checked reference policies.

use super::{CheckedProject, artifact, input, wire};
use crate::authority::{Effect, Scope, random_hex};
use crate::data::{validate_id, validate_input, validate_record};
use crate::error::{Error, Result};
use crate::hir::{CheckedProgram, Record};
use crate::host::HostRuntime;
use crate::policy::Action;
use crate::syntax::Expr;
use crate::value::{Type, Value, ValueBudget, ValueKind};
use std::collections::BTreeMap;

pub(crate) const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_w2;
CREATE TABLE IF NOT EXISTS diamond_w2.apps (app TEXT PRIMARY KEY, profile INTEGER NOT NULL, shape TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diamond_w2.rows (app TEXT NOT NULL REFERENCES diamond_w2.apps(app), resource TEXT NOT NULL, id TEXT NOT NULL, payload JSON NOT NULL CHECK (octet_length(payload::text)<=32768), PRIMARY KEY(app,resource,id));
CREATE TABLE IF NOT EXISTS diamond_w2.users (app TEXT NOT NULL REFERENCES diamond_w2.apps(app), username TEXT NOT NULL, password_hash TEXT NOT NULL, PRIMARY KEY(app,username));
CREATE TABLE IF NOT EXISTS diamond_w2.sessions (app TEXT NOT NULL REFERENCES diamond_w2.apps(app), digest TEXT NOT NULL, username TEXT, expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,digest), FOREIGN KEY(app,username) REFERENCES diamond_w2.users(app,username));
CREATE TABLE IF NOT EXISTS diamond_w2.rates (app TEXT NOT NULL REFERENCES diamond_w2.apps(app), bucket TEXT NOT NULL, attempts INTEGER NOT NULL, expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,bucket));";

#[derive(Debug, PartialEq, Eq)]
enum TransactionFailure {
    Clean,
    Failed,
    Conflict,
}

#[derive(Debug)]
pub(crate) struct PostgresStore {
    pub(super) connection: crate::relational::Connection,
    pub(super) app: String,
    pub(super) program: CheckedProgram,
    pub(super) identity: Option<super::accounts::Config>,
    active: bool,
    nested: bool,
    failure: TransactionFailure,
    pub(super) operation_id: String,
}

impl PostgresStore {
    pub(super) fn open(
        project: &CheckedProject,
        configuration: &str,
        initialize: bool,
    ) -> Result<Self> {
        Self::open_with(project, configuration, initialize, None)
    }
    pub(super) fn open_with(
        project: &CheckedProject,
        configuration: &str,
        initialize: bool,
        pool: Option<&std::sync::Arc<crate::relational::Pool>>,
    ) -> Result<Self> {
        Self::open_mode(project, configuration, initialize, pool, true)
    }
    pub(super) fn open_control(
        project: &CheckedProject,
        configuration: &str,
        pool: Option<&std::sync::Arc<crate::relational::Pool>>,
    ) -> Result<Self> {
        Self::open_mode(project, configuration, false, pool, false)
    }
    fn open_mode(
        project: &CheckedProject,
        configuration: &str,
        initialize: bool,
        pool: Option<&std::sync::Arc<crate::relational::Pool>>,
        serializable: bool,
    ) -> Result<Self> {
        let connection = if let Some(pool) = pool {
            pool.acquire()?
        } else {
            crate::relational::Connection::Direct(HostRuntime::connect_worker(configuration)?)
        };
        let mut store = Self {
            connection,
            app: artifact::hash(project.manifest.name.as_bytes()),
            program: project.server.clone(),
            identity: project.manifest.identity.clone(),
            active: false,
            nested: false,
            failure: TransactionFailure::Clean,
            operation_id: random_hex()?,
        };
        if store.program.storage.is_some() {
            store.connection.runtime()?.enable_relational();
            store
                .connection
                .relational_batch(if initialize || !serializable {
                    "BEGIN"
                } else {
                    "BEGIN ISOLATION LEVEL SERIALIZABLE"
                })?;
            store.active = true;
            if initialize {
                store.connection.query(
                    "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
                    &[&store.app],
                )?;
                store.connection.batch(SCHEMA)?;
                crate::relational::schema::initialize(
                    store.connection.runtime()?,
                    &store.program,
                    &store.app,
                )?;
            } else {
                crate::relational::schema::validate(
                    store.connection.runtime()?,
                    &store.program,
                    &store.app,
                )?;
            }
            if let Some(config) = &store.identity {
                if initialize {
                    super::accounts::initialize(store.connection.runtime()?, &store.app, config)?;
                } else {
                    super::accounts::validate(store.connection.runtime()?, &store.app)?;
                }
            }
            return Ok(store);
        }
        // Serialize initialization and every request for this application across
        // nodes. W4 replaces this coarse correctness boundary with finer locks.
        store.connection.batch("BEGIN")?;
        store.active = true;
        store.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
            &[&store.app],
        )?;
        if initialize {
            store.connection.batch(SCHEMA)?;
        }
        let shape = storage_shape(&store.program)?;
        if initialize {
            store.connection.query("INSERT INTO diamond_w2.apps(app,profile,shape) VALUES ($1,1,$2) ON CONFLICT (app) DO NOTHING", &[&store.app,&shape])?;
        }
        let rows = store.connection.query(
            "SELECT profile,shape FROM diamond_w2.apps WHERE app=$1",
            &[&store.app],
        )?;
        if rows.len() != 1
            || rows[0]
                .try_get::<_, i32>(0)
                .map_err(|_| input("invalid storage metadata"))?
                != 1
            || rows[0]
                .try_get::<_, String>(1)
                .map_err(|_| input("invalid storage metadata"))?
                != shape
        {
            return Err(input(
                "missing or incompatible W2 storage profile; initialize explicitly, migrations are later work",
            ));
        }
        Ok(store)
    }

    pub(crate) fn check_deadline(&self) -> Result<()> {
        self.connection.check_deadline()
    }

    pub(crate) fn begin(&mut self) -> Result<()> {
        if !self.active || self.nested {
            return Err(input("nested PostgreSQL transaction"));
        }
        self.nested = true;
        Ok(())
    }

    pub(crate) fn finish<T>(&mut self, result: Result<T>) -> Result<T> {
        if !self.nested {
            return Err(Error::Denied);
        }
        self.nested = false;
        if let Err(error) = &result {
            self.mark_failed(error);
        }
        if self.failure != TransactionFailure::Clean && result.is_ok() {
            Err(if self.failure == TransactionFailure::Conflict {
                Error::Conflict
            } else {
                Error::Aborted
            })
        } else {
            result
        }
    }

    pub(crate) fn mark_failed(&mut self, error: &Error) {
        if matches!(error, Error::Conflict) {
            self.failure = TransactionFailure::Conflict;
        } else if self.failure == TransactionFailure::Clean {
            self.failure = TransactionFailure::Failed;
        }
    }

    pub(super) fn finish_request<T>(&mut self, result: Result<T>) -> Result<T> {
        if !self.active || self.nested {
            return Err(Error::Denied);
        }
        let result = if self.failure != TransactionFailure::Clean && result.is_ok() {
            Err(if self.failure == TransactionFailure::Conflict {
                Error::Conflict
            } else {
                Error::Aborted
            })
        } else {
            result
        };
        let statement = if result.is_ok() { "COMMIT" } else { "ROLLBACK" };
        let relational = self.program.storage.is_some();
        let finished = if relational {
            self.connection.relational_batch(statement)
        } else {
            self.connection.batch(statement)
        };
        self.active = false;
        if let Err(error) = finished {
            self.connection.close();
            if relational
                && (statement == "ROLLBACK" || matches!(error, Error::Conflict | Error::Denied))
            {
                return if statement == "ROLLBACK" {
                    result
                } else {
                    Err(error)
                };
            }
            return Err(Error::OutcomeUnknown(self.operation_id.clone()));
        }
        result
    }

    pub(crate) fn operation(
        &mut self,
        scope: &Scope<'_>,
        resource: &str,
        method: &str,
        arguments: &[(Option<String>, Expr)],
        values: &[Value],
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        if self.failure != TransactionFailure::Clean || !self.active {
            return Err(Error::Aborted);
        }
        if self.program.storage.is_some() {
            return crate::relational::Context {
                connection: self.connection.runtime()?,
                program: &self.program,
                app: &self.app,
                scope,
            }
            .operation(resource, method, arguments, values, budget);
        }
        scope.require(&if ["get", "select"].contains(&method) {
            Effect::Read(resource.into())
        } else {
            Effect::Write(resource.into())
        })?;
        if ["create", "update"].contains(&method) {
            scope.require(&Effect::Read(resource.into()))?;
        }
        let program = &self.program;
        let name = program.resources.get(resource).ok_or(Error::Denied)?;
        let record = &program.records[name];
        let policy = program.policies.get(resource).ok_or(Error::Denied)?;
        let relation = Relation {
            program,
            name,
            record,
            policy,
            resource,
            app: &self.app,
            scope,
        };
        if method == "select" {
            return relation.select(self.connection.runtime()?, arguments, values, budget);
        }
        if method == "create" {
            return relation.create(self.connection.runtime()?, values, budget);
        }
        let id = values.first().ok_or(Error::Denied)?;
        validate_id(record, id)?;
        let rows=self.connection.query("SELECT payload::text FROM diamond_w2.rows WHERE app=$1 AND resource=$2 AND id=$3 FOR UPDATE", &[&self.app,&resource,&id.display_text()?])?;
        let row = rows.first().ok_or(Error::Denied)?;
        let before = read_record(row, name, record, program, budget)?;
        let ValueKind::Record(_, fields) = &before.0 else {
            return Err(Error::Denied);
        };
        match method {
            "get" => {
                policy.check(Action::Read, &[fields], scope.principal())?;
                Ok(before)
            }
            "update" => {
                let ValueKind::Record(_, input) = &values.get(1).ok_or(Error::Denied)?.0 else {
                    return Err(Error::Denied);
                };
                validate_input(record, input)?;
                let mut after = budget.copy_fields(fields)?;
                after.extend(budget.copy_fields(input)?);
                validate_record(record, &after)?;
                policy.check(Action::Update, &[fields, &after], scope.principal())?;
                policy.check(Action::Read, &[&after], scope.principal())?;
                let value = Value(ValueKind::Record(name.clone(), after));
                let payload = payload(&value, budget)?;
                self.connection.query("UPDATE diamond_w2.rows SET payload=$4::text::json WHERE app=$1 AND resource=$2 AND id=$3", &[&self.app,&resource,&id.display_text()?,&payload])?;
                Ok(value)
            }
            "delete" => {
                policy.check(Action::Delete, &[fields], scope.principal())?;
                self.connection.query(
                    "DELETE FROM diamond_w2.rows WHERE app=$1 AND resource=$2 AND id=$3",
                    &[&self.app, &resource, &id.display_text()?],
                )?;
                Ok(Value(ValueKind::Unit))
            }
            _ => Err(Error::Denied),
        }
    }
}

struct Relation<'a, 'scope> {
    program: &'a CheckedProgram,
    name: &'a str,
    record: &'a Record,
    policy: &'a crate::policy::Policy,
    resource: &'a str,
    app: &'a str,
    scope: &'a Scope<'scope>,
}
impl Relation<'_, '_> {
    fn select(
        &self,
        connection: &mut HostRuntime,
        arguments: &[(Option<String>, Expr)],
        values: &[Value],
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        let program = self.program;
        let name = self.name;
        let record = self.record;
        let policy = self.policy;
        let resource = self.resource;
        let app = self.app;
        let scope = self.scope;

        let named = arguments
            .iter()
            .zip(values)
            .map(|((name, _), value)| Ok((name.as_deref().ok_or(Error::Denied)?, value)))
            .collect::<Result<BTreeMap<_, _>>>()?;
        let Some(Value(ValueKind::Int(limit @ 1..=100))) = named.get("limit").copied() else {
            return Err(input("selection limit must be 1..100"));
        };
        let offset = match named.get("offset").copied() {
            None => 0,
            Some(Value(ValueKind::Int(v @ 0..=1000))) => *v,
            _ => return Err(input("selection offset must be 0..1000")),
        };
        let rows=connection.query("SELECT payload::text FROM diamond_w2.rows WHERE app=$1 AND resource=$2 ORDER BY id COLLATE \"C\" LIMIT 1001", &[&app,&resource])?;
        if rows.len() > 1000 {
            return Err(Error::Limit("W2 relation rows"));
        }
        let mut output = Vec::new();
        let mut skipped = 0;
        for row in rows {
            let value = read_record(&row, name, record, program, budget)?;
            let ValueKind::Record(_, fields) = &value.0 else {
                return Err(Error::Denied);
            };
            match policy.check(Action::Read, &[fields], scope.principal()) {
                Ok(()) => {}
                Err(Error::Denied) => continue,
                Err(e) => return Err(e),
            }
            let matches = named.iter().all(|(key, value)| {
                ["limit", "offset"].contains(key) || fields.get(*key) == Some(*value)
            });
            if !matches {
                continue;
            }
            if skipped < offset {
                skipped += 1;
                continue;
            }
            if output.len() < usize::try_from(*limit).map_err(|_| Error::Denied)? {
                output.push(value);
            }
        }
        let value = Value(ValueKind::List(output));
        budget.charge(&value)?;
        Ok(value)
    }
    fn create(
        &self,
        connection: &mut HostRuntime,
        values: &[Value],
        budget: &mut ValueBudget,
    ) -> Result<Value> {
        let name = self.name;
        let record = self.record;
        let policy = self.policy;
        let resource = self.resource;
        let app = self.app;
        let scope = self.scope;

        let ValueKind::Record(_, input) = &values.first().ok_or(Error::Denied)?.0 else {
            return Err(Error::Denied);
        };
        validate_input(record, input)?;
        let mut fields = BTreeMap::new();
        for (key, field) in &record.fields {
            let value = if key == "id" {
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
                budget.copy(
                    input
                        .get(key)
                        .or(field.default.as_ref())
                        .ok_or_else(|| input_error(key))?,
                )?
            };
            fields.insert(key.clone(), value);
        }
        validate_record(record, &fields)?;
        policy.check(Action::Create, &[&fields], scope.principal())?;
        policy.check(Action::Read, &[&fields], scope.principal())?;
        let count =
            connection.query("SELECT count(*) FROM diamond_w2.rows WHERE app=$1", &[&app])?;
        if count[0].try_get::<_, i64>(0).map_err(|_| Error::Denied)? >= 1000 {
            return Err(Error::Limit("W2 application rows"));
        }
        let id = fields["id"].display_text()?;
        let value = Value(ValueKind::Record(name.to_owned(), fields));
        let payload = payload(&value, budget)?;
        connection.query(
            "INSERT INTO diamond_w2.rows(app,resource,id,payload) VALUES ($1,$2,$3,$4::text::json)",
            &[&app, &resource, &id, &payload],
        )?;
        Ok(value)
    }
}

impl Drop for PostgresStore {
    fn drop(&mut self) {
        // The owned driver is aborted/joined even if a rollback cannot finish.
        if self.active {
            let _rollback = self.connection.batch("ROLLBACK");
        }
    }
}

fn payload(value: &Value, budget: &mut ValueBudget) -> Result<String> {
    budget.charge(value)?;
    let payload = serde_json::to_string(&wire::encode(value)?)
        .map_err(|_| input("record encoding failed"))?;
    if payload.len() > 16_384 {
        return Err(Error::Limit("W2 record payload"));
    }
    Ok(payload)
}

fn read_record(
    row: &tokio_postgres::Row,
    name: &str,
    record: &Record,
    program: &CheckedProgram,
    budget: &mut ValueBudget,
) -> Result<Value> {
    let text: String = row.try_get(0).map_err(|_| input("invalid stored row"))?;
    if text.len() > 32_768 {
        return Err(Error::Limit("W2 stored payload"));
    }
    let json = serde_json::from_str(&text).map_err(|_| input("invalid stored row"))?;
    let value = wire::decode(&Type::Record(name.into()), &json, program)?;
    let ValueKind::Record(_, fields) = &value.0 else {
        return Err(Error::Denied);
    };
    validate_record(record, fields)?;
    budget.charge(&value)?;
    Ok(value)
}

fn storage_shape(program: &CheckedProgram) -> Result<String> {
    let shape = program
        .resources
        .iter()
        .map(|(resource, name)| {
            let fields = program.records[name]
                .fields
                .iter()
                .map(|(name, field)| {
                    Ok((
                        name.clone(),
                        field.ty.to_string(),
                        field.created_by,
                        field.default.as_ref().map(wire::encode).transpose()?,
                    ))
                })
                .collect::<Result<Vec<_>>>()?;
            Ok((resource, name, fields))
        })
        .collect::<Result<Vec<_>>>()?;
    let bytes = serde_json::to_vec(&shape).map_err(|_| input("storage shape encoding failed"))?;
    Ok(artifact::hash(&bytes))
}

fn input_error(field: &str) -> Error {
    input(&format!("missing create field {field}"))
}
