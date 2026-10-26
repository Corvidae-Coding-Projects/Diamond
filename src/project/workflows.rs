//! Typed, transactional durable state machines with runtime-owned safety.

use super::{CheckedProject, Manifest, Module, artifact, input, rpc, storage::PostgresStore, wire};
use crate::{
    authority::{Effect, Effects, Principal, Restrictions, Scope, random_hex},
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
    pub state: String,
    pub initial: String,
    pub terminal: BTreeSet<String>,
    #[serde(default)]
    pub timers: BTreeMap<String, u32>,
    #[serde(default)]
    pub compensation: Option<String>,
    pub grants: BTreeSet<String>,
    #[serde(default = "attempts")]
    pub max_attempts: i32,
    #[serde(default = "transitions")]
    pub max_transitions: i32,
    pub retry: String,
    pub concurrency: String,
}

const fn attempts() -> i32 {
    3
}
const fn transitions() -> i32 {
    64
}

#[derive(Clone, Debug)]
pub(super) struct Operations {
    pub step: rpc::Operation,
    pub compensation: Option<rpc::Operation>,
}

const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_workflows;
CREATE TABLE IF NOT EXISTS diamond_workflows.apps(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app), shape TEXT NOT NULL, paused BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS diamond_workflows.executions(app TEXT NOT NULL REFERENCES diamond_workflows.apps(app), id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), binding TEXT NOT NULL, version TEXT NOT NULL, parent TEXT NOT NULL, owner TEXT NOT NULL, input TEXT NOT NULL CHECK(octet_length(input)<=16384), state_value TEXT NOT NULL CHECK(octet_length(state_value)<=16384), dedup TEXT NOT NULL CHECK(octet_length(dedup)<=160), restrictions TEXT NOT NULL, unscoped BOOLEAN NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','waiting','running','completed','dead','cancel_requested','cancelling','cancelled')), transitions INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, fence BIGINT NOT NULL DEFAULT 0, lease TIMESTAMPTZ, due TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), outcome TEXT NOT NULL DEFAULT '', PRIMARY KEY(app,id), UNIQUE(app,binding,owner,dedup));
CREATE INDEX IF NOT EXISTS workflow_due ON diamond_workflows.executions(app,due) WHERE status IN ('pending','waiting','running','cancel_requested','cancelling');";

pub(super) fn schema(
    store: &mut PostgresStore,
    project: &CheckedProject,
    initialize: bool,
) -> Result<()> {
    let shape = artifact::hash(
        &serde_json::to_vec(&project.manifest.workflows).map_err(|_| Error::Denied)?,
    );
    if initialize {
        store.connection.batch(SCHEMA)?;
        store.connection.query(
            "INSERT INTO diamond_workflows.apps(app,shape) VALUES($1,$2) ON CONFLICT DO NOTHING",
            &[&store.app, &shape],
        )?;
    }
    let rows = store.connection.query(
        "SELECT shape FROM diamond_workflows.apps WHERE app=$1",
        &[&store.app],
    )?;
    if rows
        .first()
        .map(|row| row.try_get::<_, String>(0))
        .transpose()
        .map_err(|_| Error::Denied)?
        != Some(shape)
    {
        return Err(input(
            "incompatible workflow schema; stop instances and initialize the current profile",
        ));
    }
    Ok(())
}

pub(super) fn version(project: &CheckedProject) -> Result<String> {
    let sources = project
        .modules
        .values()
        .filter(|module| module.config.target != super::Target::Browser)
        .map(|module| (&module.config.path, &project.sources[&module.config.path]))
        .collect::<BTreeMap<_, _>>();
    Ok(artifact::hash(
        &serde_json::to_vec(&(&project.manifest.workflows, sources)).map_err(|_| Error::Denied)?,
    ))
}

fn operation_map(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
    functions: BTreeMap<String, String>,
) -> Result<BTreeMap<String, rpc::Operation>> {
    let mut proxy = manifest.clone();
    proxy.rpc = functions;
    rpc::operations(&proxy, modules, program)
}

fn public_variant<'a>(
    program: &'a CheckedProgram,
    enumeration: &str,
    public: &str,
) -> Option<(&'a str, &'a [(String, Type)])> {
    program
        .enums
        .get(enumeration)?
        .variants
        .iter()
        .find_map(|(name, fields)| {
            (super::resolve::public_name(name) == public)
                .then_some((name.as_str(), fields.as_slice()))
        })
}

