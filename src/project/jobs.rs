//! Transactional job outbox and fenced, bounded, fresh-authority workers.
mod worker;
use super::{CheckedProject, Manifest, Module, artifact, input, rpc, storage::PostgresStore, wire};
use crate::{
    authority::{Effect, Effects, Scope},
    error::{Error, Result},
    hir::CheckedProgram,
    value::{Type, Value, ValueKind},
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub function: String,
    pub resource: String,
    pub grants: BTreeSet<String>,
    #[serde(default = "attempts")]
    pub max_attempts: i32,
    pub delivery: String,
}
const fn attempts() -> i32 {
    3
}
const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_w7;
CREATE TABLE IF NOT EXISTS diamond_w7.apps(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app), shape TEXT NOT NULL, paused BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS diamond_w7.jobs(app TEXT NOT NULL REFERENCES diamond_w7.apps(app), id TEXT NOT NULL, binding TEXT NOT NULL, version TEXT NOT NULL, parent TEXT NOT NULL, owner TEXT NOT NULL, payload TEXT NOT NULL CHECK(octet_length(payload)<=16384), dedup TEXT NOT NULL CHECK(octet_length(dedup)<=160), restrictions TEXT NOT NULL, unscoped BOOLEAN NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','running','done','dead','cancelled')), attempts INTEGER NOT NULL DEFAULT 0, fence BIGINT NOT NULL DEFAULT 0, lease TIMESTAMPTZ, due TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), outcome TEXT NOT NULL DEFAULT '', PRIMARY KEY(app,id), UNIQUE(app,binding,owner,dedup));
CREATE INDEX IF NOT EXISTS jobs_due ON diamond_w7.jobs(app,due) WHERE state IN ('pending','running');
CREATE TABLE IF NOT EXISTS diamond_w7.events(app TEXT NOT NULL REFERENCES diamond_w7.apps(app), cursor BIGINT GENERATED ALWAYS AS IDENTITY, binding TEXT NOT NULL, parent TEXT NOT NULL, payload TEXT NOT NULL CHECK(octet_length(payload)<=16384), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,cursor));
CREATE INDEX IF NOT EXISTS events_parent ON diamond_w7.events(app,binding,parent,cursor);";

pub(super) fn schema(
    db: &mut PostgresStore,
    project: &CheckedProject,
    initialize: bool,
) -> Result<()> {
    let shape = artifact::hash(
        &serde_json::to_vec(&(
            &project.manifest.jobs,
            &project.manifest.events,
            &project.server.durable,
        ))
        .map_err(|_| Error::Denied)?,
    );
    if initialize {
        db.connection.batch(SCHEMA)?;
        db.connection.query(
            "INSERT INTO diamond_w7.apps(app,shape) VALUES($1,$2) ON CONFLICT DO NOTHING",
            &[&db.app, &shape],
        )?;
    }
    let rows = db
        .connection
        .query("SELECT shape FROM diamond_w7.apps WHERE app=$1", &[&db.app])?;
    if rows
        .first()
        .map(|r| r.try_get::<_, String>(0))
        .transpose()
        .map_err(|_| Error::Denied)?
        != Some(shape)
    {
        return Err(input(
            "incompatible jobs/events schema or contract; stop instances and initialize the current profile",
        ));
    }
    Ok(())
}
pub(super) fn version(project: &CheckedProject) -> Result<String> {
    let sources = project
        .modules
        .values()
        .filter(|m| m.config.target != super::Target::Browser)
        .map(|m| (&m.config.path, &project.sources[&m.config.path]))
        .collect::<BTreeMap<_, _>>();
    Ok(artifact::hash(
        &serde_json::to_vec(&(&project.manifest.jobs, &project.manifest.events, sources))
            .map_err(|_| Error::Denied)?,
    ))
}