pub(super) fn checked(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
) -> Result<BTreeMap<String, Operations>> {
    if manifest.workflows.is_empty() {
        return Ok(BTreeMap::new());
    }
    if manifest.identity.is_none()
        || manifest.storage.is_none()
        || manifest.workflows.len() > 32
        || !manifest.provides.contains("Workflows")
    {
        return Err(input(
            "workflows require Workflows, accounts, relational storage, and at most 32 bindings",
        ));
    }
    let mut functions = BTreeMap::new();
    for (name, binding) in &manifest.workflows {
        functions.insert(format!("workflow_{name}_step"), binding.function.clone());
        if let Some(compensation) = &binding.compensation {
            functions.insert(format!("workflow_{name}_compensate"), compensation.clone());
        }
    }
    let checked = operation_map(manifest, modules, program, functions)?;
    manifest
        .workflows
        .iter()
        .map(|(name, binding)| {
            Ok((
                name.clone(),
                checked_binding(name, binding, &checked, program)?,
            ))
        })
        .collect()
}

fn checked_binding(
    name: &str,
    binding: &Binding,
    checked: &BTreeMap<String, rpc::Operation>,
    program: &CheckedProgram,
) -> Result<Operations> {
    let step = checked[&format!("workflow_{name}_step")].clone();
    let state = Type::Enum(
        program
            .enums
            .keys()
            .find(|candidate| super::resolve::public_name(candidate) == binding.state)
            .cloned()
            .ok_or_else(|| input("workflow state must name a declared enum"))?,
    );
    let Type::Enum(enumeration) = &state else {
        unreachable!()
    };
    public_variant(program, enumeration, &binding.initial)
        .filter(|(_, fields)| fields.is_empty())
        .ok_or_else(|| input("workflow initial state must be a fieldless enum variant"))?;
    let public_variants = program.enums[enumeration]
        .variants
        .keys()
        .map(|variant| super::resolve::public_name(variant).to_owned())
        .collect::<BTreeSet<_>>();
    if binding.terminal.is_empty()
        || binding.terminal.contains(&binding.initial)
        || !binding.terminal.is_subset(&public_variants)
        || !binding
            .timers
            .keys()
            .all(|variant| public_variants.contains(variant) && !binding.terminal.contains(variant))
        || binding
            .timers
            .values()
            .any(|seconds| !(1..=86_400).contains(seconds))
        || !(1..=8).contains(&binding.max_attempts)
        || !(1..=256).contains(&binding.max_transitions)
        || binding.retry != "transactional"
        || binding.concurrency != "parent"
        || !program.resources.contains_key(&binding.resource)
        || step.arguments.len() != 3
        || step.arguments[0] != Type::Text
        || step.arguments[2] != state
        || step.result != state
        || !wire::safe_type(&step.arguments[1], program)
    {
        return Err(input(
            "workflow requires a typed (Text, input, state) -> Result<state, DataError> handler, explicit terminal states, bounded timers/retries/transitions, transactional retry, and parent concurrency",
        ));
    }
    let grants = binding
        .grants
        .iter()
        .map(|grant| super::capability_effect(grant))
        .collect::<Result<Effects>>()?;
    let forbidden_effect = |operation: &rpc::Operation| {
        program.functions[&operation.function]
            .effects
            .iter()
            .any(|effect| matches!(effect, Effect::Host(name) if name != "Events"))
    };
    if !grants.contains(&Effect::Read(binding.resource.clone()))
        || !grants.contains(&Effect::Write(binding.resource.clone()))
        || !program.functions[&step.function].effects.is_subset(&grants)
        || !grants.is_subset(&program.grants)
        || forbidden_effect(&step)
    {
        return Err(input(
            "workflow handlers require explicit parent read/write grants and may not perform uncertain external effects",
        ));
    }
    let compensation = checked.get(&format!("workflow_{name}_compensate")).cloned();
    if let Some(compensation) = &compensation
        && (compensation.arguments != step.arguments
            || compensation.result != Type::Text
            || !program.functions[&compensation.function]
                .effects
                .is_subset(&grants)
            || forbidden_effect(compensation))
    {
        return Err(input(
            "workflow compensation requires the same parent/input/state arguments, a Text result, explicit grants, and transactional effects",
        ));
    }
    Ok(Operations { step, compensation })
}

fn text(value: Option<&Value>) -> Result<&str> {
    match value {
        Some(Value(ValueKind::Text(value))) => Ok(value),
        _ => Err(Error::Denied),
    }
}

fn bounded(value: &Value) -> Result<String> {
    let encoded = wire::encode(value)?.to_string();
    if encoded.len() > 16_384 {
        Err(Error::Limit("workflow value"))
    } else {
        Ok(encoded)
    }
}

fn initial_value(program: &CheckedProgram, binding: &Binding) -> Result<Value> {
    let enumeration = program
        .enums
        .keys()
        .find(|name| super::resolve::public_name(name) == binding.state)
        .ok_or(Error::Denied)?;
    let (variant, _) =
        public_variant(program, enumeration, &binding.initial).ok_or(Error::Denied)?;
    Ok(Value(ValueKind::Variant(
        enumeration.clone(),
        variant.into(),
        Vec::new(),
    )))
}

impl PostgresStore {
    pub(crate) fn workflow_call(
        &mut self,
        scope: &Scope<'_>,
        method: &str,
        values: &[Value],
    ) -> Result<Value> {
        scope.require(&Effect::Host("Workflows".into()))?;
        let binding_name = text(values.first())?;
        let binding = self
            .workflows
            .get(binding_name)
            .cloned()
            .ok_or(Error::Denied)?;
        let operation = self
            .workflow_operations
            .get(binding_name)
            .cloned()
            .ok_or(Error::Denied)?;
        let parent_or_id = text(values.get(1))?;
        if method == "workflow_submit" {
            return self.submit_workflow(scope, &binding, &operation, parent_or_id, values);
        }
        self.control_workflow(
            scope,
            method,
            binding_name,
            &binding,
            &operation,
            parent_or_id,
        )
    }