pub(super) fn checked(
    m: &Manifest,
    modules: &BTreeMap<String, Module>,
    p: &CheckedProgram,
) -> Result<BTreeMap<String, rpc::Operation>> {
    if m.jobs.is_empty() && m.events.is_empty() {
        return Ok(BTreeMap::new());
    }
    if m.identity.is_none() || m.storage.is_none() || m.jobs.len() > 32 || m.events.len() > 32 {
        return Err(input(
            "jobs/events require accounts and relational storage, at most 32 bindings each",
        ));
    }
    let mut proxy = m.clone();
    proxy.rpc = m
        .jobs
        .iter()
        .map(|(n, b)| (n.clone(), b.function.clone()))
        .collect();
    let ops = rpc::operations(&proxy, modules, p)?;
    for (name, b) in &m.jobs {
        let op = &ops[name];
        if op.arguments.len() != 2
            || op.arguments[0] != Type::Text
            || op.result != Type::Text
            || !(1..=8).contains(&b.max_attempts)
            || !p.resources.contains_key(&b.resource)
        {
            return Err(input(
                "job handler requires (parent: Text, payload: wire-safe type) -> Result<Text, DataError>, a known parent resource and 1..8 attempts",
            ));
        }
        if !["transactional", "deduplicated"].contains(&b.delivery.as_str())
            || b.delivery == "transactional"
                && p.functions[&op.function]
                    .effects
                    .iter()
                    .any(|e| matches!(e,Effect::Host(n) if n.starts_with("Http<")))
        {
            return Err(input(
                "external job handlers require delivery=deduplicated and a receiver-side stable job key",
            ));
        }
        let grants = b
            .grants
            .iter()
            .map(|s| super::capability_effect(s))
            .collect::<Result<Effects>>()?;
        if !grants.contains(&Effect::Read(b.resource.clone()))
            || !grants.contains(&Effect::Write(b.resource.clone()))
            || !p.functions[&op.function].effects.is_subset(&grants)
            || !grants.is_subset(&p.grants)
            || grants.contains(&Effect::Host("Jobs".into()))
        {
            return Err(input(
                "handler effects exceed its explicit grants; recursive job submission is prohibited",
            ));
        }
    }
    Ok(ops)
}
fn text(v: Option<&Value>) -> Result<&str> {
    match v {
        Some(Value(ValueKind::Text(s))) => Ok(s),
        _ => Err(Error::Denied),
    }
}
fn bounded_payload(value: &Value) -> Result<String> {
    let s = wire::encode(value)?.to_string();
    if s.len() > 16384 {
        Err(Error::Limit("durable payload"))
    } else {
        Ok(s)
    }
}
impl PostgresStore {
    pub(crate) fn durable_call(
        &mut self,
        scope: &Scope<'_>,
        method: &str,
        values: &[Value],
    ) -> Result<Value> {
        let event = method == "event_emit";
        scope.require(&Effect::Host(if event { "Events" } else { "Jobs" }.into()))?;
        let binding = text(values.first())?;
        let parent_or_id = text(values.get(1))?;
        let owner = scope.principal().id().ok_or(Error::Unauthenticated)?;
        if event {
            return self.emit(
                scope,
                binding,
                parent_or_id,
                values.get(2).ok_or(Error::Denied)?,
            );
        }
        let config = self.jobs.get(binding).cloned().ok_or(Error::Denied)?;
        if method == "job_submit" {
            self.attachment(scope, &config.resource, parent_or_id, true)?;
            let payload = values.get(2).ok_or(Error::Denied)?;
            let expected = &self
                .job_operations
                .get(binding)
                .ok_or(Error::Denied)?
                .arguments[1];
            wire::decode(expected, &wire::encode(payload)?, &self.program)?;
            let payload = bounded_payload(payload)?;
            let dedup = text(values.get(3))?;
            if dedup.is_empty() || dedup.len() > 160 {
                return Err(input("job dedup key requires 1..160 bytes"));
            }
            self.connection.query(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,7))",
                &[&self.app],
            )?;
            let restrictions =
                serde_json::to_string(&scope.restrictions()).map_err(|_| Error::Denied)?;
            let shape = self.job_version.clone();
            let existing = self.connection.query(
                "SELECT id,parent,payload,version,restrictions,unscoped FROM diamond_w7.jobs WHERE app=$1 AND binding=$2 AND owner=$3 AND dedup=$4",
                &[&self.app, &binding, &owner, &dedup],
            )?;
            if let Some(row) = existing.first() {
                let same = row.try_get::<_, String>(1).map_err(|_| Error::Denied)? == parent_or_id
                    && row.try_get::<_, String>(2).map_err(|_| Error::Denied)? == payload
                    && row.try_get::<_, String>(3).map_err(|_| Error::Denied)? == shape
                    && row.try_get::<_, String>(4).map_err(|_| Error::Denied)? == restrictions
                    && row.try_get::<_, bool>(5).map_err(|_| Error::Denied)?
                        == scope.allows_unscoped();
                if !same {
                    return Err(Error::Conflict);
                }
                return Ok(Value(ValueKind::Text(
                    row.try_get(0).map_err(|_| Error::Denied)?,
                )));
            }
            let count = self.connection.query(
                "SELECT COUNT(*) FROM diamond_w7.jobs WHERE app=$1",
                &[&self.app],
            )?[0]
                .try_get::<_, i64>(0)
                .map_err(|_| Error::Denied)?;
            if count >= 10000 {
                return Err(Error::Limit(
                    "job quota; archive terminal jobs administratively",
                ));
            }
            let id = crate::authority::random_hex()?;
            let rows=self.connection.query("INSERT INTO diamond_w7.jobs(app,id,binding,version,parent,owner,payload,dedup,restrictions,unscoped,state) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'pending') ON CONFLICT(app,binding,owner,dedup) DO UPDATE SET dedup=EXCLUDED.dedup WHERE diamond_w7.jobs.parent=EXCLUDED.parent AND diamond_w7.jobs.payload=EXCLUDED.payload AND diamond_w7.jobs.version=EXCLUDED.version AND diamond_w7.jobs.restrictions=EXCLUDED.restrictions AND diamond_w7.jobs.unscoped=EXCLUDED.unscoped RETURNING id", &[&self.app,&id,&binding,&shape,&parent_or_id,&owner,&payload,&dedup,&restrictions,&scope.allows_unscoped()])?;
            return Ok(Value(ValueKind::Text(
                rows.first()
                    .ok_or(Error::Conflict)?
                    .try_get(0)
                    .map_err(|_| Error::Denied)?,
            )));
        }
        let rows=self.connection.query("SELECT parent,owner,state,attempts FROM diamond_w7.jobs WHERE app=$1 AND id=$2 AND binding=$3 FOR UPDATE", &[&self.app,&parent_or_id,&binding])?;
        let row = rows.first().ok_or(Error::Denied)?;
        let parent: String = row.try_get(0).map_err(|_| Error::Denied)?;
        if row.try_get::<_, String>(1).map_err(|_| Error::Denied)? != owner {
            return Err(Error::Denied);
        }
        self.attachment(scope, &config.resource, &parent, method == "job_cancel")?;
        if method == "job_cancel" {
            self.connection.query("UPDATE diamond_w7.jobs SET state='cancelled',fence=fence+1,outcome='cancelled' WHERE app=$1 AND id=$2 AND state IN ('pending','running')", &[&self.app,&parent_or_id])?;
        }
        let rows = self.connection.query(
            "SELECT state,attempts,outcome FROM diamond_w7.jobs WHERE app=$1 AND id=$2",
            &[&self.app, &parent_or_id],
        )?;
        let state: String = rows[0].try_get(0).map_err(|_| Error::Denied)?;
        let attempts: i32 = rows[0].try_get(1).map_err(|_| Error::Denied)?;
        let outcome: String = rows[0].try_get(2).map_err(|_| Error::Denied)?;
        Ok(Value(ValueKind::Text(
            serde_json::json!({"state":state,"attempts":attempts,"outcome":outcome}).to_string(),
        )))
    }
}