    fn submit_workflow(
        &mut self,
        scope: &Scope<'_>,
        binding: &Binding,
        operation: &Operations,
        parent: &str,
        values: &[Value],
    ) -> Result<Value> {
        let binding_name = text(values.first())?;
        let owner = scope.principal().id().ok_or(Error::Unauthenticated)?;
        self.attachment(scope, &binding.resource, parent, true)?;
        let input_value = values.get(2).ok_or(Error::Denied)?;
        wire::decode(
            &operation.step.arguments[1],
            &wire::encode(input_value)?,
            &self.program,
        )?;
        let input_encoded = bounded(input_value)?;
        let state = bounded(&initial_value(&self.program, binding)?)?;
        let dedup = text(values.get(3))?;
        if !(1..=160).contains(&dedup.len()) {
            return Err(input("workflow idempotency key requires 1..160 bytes"));
        }
        let restrictions =
            serde_json::to_string(&scope.restrictions()).map_err(|_| Error::Denied)?;
        let version = self.workflow_version.clone();
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,31))",
            &[&format!("{}:{binding_name}:{parent}", self.app)],
        )?;
        let existing=self.connection.query("SELECT id,parent,input,version,restrictions,unscoped FROM diamond_workflows.executions WHERE app=$1 AND binding=$2 AND owner=$3 AND dedup=$4", &[&self.app,&binding_name,&owner,&dedup])?;
        if let Some(row) = existing.first() {
            let same = row.try_get::<_, String>(1).map_err(|_| Error::Denied)? == parent
                && row.try_get::<_, String>(2).map_err(|_| Error::Denied)? == input_encoded
                && row.try_get::<_, String>(3).map_err(|_| Error::Denied)? == version
                && row.try_get::<_, String>(4).map_err(|_| Error::Denied)? == restrictions
                && row.try_get::<_, bool>(5).map_err(|_| Error::Denied)? == scope.allows_unscoped();
            if !same {
                return Err(Error::Conflict);
            }
            return Ok(Value(ValueKind::Text(
                row.try_get(0).map_err(|_| Error::Denied)?,
            )));
        }
        let active=self.connection.query("SELECT id FROM diamond_workflows.executions WHERE app=$1 AND binding=$2 AND parent=$3 AND status IN ('pending','waiting','running','cancel_requested','cancelling') LIMIT 1", &[&self.app,&binding_name,&parent])?;
        if !active.is_empty() {
            return Err(Error::Conflict);
        }
        let count = self.connection.query(
            "SELECT COUNT(*) FROM diamond_workflows.executions WHERE app=$1",
            &[&self.app],
        )?[0]
            .try_get::<_, i64>(0)
            .map_err(|_| Error::Denied)?;
        if count >= 10_000 {
            return Err(Error::Limit("workflow execution quota"));
        }
        let id = random_hex()?;
        self.connection.query("INSERT INTO diamond_workflows.executions(app,id,binding,version,parent,owner,input,state_value,dedup,restrictions,unscoped,status) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'pending')", &[&self.app,&id,&binding_name,&version,&parent,&owner,&input_encoded,&state,&dedup,&restrictions,&scope.allows_unscoped()])?;
        Ok(Value(ValueKind::Text(id)))
    }

    fn control_workflow(
        &mut self,
        scope: &Scope<'_>,
        method: &str,
        binding_name: &str,
        binding: &Binding,
        operation: &Operations,
        identifier: &str,
    ) -> Result<Value> {
        let owner = scope.principal().id().ok_or(Error::Unauthenticated)?;
        let rows=self.connection.query("SELECT parent,owner,state_value,status,transitions,attempts,outcome FROM diamond_workflows.executions WHERE app=$1 AND id=$2 AND binding=$3 FOR UPDATE", &[&self.app,&identifier,&binding_name])?;
        let row = rows.first().ok_or(Error::Denied)?;
        let parent: String = row.try_get(0).map_err(|_| Error::Denied)?;
        if row.try_get::<_, String>(1).map_err(|_| Error::Denied)? != owner {
            return Err(Error::Denied);
        }
        self.attachment(
            scope,
            &binding.resource,
            &parent,
            method == "workflow_cancel",
        )?;
        if method == "workflow_cancel" {
            self.connection.query("UPDATE diamond_workflows.executions SET status='cancel_requested',fence=fence+1,lease=NULL,due=clock_timestamp(),outcome='cancel_requested' WHERE app=$1 AND id=$2 AND status IN ('pending','waiting','running')", &[&self.app,&identifier])?;
        } else if method == "workflow_state" {
            let encoded: String = row.try_get(2).map_err(|_| Error::Denied)?;
            return wire::decode(
                &operation.step.arguments[2],
                &serde_json::from_str(&encoded).map_err(|_| Error::Denied)?,
                &self.program,
            );
        } else if method != "workflow_status" {
            return Err(Error::Denied);
        }
        let rows=self.connection.query("SELECT status,transitions,attempts,outcome FROM diamond_workflows.executions WHERE app=$1 AND id=$2", &[&self.app,&identifier])?;
        Ok(Value(ValueKind::Text(
            serde_json::json!({
                "status":rows[0].try_get::<_,String>(0).map_err(|_| Error::Denied)?,
                "transitions":rows[0].try_get::<_,i32>(1).map_err(|_| Error::Denied)?,
                "attempts":rows[0].try_get::<_,i32>(2).map_err(|_| Error::Denied)?,
                "outcome":rows[0].try_get::<_,String>(3).map_err(|_| Error::Denied)?
            })
            .to_string(),
        )))
    }
}

#[derive(Debug)]
struct Claim {
    id: String,
    binding: String,
    parent: String,
    owner: String,
    input: String,
    state: String,
    restrictions: String,
    unscoped: bool,
    cancelling: bool,
    transitions: i32,
    attempts: i32,
    fence: i64,
}

fn claim(project: &CheckedProject, configuration: &str) -> Result<Option<Claim>> {
    let mut db = PostgresStore::open_control(project, configuration, None)?;
    let version = version(project)?;
    for (name, binding) in &project.manifest.workflows {
        db.connection.query("UPDATE diamond_workflows.executions SET status='dead',outcome=CASE WHEN status='cancelling' THEN 'compensation_exhausted' ELSE 'attempts_exhausted' END,fence=fence+1,lease=NULL WHERE app=$1 AND binding=$2 AND status IN ('running','cancelling') AND lease<clock_timestamp() AND attempts >= $3", &[&db.app,&name,&binding.max_attempts])?;
    }
    let rows=db.connection.query("WITH candidate AS (SELECT e.id,e.status='cancel_requested' OR e.status='cancelling' AS cancel FROM diamond_workflows.executions e JOIN diamond_workflows.apps a ON e.app=a.app WHERE e.app=$1 AND e.version=$2 AND NOT a.paused AND ((e.status IN ('pending','waiting','cancel_requested') AND e.due<=clock_timestamp()) OR (e.status IN ('running','cancelling') AND e.lease<clock_timestamp())) ORDER BY e.due,e.id FOR UPDATE OF e SKIP LOCKED LIMIT 1) UPDATE diamond_workflows.executions e SET status=CASE WHEN c.cancel THEN 'cancelling' ELSE 'running' END,attempts=e.attempts+1,fence=e.fence+1,lease=clock_timestamp()+INTERVAL '30 seconds' FROM candidate c WHERE e.app=$1 AND e.id=c.id RETURNING e.id,e.binding,e.parent,e.owner,e.input,e.state_value,e.restrictions,e.unscoped,c.cancel,e.transitions,e.attempts,e.fence", &[&db.app,&version])?;
    let value = rows
        .first()
        .map(|row| -> Result<Claim> {
            Ok(Claim {
                id: row.try_get(0).map_err(|_| Error::Denied)?,
                binding: row.try_get(1).map_err(|_| Error::Denied)?,
                parent: row.try_get(2).map_err(|_| Error::Denied)?,
                owner: row.try_get(3).map_err(|_| Error::Denied)?,
                input: row.try_get(4).map_err(|_| Error::Denied)?,
                state: row.try_get(5).map_err(|_| Error::Denied)?,
                restrictions: row.try_get(6).map_err(|_| Error::Denied)?,
                unscoped: row.try_get(7).map_err(|_| Error::Denied)?,
                cancelling: row.try_get(8).map_err(|_| Error::Denied)?,
                transitions: row.try_get(9).map_err(|_| Error::Denied)?,
                attempts: row.try_get(10).map_err(|_| Error::Denied)?,
                fence: row.try_get(11).map_err(|_| Error::Denied)?,
            })
        })
        .transpose()?;
    db.finish_request(Ok(value))
}

fn execute(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
    claim: &Claim,
) -> Result<()> {
    let mut db = PostgresStore::open_with(project, configuration, false, None)?;
    let expected = if claim.cancelling {
        "cancelling"
    } else {
        "running"
    };
    let rows=db.connection.query("SELECT id FROM diamond_workflows.executions WHERE app=$1 AND id=$2 AND fence=$3 AND status=$4 AND lease>clock_timestamp()", &[&db.app,&claim.id,&claim.fence,&expected])?;
    if rows.is_empty() {
        return Err(Error::Conflict);
    }
    let users=db.connection.query("SELECT username FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled FOR SHARE", &[&db.app,&claim.owner])?;
    if users.is_empty() {
        return Err(Error::Unauthenticated);
    }
    let binding = &project.manifest.workflows[&claim.binding];
    let operations = &project.workflows[&claim.binding];
    let grants = binding
        .grants
        .iter()
        .map(|grant| super::capability_effect(grant))
        .collect::<Result<Effects>>()?;
    let principal = Principal::User {
        id: claim.owner.clone(),
        roles: BTreeSet::new(),
    };
    let restrictions: Option<Restrictions> =
        serde_json::from_str(&claim.restrictions).map_err(|_| Error::Denied)?;
    let scope = restrictions.as_ref().map_or_else(
        || Scope::new(&principal, &grants),
        |value| Scope::restricted(&principal, &grants, value, claim.unscoped),
    );
    db.attachment(&scope, &binding.resource, &claim.parent, true)?;
    let input = wire::decode(
        &operations.step.arguments[1],
        &serde_json::from_str(&claim.input).map_err(|_| Error::Denied)?,
        &project.server,
    )?;
    let state = wire::decode(
        &operations.step.arguments[2],
        &serde_json::from_str(&claim.state).map_err(|_| Error::Denied)?,
        &project.server,
    )?;
    if claim.cancelling {
        if let Some(compensation) = &operations.compensation {
            crate::eval::Interpreter::new(&project.server, &scope, None, None)
                .with_integrations(runtime)
                .with_postgres(&mut db)
                .function(
                    &compensation.function,
                    &[Value(ValueKind::Text(claim.parent.clone())), input, state],
                )
                .and_then(Value::into_result)?
                .display_text()?;
        }
        let rows=db.connection.query("UPDATE diamond_workflows.executions SET status='cancelled',outcome='cancelled',lease=NULL WHERE app=$1 AND id=$2 AND fence=$3 AND status='cancelling' AND lease>clock_timestamp() RETURNING id", &[&db.app,&claim.id,&claim.fence])?;
        return db.finish_request(if rows.is_empty() {
            Err(Error::Conflict)
        } else {
            Ok(())
        });
    }
    if claim.transitions >= binding.max_transitions {
        return db.finish_request(Err(Error::Limit("workflow transition budget")));
    }
    let next = crate::eval::Interpreter::new(&project.server, &scope, None, None)
        .with_integrations(runtime)
        .with_postgres(&mut db)
        .function(
            &operations.step.function,
            &[Value(ValueKind::Text(claim.parent.clone())), input, state],
        )
        .and_then(Value::into_result)?;
    let encoded = bounded(&next)?;
    let tag = match &next.0 {
        ValueKind::Variant(_, name, _) => super::resolve::public_name(name).to_owned(),
        _ => return db.finish_request(Err(Error::Denied)),
    };
    let terminal = binding.terminal.contains(&tag);
    let delay =
        i32::try_from(binding.timers.get(&tag).copied().unwrap_or(0)).map_err(|_| Error::Denied)?;
    let status = if terminal {
        "completed"
    } else if delay > 0 {
        "waiting"
    } else {
        "pending"
    };
    let outcome = if terminal {
        format!("terminal:{tag}")
    } else {
        "advanced".into()
    };
    let rows=db.connection.query("UPDATE diamond_workflows.executions SET state_value=$4,status=$5,outcome=$6,transitions=transitions+1,attempts=0,lease=NULL,due=clock_timestamp()+$7::integer*INTERVAL '1 second' WHERE app=$1 AND id=$2 AND fence=$3 AND status='running' AND lease>clock_timestamp() RETURNING id", &[&db.app,&claim.id,&claim.fence,&encoded,&status,&outcome,&delay])?;
    db.finish_request(if rows.is_empty() {
        Err(Error::Conflict)
    } else {
        Ok(())
    })
}

pub(super) fn step(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
) -> Result<bool> {
    let Some(claim) = claim(project, configuration)? else {
        return Ok(false);
    };
    if let Err(error) = execute(project, configuration, runtime, &claim) {
        eprintln!(
            "{}",
            serde_json::json!({"event":"workflow_attempt_failed","class":match &error {Error::Denied=>"denied",Error::Unauthenticated=>"unauthenticated",Error::Conflict=>"conflict",_=>"failure"}})
        );
        let mut db = PostgresStore::open_control(project, configuration, None)?;
        let binding = &project.manifest.workflows[&claim.binding];
        let terminal = matches!(
            error,
            Error::Denied | Error::Unauthenticated | Error::Limit(_)
        ) || claim.attempts >= binding.max_attempts;
        let status = if terminal {
            "dead"
        } else if claim.cancelling {
            "cancel_requested"
        } else {
            "pending"
        };
        let outcome = if terminal {
            if claim.cancelling {
                "compensation_failed"
            } else {
                "denied_or_exhausted"
            }
        } else {
            "retry"
        };
        let delay = 1_i32
            .checked_shl(u32::try_from(claim.attempts.saturating_sub(1)).unwrap_or(8))
            .unwrap_or(60)
            .min(60);
        db.connection.query("UPDATE diamond_workflows.executions SET status=$4,outcome=$5,lease=NULL,due=clock_timestamp()+$6::integer*INTERVAL '1 second' WHERE app=$1 AND id=$2 AND fence=$3 AND status IN ('running','cancelling')", &[&db.app,&claim.id,&claim.fence,&status,&outcome,&delay])?;
        db.finish_request(Ok(()))?;
    }
    Ok(true)
}

pub(super) fn contracts() -> serde_json::Value {
    serde_json::json!({
        "capability":"Workflows",
        "state":"declared wire-safe enum; fieldless initial variant; explicit terminal variants",
        "execution":"transactional one-transition attempts with leases, fences, current authority, and parent concurrency",
        "timers":"manifest-bound database-clock delay by resulting state",
        "cancellation":"fenced cancel request with optional transactional compensation",
        "external_effects":"prohibited in step and compensation handlers",
        "limits":{"bindings":32,"attempts":8,"transitions":256,"value_bytes":16384,"executions":10000}
    })
}
