//! Shared G4 coordination, search, replica, garbage-collection, and recovery state.

use super::{CheckedProject, Manifest, artifact, input, storage::PostgresStore};
use crate::{
    Error,
    authority::{Effect, Scope},
    error::Result,
    hir::CheckedProgram,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read as _, Write as _},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

const MARKER: &str = ".diamond-operations-store";
const MAX_TASKS: i64 = 10_000;
const MAX_SEARCH_DOCUMENTS: usize = 10_000;
const MAX_COMMAND_OUTPUT: usize = 65_536;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    profile: String,
    pub(super) bindings: BTreeMap<String, Binding>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub(super) resource: String,
    pub(super) git: String,
    pub(super) delivery: String,
    pub(super) primary_shard: String,
    #[serde(default)]
    search_resources: Vec<String>,
    #[serde(default = "node_ttl")]
    node_ttl_seconds: u64,
    #[serde(default = "rate_limit")]
    rate_per_minute: i64,
    #[serde(default = "gc_grace")]
    gc_grace_seconds: u64,
}

const fn node_ttl() -> u64 {
    30
}

const fn rate_limit() -> i64 {
    600
}

const fn gc_grace() -> u64 {
    3600
}

impl Config {
    pub(super) fn check(&self, manifest: &Manifest, program: &CheckedProgram) -> Result<()> {
        let git = manifest.git.as_ref().ok_or(Error::Denied)?;
        let delivery = manifest.delivery.as_ref().ok_or(Error::Denied)?;
        if self.profile != "operations-v1" || self.bindings.is_empty() || self.bindings.len() > 16 {
            return Err(input("operations requires the operations-v1 profile"));
        }
        for (name, binding) in &self.bindings {
            if !super::resolve::identifier(name)
                || !program.resources.contains_key(&binding.resource)
                || !git.bindings.contains_key(&binding.git)
                || !delivery.bindings.contains_key(&binding.delivery)
                || !super::resolve::identifier(&binding.primary_shard)
                || binding.search_resources.is_empty()
                || binding.search_resources.len() > 16
                || binding
                    .search_resources
                    .iter()
                    .any(|resource| !program.resources.contains_key(resource))
                || !(5..=300).contains(&binding.node_ttl_seconds)
                || !(10..=100_000).contains(&binding.rate_per_minute)
                || !(1..=2_592_000).contains(&binding.gc_grace_seconds)
                || !manifest.provides.contains(&format!("OpsRead<{name}>"))
                || !manifest.provides.contains(&format!("OpsAdmin<{name}>"))
                || !manifest
                    .provides
                    .contains(&format!("DbRead<{}>", binding.resource))
                || binding
                    .search_resources
                    .iter()
                    .any(|resource| !manifest.provides.contains(&format!("DbRead<{resource}>")))
            {
                return Err(input(
                    "invalid operations binding or absent Git, delivery, or resource capability",
                ));
            }
        }
        let mut git_bindings = BTreeSet::new();
        if self
            .bindings
            .values()
            .any(|binding| !git_bindings.insert(binding.git.as_str()))
        {
            return Err(input(
                "each Git binding may have only one operations placement controller",
            ));
        }
        Ok(())
    }
}

const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_g4_ops;
CREATE TABLE IF NOT EXISTS diamond_g4_ops.stores(app TEXT NOT NULL REFERENCES diamond_w2.apps(app), binding TEXT NOT NULL, shape TEXT NOT NULL, git_binding TEXT NOT NULL, delivery_binding TEXT NOT NULL, primary_shard TEXT NOT NULL CHECK(primary_shard ~ '^[-_A-Za-z0-9]{1,128}$'), identity TEXT, policy_generation BIGINT NOT NULL DEFAULT 1 CHECK(policy_generation>0), PRIMARY KEY(app,binding), UNIQUE(app,git_binding));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.nodes(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[-_A-Za-z0-9]{1,128}$'), kind TEXT NOT NULL CHECK(kind IN ('web','worker','search','pages','registry')), version TEXT NOT NULL CHECK(octet_length(version) BETWEEN 1 AND 128), state TEXT NOT NULL CHECK(state IN ('active','draining','offline')), capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 4096), generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), heartbeat TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id), FOREIGN KEY(app,binding) REFERENCES diamond_g4_ops.stores(app,binding));
CREATE INDEX IF NOT EXISTS g4_nodes_heartbeat ON diamond_g4_ops.nodes(app,binding,heartbeat);
CREATE TABLE IF NOT EXISTS diamond_g4_ops.shards(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[-_A-Za-z0-9]{1,128}$'), identity TEXT NOT NULL CHECK(octet_length(identity) BETWEEN 1 AND 128), state TEXT NOT NULL CHECK(state IN ('active','draining','fenced')), generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id), FOREIGN KEY(app,binding) REFERENCES diamond_g4_ops.stores(app,binding));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.placements(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL CHECK(repository ~ '^[-_A-Za-z0-9]{1,128}$'), shard TEXT NOT NULL, object TEXT NOT NULL DEFAULT '' CHECK(object='' OR octet_length(object) IN (40,64)), generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,repository), FOREIGN KEY(app,binding,shard) REFERENCES diamond_g4_ops.shards(app,binding,id));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.replicas(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, shard TEXT NOT NULL, object TEXT NOT NULL CHECK(octet_length(object) IN (40,64)), verified BOOLEAN NOT NULL, updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,repository,shard), FOREIGN KEY(app,binding,shard) REFERENCES diamond_g4_ops.shards(app,binding,id));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.search_documents(app TEXT NOT NULL, binding TEXT NOT NULL, document TEXT NOT NULL CHECK(document ~ '^[0-9a-f]{64}$'), repository TEXT NOT NULL, kind TEXT NOT NULL CHECK(octet_length(kind) BETWEEN 1 AND 80), title TEXT NOT NULL CHECK(octet_length(title)<=1024), body TEXT NOT NULL CHECK(octet_length(body)<=8192), generation BIGINT NOT NULL CHECK(generation>0), updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,document));
CREATE INDEX IF NOT EXISTS g4_search_text ON diamond_g4_ops.search_documents USING GIN (to_tsvector('simple',title||' '||body));
CREATE INDEX IF NOT EXISTS g4_search_repository ON diamond_g4_ops.search_documents(app,binding,repository);
CREATE TABLE IF NOT EXISTS diamond_g4_ops.tasks(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), kind TEXT NOT NULL CHECK(kind IN ('search','mirror','promote','gc')), subject TEXT NOT NULL CHECK(octet_length(subject)<=160), argument TEXT NOT NULL CHECK(octet_length(argument)<=256), dedup TEXT NOT NULL CHECK(octet_length(dedup) BETWEEN 1 AND 160), state TEXT NOT NULL CHECK(state IN ('pending','running','done','failed')), attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8), fence BIGINT NOT NULL DEFAULT 0, lease TIMESTAMPTZ, outcome TEXT NOT NULL DEFAULT '' CHECK(octet_length(outcome)<=1024), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), finished TIMESTAMPTZ, PRIMARY KEY(app,binding,id), UNIQUE(app,binding,kind,dedup), FOREIGN KEY(app,binding) REFERENCES diamond_g4_ops.stores(app,binding));
CREATE INDEX IF NOT EXISTS g4_task_claim ON diamond_g4_ops.tasks(app,state,created);
CREATE TABLE IF NOT EXISTS diamond_g4_ops.mirrors(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), repository TEXT NOT NULL, target TEXT NOT NULL, expected_object TEXT NOT NULL CHECK(expected_object='' OR octet_length(expected_object) IN (40,64)), state TEXT NOT NULL CHECK(state IN ('pending','running','verified','failed')), generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id), UNIQUE(app,binding,repository,target), FOREIGN KEY(app,binding,target) REFERENCES diamond_g4_ops.shards(app,binding,id));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.holds(app TEXT NOT NULL, binding TEXT NOT NULL, digest TEXT NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'), reason TEXT NOT NULL CHECK(octet_length(reason) BETWEEN 1 AND 160), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,digest));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.gc_runs(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), removed INTEGER NOT NULL CHECK(removed BETWEEN 0 AND 1000), retained INTEGER NOT NULL CHECK(retained BETWEEN 0 AND 1000000), bytes BIGINT NOT NULL CHECK(bytes>=0), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.rates(app TEXT NOT NULL, binding TEXT NOT NULL, bucket TEXT NOT NULL CHECK(bucket ~ '^[0-9a-f]{64}$'), attempts BIGINT NOT NULL CHECK(attempts>0), expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,binding,bucket));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.backups(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[-_A-Za-z0-9]{1,128}$'), state TEXT NOT NULL CHECK(state IN ('pending','complete','failed')), digest TEXT NOT NULL CHECK(digest='' OR digest ~ '^[0-9a-f]{64}$'), source_generation BIGINT NOT NULL CHECK(source_generation>0), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.restores(app TEXT NOT NULL, binding TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[-_A-Za-z0-9]{1,128}$'), backup TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('isolated','verified','failed','promoted')), details TEXT NOT NULL CHECK(octet_length(details)<=1024), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.policies(app TEXT NOT NULL, binding TEXT NOT NULL, key TEXT NOT NULL CHECK(key ~ '^[-_A-Za-z0-9]{1,128}$'), value TEXT NOT NULL CHECK(octet_length(value)<=1024), generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), updated TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,key));
CREATE TABLE IF NOT EXISTS diamond_g4_ops.audit(app TEXT NOT NULL, binding TEXT NOT NULL, id BIGINT GENERATED ALWAYS AS IDENTITY, actor TEXT NOT NULL CHECK(octet_length(actor) BETWEEN 1 AND 160), action TEXT NOT NULL CHECK(octet_length(action) BETWEEN 1 AND 80), subject TEXT NOT NULL CHECK(octet_length(subject)<=160), created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(app,binding,id));";

pub(super) fn schema(store: &mut PostgresStore, config: &Config, initialize: bool) -> Result<()> {
    let shape = artifact::hash(&serde_json::to_vec(config).map_err(|_| Error::Denied)?);
    if initialize {
        store.connection.batch(SCHEMA)?;
        for (binding, value) in &config.bindings {
            store.connection.query(
                "INSERT INTO diamond_g4_ops.stores(app,binding,shape,git_binding,delivery_binding,primary_shard) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
                &[&store.app, binding, &shape, &value.git, &value.delivery, &value.primary_shard],
            )?;
        }
    }
    let rows = store.connection.query(
        "SELECT binding,shape FROM diamond_g4_ops.stores WHERE app=$1 ORDER BY binding",
        &[&store.app],
    )?;
    let stored = rows
        .iter()
        .map(|row| {
            Ok((
                row.try_get::<_, String>(0).map_err(|_| Error::Denied)?,
                row.try_get::<_, String>(1).map_err(|_| Error::Denied)?,
            ))
        })
        .collect::<Result<BTreeMap<_, _>>>()?;
    if stored.len() != config.bindings.len()
        || config
            .bindings
            .keys()
            .any(|binding| stored.get(binding) != Some(&shape))
    {
        return Err(input(
            "incompatible operations metadata; stop instances and initialize the current schema",
        ));
    }
    Ok(())
}

fn binding(database: &PostgresStore, name: &str) -> Result<Binding> {
    database
        .operations
        .as_ref()
        .and_then(|config| config.bindings.get(name))
        .cloned()
        .ok_or(Error::Denied)
}

#[derive(Debug)]
pub(super) struct GitRoute {
    pub(super) root: PathBuf,
    pub(super) shard: String,
    pub(super) store_identity: String,
    pub(super) placement_generation: i64,
}

fn git_controller(
    database: &PostgresStore,
    git_binding: &str,
) -> Result<Option<(String, Binding)>> {
    let mut matching = database
        .operations
        .as_ref()
        .into_iter()
        .flat_map(|config| &config.bindings)
        .filter(|(_, config)| config.git == git_binding);
    let result = matching
        .next()
        .map(|(name, config)| (name.clone(), config.clone()));
    if matching.next().is_some() {
        return Err(Error::Denied);
    }
    Ok(result)
}

/// Resolve the one authoritative filesystem root for a repository placement.
pub(super) fn git_route(
    database: &mut PostgresStore,
    grants: &crate::RuntimeGrants,
    git_binding: &str,
    repository: &str,
) -> Result<Option<GitRoute>> {
    let Some((operations_binding, config)) = git_controller(database, git_binding)? else {
        return Ok(None);
    };
    if !crate::auth::valid_id(repository) {
        return Err(Error::Denied);
    }
    let rows = database.connection.query(
        "SELECT p.shard,p.generation,s.identity,s.state FROM diamond_g4_ops.placements p JOIN diamond_g4_ops.shards s ON s.app=p.app AND s.binding=p.binding AND s.id=p.shard WHERE p.app=$1 AND p.binding=$2 AND p.repository=$3",
        &[&database.app, &operations_binding, &repository],
    )?;
    let row = rows.first().ok_or(Error::Conflict)?;
    let shard: String = row.try_get(0).map_err(|_| Error::Denied)?;
    let placement_generation: i64 = row.try_get(1).map_err(|_| Error::Denied)?;
    let store_identity: String = row.try_get(2).map_err(|_| Error::Denied)?;
    let state: String = row.try_get(3).map_err(|_| Error::Denied)?;
    if state != "active"
        || placement_generation <= 0
        || !valid_identity(&store_identity)
        || !super::resolve::identifier(&shard)
    {
        return Err(Error::Conflict);
    }
    let root = if shard == config.primary_shard {
        grants
            .git
            .get(git_binding)
            .ok_or(Error::Denied)?
            .root
            .clone()
    } else {
        grants
            .operations
            .get(&operations_binding)
            .ok_or(Error::Denied)?
            .root
            .join("replicas")
            .join(&shard)
    };
    Ok(Some(GitRoute {
        root,
        shard,
        store_identity,
        placement_generation,
    }))
}

fn valid_identity(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// Register a newly created repository on its controller's primary shard.
pub(super) fn place_primary(
    database: &mut PostgresStore,
    grants: &crate::RuntimeGrants,
    git_binding: &str,
    repository: &str,
) -> Result<()> {
    let Some((operations_binding, config)) = git_controller(database, git_binding)? else {
        return Ok(());
    };
    let identity = super::git::primary_store_identity(grants, git_binding)?;
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.placements(app,binding,repository,shard,object) VALUES($1,$2,$3,$4,'') ON CONFLICT(app,binding,repository) DO UPDATE SET repository=EXCLUDED.repository WHERE diamond_g4_ops.placements.shard=EXCLUDED.shard RETURNING repository",
        &[&database.app, &operations_binding, &repository, &config.primary_shard],
    )?;
    if rows.len() != 1 {
        return Err(Error::Conflict);
    }
    let shards = database.connection.query(
        "SELECT identity,state FROM diamond_g4_ops.shards WHERE app=$1 AND binding=$2 AND id=$3",
        &[&database.app, &operations_binding, &config.primary_shard],
    )?;
    if shards.len() != 1
        || shards[0]
            .try_get::<_, String>(0)
            .map_err(|_| Error::Denied)?
            != identity
        || shards[0]
            .try_get::<_, String>(1)
            .map_err(|_| Error::Denied)?
            != "active"
    {
        return Err(Error::Conflict);
    }
    Ok(())
}

pub(super) fn replace_primary_store_identity(
    database: &mut PostgresStore,
    git_binding: &str,
    prior: &str,
    replacement: &str,
) -> Result<()> {
    let Some((operations_binding, config)) = git_controller(database, git_binding)? else {
        return Ok(());
    };
    if !valid_identity(prior) || !valid_identity(replacement) || prior == replacement {
        return Err(Error::Denied);
    }
    let rows = database.connection.query(
        "UPDATE diamond_g4_ops.shards SET identity=$5,generation=generation+1,updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND id=$3 AND identity=$4 RETURNING id",
        &[&database.app, &operations_binding, &config.primary_shard, &prior, &replacement],
    )?;
    if rows.len() != 1 {
        return Err(Error::Conflict);
    }
    Ok(())
}

fn administrator(scope: &Scope<'_>) -> Result<String> {
    if !scope.principal().has_role("account_admin") {
        return Err(Error::Denied);
    }
    scope
        .principal()
        .id()
        .map(ToOwned::to_owned)
        .ok_or(Error::Unauthenticated)
}

fn authorize_repository(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    config: &Binding,
    repository: &str,
    write: bool,
) -> Result<()> {
    if !crate::auth::valid_id(repository) {
        return Err(Error::Denied);
    }
    let mut effects = BTreeSet::from([Effect::Read(config.resource.clone())]);
    if write {
        effects.insert(Effect::Write(config.resource.clone()));
    }
    database.attachment(
        &Scope::new(scope.principal(), &effects),
        &config.resource,
        repository,
        write,
    )
}

fn valid_text(value: &str, maximum: usize) -> bool {
    !value.is_empty() && value.len() <= maximum && !value.chars().any(char::is_control)
}

fn valid_oid(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn audit(
    database: &mut PostgresStore,
    binding: &str,
    actor: &str,
    action: &str,
    subject: &str,
) -> Result<()> {
    database.connection.query(
        "INSERT INTO diamond_g4_ops.audit(app,binding,actor,action,subject) VALUES($1,$2,$3,$4,$5)",
        &[&database.app, &binding, &actor, &action, &subject],
    )?;
    Ok(())
}

fn task(
    database: &mut PostgresStore,
    binding: &str,
    kind: &str,
    subject: &str,
    argument: &str,
    dedup: &str,
) -> Result<String> {
    if !valid_text(dedup, 160) {
        return Err(input("invalid operations task identity"));
    }
    let count = database.connection.query(
        "SELECT count(*) FROM diamond_g4_ops.tasks WHERE app=$1 AND state IN ('pending','running')",
        &[&database.app],
    )?;
    if count[0].try_get::<_, i64>(0).map_err(|_| Error::Denied)? >= MAX_TASKS {
        return Err(Error::Limit("operations task queue"));
    }
    let id = crate::authority::random_hex()?;
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.tasks(app,binding,id,kind,subject,argument,dedup,state) VALUES($1,$2,$3,$4,$5,$6,$7,'pending') ON CONFLICT(app,binding,kind,dedup) DO UPDATE SET dedup=EXCLUDED.dedup WHERE diamond_g4_ops.tasks.subject=EXCLUDED.subject AND diamond_g4_ops.tasks.argument=EXCLUDED.argument RETURNING id",
        &[&database.app, &binding, &id, &kind, &subject, &argument, &dedup],
    )?;
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get(0)
        .map_err(|_| Error::Denied)
}

/// Execute one fixed, audited administrative operation.
pub(crate) fn command(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    binding_name: &str,
    operation: &str,
    subject: &str,
    values: [&str; 3],
    expected: i64,
) -> Result<String> {
    let actor = administrator(scope)?;
    let config = binding(database, binding_name)?;
    if !super::resolve::identifier(binding_name)
        || !super::resolve::identifier(operation)
        || subject.len() > 160
        || values
            .iter()
            .any(|value| value.len() > 1024 || value.chars().any(char::is_control))
        || expected < 0
    {
        return Err(input("invalid operations command"));
    }
    let result = match operation {
        "node_drain" => {
            let rows = database.connection.query(
                "UPDATE diamond_g4_ops.nodes SET state='draining',generation=generation+1 WHERE app=$1 AND binding=$2 AND id=$3 AND generation=$4 AND state='active' RETURNING generation",
                &[&database.app, &binding_name, &subject, &expected],
            )?;
            rows.first()
                .ok_or(Error::Conflict)?
                .try_get::<_, i64>(0)
                .map(|generation| generation.to_string())
                .map_err(|_| Error::Denied)?
        }
        "shard_record" => shard_record(database, binding_name, subject, values, expected)?,
        "placement_record" => {
            authorize_repository(database, scope, &config, subject, true)?;
            placement_record(database, binding_name, subject, values, expected)?
        }
        "mirror_schedule" => {
            authorize_repository(database, scope, &config, subject, false)?;
            mirror_schedule(database, binding_name, subject, values, expected)?
        }
        "promotion_schedule" => {
            authorize_repository(database, scope, &config, subject, true)?;
            promotion_schedule(database, binding_name, subject, values, expected)?
        }
        "search_schedule" => task(database, binding_name, "search", subject, "", values[0])?,
        "gc_schedule" => task(database, binding_name, "gc", subject, "", values[0])?,
        "hold_set" => hold_set(database, binding_name, subject, values)?,
        "backup_record" => backup_record(database, binding_name, subject, values, expected)?,
        "restore_record" => restore_record(database, binding_name, subject, values)?,
        "policy_set" => policy_set(database, binding_name, subject, values[0], expected)?,
        _ => return Err(Error::Denied),
    };
    audit(database, binding_name, &actor, operation, subject)?;
    Ok(result)
}

fn shard_record(
    database: &mut PostgresStore,
    binding: &str,
    shard: &str,
    values: [&str; 3],
    expected: i64,
) -> Result<String> {
    if !super::resolve::identifier(shard)
        || !values[0].is_empty()
        || !["active", "draining", "fenced"].contains(&values[1])
    {
        return Err(input("invalid shard state"));
    }
    let rows = if expected == 0 {
        let identity = crate::authority::random_hex()?;
        database.connection.query(
            "INSERT INTO diamond_g4_ops.shards(app,binding,id,identity,state) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING generation",
            &[&database.app, &binding, &shard, &identity, &values[1]],
        )?
    } else {
        database.connection.query(
            "UPDATE diamond_g4_ops.shards SET state=$5,generation=generation+1,updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND id=$3 AND generation=$4 RETURNING generation",
            &[&database.app, &binding, &shard, &expected, &values[1]],
        )?
    };
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get::<_, i64>(0)
        .map(|generation| generation.to_string())
        .map_err(|_| Error::Denied)
}

fn promotion_schedule(
    database: &mut PostgresStore,
    binding: &str,
    repository: &str,
    values: [&str; 3],
    expected: i64,
) -> Result<String> {
    let target = values[0];
    let object = values[1];
    let dedup = values[2];
    if !super::resolve::identifier(target)
        || !valid_oid(object)
        || !valid_text(dedup, 160)
        || expected <= 0
    {
        return Err(input("invalid promotion request"));
    }
    let placement = database.connection.query(
        "SELECT generation,object,shard FROM diamond_g4_ops.placements WHERE app=$1 AND binding=$2 AND repository=$3",
        &[&database.app, &binding, &repository],
    )?;
    if placement.len() != 1
        || placement[0]
            .try_get::<_, i64>(0)
            .map_err(|_| Error::Denied)?
            != expected
        || placement[0]
            .try_get::<_, String>(1)
            .map_err(|_| Error::Denied)?
            != object
        || placement[0]
            .try_get::<_, String>(2)
            .map_err(|_| Error::Denied)?
            == target
    {
        return Err(Error::Conflict);
    }
    let replica = database.connection.query(
        "SELECT r.verified,s.state FROM diamond_g4_ops.replicas r JOIN diamond_g4_ops.shards s ON s.app=r.app AND s.binding=r.binding AND s.id=r.shard WHERE r.app=$1 AND r.binding=$2 AND r.repository=$3 AND r.shard=$4 AND r.object=$5",
        &[&database.app, &binding, &repository, &target, &object],
    )?;
    if replica.len() != 1
        || !replica[0]
            .try_get::<_, bool>(0)
            .map_err(|_| Error::Denied)?
        || replica[0]
            .try_get::<_, String>(1)
            .map_err(|_| Error::Denied)?
            != "active"
    {
        return Err(Error::Conflict);
    }
    task(
        database,
        binding,
        "promote",
        repository,
        &format!("{target}:{expected}:{object}"),
        dedup,
    )
}

fn placement_record(
    database: &mut PostgresStore,
    binding: &str,
    repository: &str,
    values: [&str; 3],
    expected: i64,
) -> Result<String> {
    let shard = values[0];
    let object = values[1];
    if !super::resolve::identifier(shard) || !valid_oid(object) {
        return Err(input("invalid placement"));
    }
    let active = database.connection.query(
        "SELECT state FROM diamond_g4_ops.shards WHERE app=$1 AND binding=$2 AND id=$3",
        &[&database.app, &binding, &shard],
    )?;
    if active
        .first()
        .and_then(|row| row.try_get::<_, String>(0).ok())
        .as_deref()
        != Some("active")
    {
        return Err(Error::Conflict);
    }
    let rows = if expected == 0 {
        database.connection.query(
            "INSERT INTO diamond_g4_ops.placements(app,binding,repository,shard,object) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING generation",
            &[&database.app, &binding, &repository, &shard, &object],
        )?
    } else {
        let current = database.connection.query(
            "SELECT p.shard,s.state FROM diamond_g4_ops.placements p JOIN diamond_g4_ops.shards s ON s.app=p.app AND s.binding=p.binding AND s.id=p.shard WHERE p.app=$1 AND p.binding=$2 AND p.repository=$3 AND p.generation=$4",
            &[&database.app, &binding, &repository, &expected],
        )?;
        if current
            .first()
            .and_then(|row| row.try_get::<_, String>(1).ok())
            .as_deref()
            != Some("fenced")
        {
            return Err(Error::Conflict);
        }
        let replica = database.connection.query(
            "SELECT verified FROM diamond_g4_ops.replicas WHERE app=$1 AND binding=$2 AND repository=$3 AND shard=$4 AND object=$5",
            &[&database.app, &binding, &repository, &shard, &object],
        )?;
        if replica
            .first()
            .and_then(|row| row.try_get::<_, bool>(0).ok())
            != Some(true)
        {
            return Err(Error::Conflict);
        }
        database.connection.query(
            "UPDATE diamond_g4_ops.placements SET shard=$5,object=$6,generation=generation+1,updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND generation=$4 RETURNING generation",
            &[&database.app, &binding, &repository, &expected, &shard, &object],
        )?
    };
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get::<_, i64>(0)
        .map(|generation| generation.to_string())
        .map_err(|_| Error::Denied)
}

fn mirror_schedule(
    database: &mut PostgresStore,
    binding: &str,
    repository: &str,
    values: [&str; 3],
    expected: i64,
) -> Result<String> {
    if !super::resolve::identifier(values[0])
        || !valid_oid(values[1])
        || !valid_text(values[2], 160)
        || expected <= 0
    {
        return Err(input("invalid mirror request"));
    }
    let placement = database.connection.query(
        "SELECT generation,object FROM diamond_g4_ops.placements WHERE app=$1 AND binding=$2 AND repository=$3",
        &[&database.app, &binding, &repository],
    )?;
    if placement
        .first()
        .and_then(|row| row.try_get::<_, i64>(0).ok())
        != Some(expected)
        || placement
            .first()
            .and_then(|row| row.try_get::<_, String>(1).ok())
            .as_deref()
            != Some(values[1])
    {
        return Err(Error::Conflict);
    }
    let mirror_id = crate::authority::random_hex()?;
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.mirrors(app,binding,id,repository,target,expected_object,state) VALUES($1,$2,$3,$4,$5,$6,'pending') ON CONFLICT(app,binding,repository,target) DO UPDATE SET expected_object=EXCLUDED.expected_object,state='pending',generation=diamond_g4_ops.mirrors.generation+1,updated=clock_timestamp() RETURNING id",
        &[&database.app, &binding, &mirror_id, &repository, &values[0], &values[1]],
    )?;
    let id: String = rows[0].try_get(0).map_err(|_| Error::Denied)?;
    task(database, binding, "mirror", &id, values[0], values[2])?;
    Ok(id)
}

fn hold_set(
    database: &mut PostgresStore,
    binding: &str,
    digest: &str,
    values: [&str; 3],
) -> Result<String> {
    if !valid_digest(digest) || !["true", "false"].contains(&values[0]) {
        return Err(input("invalid object hold"));
    }
    if values[0] == "true" {
        if !valid_text(values[1], 160) {
            return Err(input("object hold needs a bounded reason"));
        }
        database.connection.query(
            "INSERT INTO diamond_g4_ops.holds(app,binding,digest,reason) VALUES($1,$2,$3,$4) ON CONFLICT(app,binding,digest) DO UPDATE SET reason=EXCLUDED.reason",
            &[&database.app, &binding, &digest, &values[1]],
        )?;
    } else {
        database.connection.query(
            "DELETE FROM diamond_g4_ops.holds WHERE app=$1 AND binding=$2 AND digest=$3",
            &[&database.app, &binding, &digest],
        )?;
    }
    Ok(values[0].to_owned())
}

fn backup_record(
    database: &mut PostgresStore,
    binding: &str,
    id: &str,
    values: [&str; 3],
    generation: i64,
) -> Result<String> {
    if !super::resolve::identifier(id)
        || !["pending", "complete", "failed"].contains(&values[0])
        || !(values[1].is_empty() || valid_digest(values[1]))
        || generation <= 0
    {
        return Err(input("invalid backup catalog entry"));
    }
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.backups(app,binding,id,state,digest,source_generation) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(app,binding,id) DO UPDATE SET state=EXCLUDED.state,digest=EXCLUDED.digest WHERE diamond_g4_ops.backups.source_generation=EXCLUDED.source_generation RETURNING id",
        &[&database.app, &binding, &id, &values[0], &values[1], &generation],
    )?;
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get(0)
        .map_err(|_| Error::Denied)
}

fn restore_record(
    database: &mut PostgresStore,
    binding: &str,
    id: &str,
    values: [&str; 3],
) -> Result<String> {
    if !super::resolve::identifier(id)
        || !super::resolve::identifier(values[0])
        || !["isolated", "verified", "failed", "promoted"].contains(&values[1])
        || values[2].len() > 1024
    {
        return Err(input("invalid restore catalog entry"));
    }
    let backup = database.connection.query(
        "SELECT state FROM diamond_g4_ops.backups WHERE app=$1 AND binding=$2 AND id=$3",
        &[&database.app, &binding, &values[0]],
    )?;
    if backup
        .first()
        .and_then(|row| row.try_get::<_, String>(0).ok())
        .as_deref()
        != Some("complete")
    {
        return Err(Error::Conflict);
    }
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.restores(app,binding,id,backup,state,details) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(app,binding,id) DO UPDATE SET state=EXCLUDED.state,details=EXCLUDED.details WHERE diamond_g4_ops.restores.backup=EXCLUDED.backup RETURNING id",
        &[&database.app, &binding, &id, &values[0], &values[1], &values[2]],
    )?;
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get(0)
        .map_err(|_| Error::Denied)
}

fn policy_set(
    database: &mut PostgresStore,
    binding: &str,
    key: &str,
    value: &str,
    expected: i64,
) -> Result<String> {
    if !super::resolve::identifier(key) || value.len() > 1024 {
        return Err(input("invalid operations policy"));
    }
    let rows = if expected == 0 {
        database.connection.query(
            "INSERT INTO diamond_g4_ops.policies(app,binding,key,value) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING RETURNING generation",
            &[&database.app, &binding, &key, &value],
        )?
    } else {
        database.connection.query(
            "UPDATE diamond_g4_ops.policies SET value=$5,generation=generation+1,updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND key=$3 AND generation=$4 RETURNING generation",
            &[&database.app, &binding, &key, &expected, &value],
        )?
    };
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get::<_, i64>(0)
        .map(|generation| generation.to_string())
        .map_err(|_| Error::Denied)
}

/// Charge a shared application-node rate bucket.
pub(crate) fn rate(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    binding_name: &str,
    bucket: &str,
    cost: i64,
) -> Result<String> {
    let config = binding(database, binding_name)?;
    if !valid_text(bucket, 128) || !(1..=100).contains(&cost) {
        return Err(input("invalid shared rate request"));
    }
    let actor = scope.principal().id().unwrap_or("anonymous");
    let key = artifact::hash(format!("{actor}:{bucket}").as_bytes());
    database.connection.query(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
        &[&format!("{}:{binding_name}:rate:{key}", database.app)],
    )?;
    database.connection.query(
        "DELETE FROM diamond_g4_ops.rates WHERE app=$1 AND binding=$2 AND expires<=clock_timestamp()",
        &[&database.app, &binding_name],
    )?;
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.rates(app,binding,bucket,attempts,expires) VALUES($1,$2,$3,$4,clock_timestamp()+interval '1 minute') ON CONFLICT(app,binding,bucket) DO UPDATE SET attempts=diamond_g4_ops.rates.attempts+EXCLUDED.attempts RETURNING attempts",
        &[&database.app, &binding_name, &key, &cost],
    )?;
    let attempts: i64 = rows[0].try_get(0).map_err(|_| Error::Denied)?;
    if attempts > config.rate_per_minute {
        Err(Error::Limit("shared operations rate"))
    } else {
        Ok((config.rate_per_minute - attempts).to_string())
    }
}

/// Query bounded operational state or reauthorized global search candidates.
pub(crate) fn query(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    binding_name: &str,
    selector: &str,
    search: &str,
    limit: i64,
) -> Result<serde_json::Value> {
    let config = binding(database, binding_name)?;
    if !(1..=100).contains(&limit) || search.len() > 128 || search.chars().any(char::is_control) {
        return Err(input("invalid operations query"));
    }
    if selector == "search" {
        return search_query(database, scope, binding_name, &config, search, limit);
    }
    administrator(scope)?;
    let node_ttl = Duration::from_secs(config.node_ttl_seconds).as_secs_f64();
    let rows = match selector {
        "nodes" => database.connection.query(
            "SELECT id,kind,version,CASE WHEN state='active' AND heartbeat<clock_timestamp()-make_interval(secs=>$4) THEN 'offline' ELSE state END,capacity::text,generation::text FROM diamond_g4_ops.nodes WHERE app=$1 AND binding=$2 ORDER BY kind,id LIMIT $3",
            &[&database.app, &binding_name, &limit, &node_ttl],
        )?,
        "shards" => database.connection.query(
            "SELECT id,'bound'::text,state,generation::text,''::text,''::text FROM diamond_g4_ops.shards WHERE app=$1 AND binding=$2 ORDER BY id LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "placements" => database.connection.query(
            "SELECT repository,shard,object,generation::text,''::text,''::text FROM diamond_g4_ops.placements WHERE app=$1 AND binding=$2 ORDER BY repository LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "tasks" => database.connection.query(
            "SELECT id,kind,subject,state,attempts::text,outcome FROM diamond_g4_ops.tasks WHERE app=$1 AND binding=$2 ORDER BY created DESC LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "mirrors" => database.connection.query(
            "SELECT id,repository,target,state,expected_object,generation::text FROM diamond_g4_ops.mirrors WHERE app=$1 AND binding=$2 ORDER BY updated DESC LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "backups" => database.connection.query(
            "SELECT id,state,digest,source_generation::text,''::text,''::text FROM diamond_g4_ops.backups WHERE app=$1 AND binding=$2 ORDER BY created DESC LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "restores" => database.connection.query(
            "SELECT id,backup,state,details,''::text,''::text FROM diamond_g4_ops.restores WHERE app=$1 AND binding=$2 ORDER BY created DESC LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "gc" => database.connection.query(
            "SELECT id,removed::text,retained::text,bytes::text,''::text,''::text FROM diamond_g4_ops.gc_runs WHERE app=$1 AND binding=$2 ORDER BY created DESC LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "policies" => database.connection.query(
            "SELECT key,value,generation::text,''::text,''::text,''::text FROM diamond_g4_ops.policies WHERE app=$1 AND binding=$2 ORDER BY key LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        "audit" => database.connection.query(
            "SELECT id::text,actor,action,subject,''::text,''::text FROM diamond_g4_ops.audit WHERE app=$1 AND binding=$2 ORDER BY id DESC LIMIT $3",
            &[&database.app, &binding_name, &limit],
        )?,
        _ => return Err(Error::Denied),
    };
    rows_to_page(&rows)
}

fn rows_to_page(rows: &[tokio_postgres::Row]) -> Result<serde_json::Value> {
    let cards = rows
        .iter()
        .map(|row| {
            Ok(serde_json::json!({
                "id": row.try_get::<_, String>(0).map_err(|_| Error::Denied)?,
                "one": row.try_get::<_, String>(1).map_err(|_| Error::Denied)?,
                "two": row.try_get::<_, String>(2).map_err(|_| Error::Denied)?,
                "three": row.try_get::<_, String>(3).map_err(|_| Error::Denied)?,
                "four": row.try_get::<_, String>(4).map_err(|_| Error::Denied)?,
                "five": row.try_get::<_, String>(5).map_err(|_| Error::Denied)?,
            }))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(serde_json::json!({"cards":cards,"total":cards.len().to_string()}))
}

fn search_query(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    binding_name: &str,
    config: &Binding,
    search: &str,
    limit: i64,
) -> Result<serde_json::Value> {
    let search = search.trim();
    if search.is_empty() {
        return Ok(serde_json::json!({"cards":[],"total":"0"}));
    }
    let candidates = (limit.saturating_mul(10)).min(500);
    let rows = database.connection.query(
        "WITH q AS (SELECT plainto_tsquery('simple',$3) AS terms) SELECT document,repository,kind,title,left(body,512) FROM diamond_g4_ops.search_documents,q WHERE app=$1 AND binding=$2 AND (to_tsvector('simple',title||' '||body) @@ q.terms OR strpos(lower(title),lower($3))>0) ORDER BY (lower(title)=lower($3)) DESC,(left(lower(title),char_length($3))=lower($3)) DESC,ts_rank_cd(setweight(to_tsvector('simple',title),'A')||setweight(to_tsvector('simple',body),'B'),q.terms,32) DESC,updated DESC,document LIMIT $4",
        &[&database.app, &binding_name, &search, &candidates],
    )?;
    let mut cards = Vec::new();
    for row in rows {
        let repository: String = row.try_get(1).map_err(|_| Error::Denied)?;
        if authorize_repository(database, scope, config, &repository, false).is_err() {
            continue;
        }
        cards.push(serde_json::json!({
            "id": row.try_get::<_, String>(0).map_err(|_| Error::Denied)?,
            "repository": repository,
            "kind": row.try_get::<_, String>(2).map_err(|_| Error::Denied)?,
            "title": row.try_get::<_, String>(3).map_err(|_| Error::Denied)?,
            "body": row.try_get::<_, String>(4).map_err(|_| Error::Denied)?,
        }));
        if i64::try_from(cards.len()).map_err(|_| Error::Denied)? >= limit {
            break;
        }
    }
    Ok(serde_json::json!({"total":cards.len().to_string(),"cards":cards}))
}

#[derive(Debug)]
struct OperationsStore {
    root: PathBuf,
    app: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Marker {
    app: String,
    binding: String,
    identity: String,
}

impl OperationsStore {
    fn open(
        database: &mut PostgresStore,
        grants: &crate::RuntimeGrants,
        binding: &str,
        initialize: bool,
    ) -> Result<Self> {
        let grant = grants.operations.get(binding).ok_or(Error::Denied)?;
        private_directory(&grant.root)?;
        let marker_path = grant.root.join(MARKER);
        let marker = if initialize && !marker_path.exists() {
            let marker = Marker {
                app: database.app.clone(),
                binding: binding.to_owned(),
                identity: crate::authority::random_hex()?,
            };
            create_private_directory(&grant.root.join("replicas"))?;
            let bytes = serde_json::to_vec(&marker).map_err(|_| Error::Denied)?;
            let mut file = options().write(true).create_new(true).open(&marker_path)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            File::open(&grant.root)?.sync_all()?;
            marker
        } else {
            read_marker(&marker_path)?
        };
        if marker.app != database.app || marker.binding != binding {
            return Err(input(
                "operations store belongs to another application or binding",
            ));
        }
        let rows = database.connection.query(
            "UPDATE diamond_g4_ops.stores SET identity=COALESCE(identity,$3) WHERE app=$1 AND binding=$2 AND (identity IS NULL OR identity=$3) RETURNING identity",
            &[&database.app, &binding, &marker.identity],
        )?;
        if rows
            .first()
            .and_then(|row| row.try_get::<_, String>(0).ok())
            .as_deref()
            != Some(marker.identity.as_str())
        {
            return Err(input("operations store identity changed"));
        }
        private_directory(&grant.root.join("replicas"))?;
        Ok(Self {
            root: grant.root.clone(),
            app: database.app.clone(),
        })
    }
}

fn options() -> OpenOptions {
    let mut options = OpenOptions::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    options
}

fn private_directory(path: &Path) -> Result<()> {
    if !path.is_absolute() || path.canonicalize()? != path {
        return Err(Error::Denied);
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() {
        return Err(Error::Denied);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt as _;
        if metadata.mode() & 0o077 != 0 {
            return Err(input("operations store must have mode 0700"));
        }
    }
    Ok(())
}

fn create_private_directory(path: &Path) -> Result<()> {
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt as _;
        builder.mode(0o700);
    }
    builder.create(path)?;
    Ok(())
}

fn read_marker(path: &Path) -> Result<Marker> {
    let mut file = options().read(true).open(path)?;
    let mut bytes = Vec::new();
    std::io::Read::by_ref(&mut file)
        .take(4097)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 4096 {
        return Err(Error::Limit("operations marker"));
    }
    super::wire::strict_from_slice(&bytes).map_err(|_| Error::Denied)
}

pub(super) fn initialize(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<String> {
    let operations = project.manifest.operations.as_ref().ok_or(Error::Denied)?;
    if grants.operations.keys().collect::<BTreeSet<_>>()
        != operations.bindings.keys().collect::<BTreeSet<_>>()
    {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let mut initialized = Vec::new();
    for (binding, config) in &operations.bindings {
        OperationsStore::open(&mut database, grants, binding, true)?;
        let identity = super::git::primary_store_identity(grants, &config.git)?;
        let rows = database.connection.query(
            "INSERT INTO diamond_g4_ops.shards(app,binding,id,identity,state) VALUES($1,$2,$3,$4,'active') ON CONFLICT(app,binding,id) DO UPDATE SET identity=EXCLUDED.identity WHERE diamond_g4_ops.shards.identity=EXCLUDED.identity RETURNING id",
            &[&database.app, binding, &config.primary_shard, &identity],
        )?;
        if rows.len() != 1 {
            return database.finish_request(Err(Error::Conflict));
        }
        initialized.push(binding);
    }
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({"initialized":initialized}).to_string())
}

pub(super) fn validate(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<()> {
    let Some(operations) = &project.manifest.operations else {
        return if grants.operations.is_empty() {
            Ok(())
        } else {
            Err(Error::Denied)
        };
    };
    if grants.operations.keys().collect::<BTreeSet<_>>()
        != operations.bindings.keys().collect::<BTreeSet<_>>()
    {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    for binding in operations.bindings.keys() {
        OperationsStore::open(&mut database, grants, binding, false)?;
    }
    database.finish_request(Ok(()))
}

pub(super) fn heartbeat(
    project: &CheckedProject,
    configuration: &str,
    binding_name: &str,
    id: &str,
    kind: &str,
    version: &str,
    capacity: i32,
) -> Result<bool> {
    if !super::resolve::identifier(id)
        || !["web", "worker", "search", "pages", "registry"].contains(&kind)
        || !valid_text(version, 128)
        || !(1..=4096).contains(&capacity)
    {
        return Err(input("invalid node heartbeat"));
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    binding(&database, binding_name)?;
    let rows = database.connection.query(
        "INSERT INTO diamond_g4_ops.nodes(app,binding,id,kind,version,state,capacity) VALUES($1,$2,$3,$4,$5,'active',$6) ON CONFLICT(app,binding,id) DO UPDATE SET version=EXCLUDED.version,capacity=EXCLUDED.capacity,heartbeat=clock_timestamp(),state=CASE WHEN diamond_g4_ops.nodes.state='draining' THEN 'draining' ELSE 'active' END WHERE diamond_g4_ops.nodes.kind=EXCLUDED.kind RETURNING state",
        &[&database.app, &binding_name, &id, &kind, &version, &capacity],
    )?;
    let active = rows
        .first()
        .and_then(|row| row.try_get::<_, String>(0).ok())
        .as_deref()
        == Some("active");
    database.finish_request(Ok(()))?;
    Ok(active)
}

pub(super) fn offline(
    project: &CheckedProject,
    configuration: &str,
    binding: &str,
    id: &str,
) -> Result<()> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    database.connection.query(
        "UPDATE diamond_g4_ops.nodes SET state='offline',generation=generation+1,heartbeat=clock_timestamp() WHERE app=$1 AND binding=$2 AND id=$3",
        &[&database.app, &binding, &id],
    )?;
    database.finish_request(Ok(()))
}

#[derive(Debug)]
struct TaskClaim {
    binding: String,
    id: String,
    kind: String,
    subject: String,
    argument: String,
    fence: i64,
}

pub(super) fn work(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<String> {
    validate(project, configuration, grants)?;
    let node = format!("worker_{}", &crate::authority::random_hex()?[..16]);
    let operations = project.manifest.operations.as_ref().ok_or(Error::Denied)?;
    let binding_name = operations.bindings.keys().next().ok_or(Error::Denied)?;
    heartbeat(
        project,
        configuration,
        binding_name,
        &node,
        "worker",
        env!("CARGO_PKG_VERSION"),
        1,
    )?;
    let claim = claim_task(project, configuration)?;
    let Some(claim) = claim else {
        offline(project, configuration, binding_name, &node)?;
        return Ok("{\"worked\":false}".into());
    };
    let result = match claim.kind.as_str() {
        "search" => rebuild_search(project, configuration, &claim.binding),
        "mirror" => mirror(project, configuration, grants, &claim),
        "promote" => promote(project, configuration, grants, &claim),
        "gc" => garbage_collect(project, configuration, grants, &claim.binding),
        _ => Err(Error::Denied),
    };
    let completed = complete_task(project, configuration, &claim, &result);
    offline(project, configuration, binding_name, &node)?;
    completed?;
    result?;
    Ok(serde_json::json!({"worked":true,"task":claim.id,"kind":claim.kind}).to_string())
}

fn claim_task(project: &CheckedProject, configuration: &str) -> Result<Option<TaskClaim>> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let rows = database.connection.query(
        "WITH candidate AS (SELECT id FROM diamond_g4_ops.tasks WHERE app=$1 AND (state='pending' OR state='running' AND lease<clock_timestamp()) AND attempts<3 ORDER BY created,id FOR UPDATE SKIP LOCKED LIMIT 1) UPDATE diamond_g4_ops.tasks t SET state='running',attempts=t.attempts+1,fence=t.fence+1,lease=clock_timestamp()+interval '60 seconds' FROM candidate c WHERE t.app=$1 AND t.id=c.id RETURNING t.binding,t.id,t.kind,t.subject,t.argument,t.fence",
        &[&database.app],
    )?;
    let claim = rows
        .first()
        .map(|row| {
            Ok::<TaskClaim, Error>(TaskClaim {
                binding: row.try_get(0).map_err(|_| Error::Denied)?,
                id: row.try_get(1).map_err(|_| Error::Denied)?,
                kind: row.try_get(2).map_err(|_| Error::Denied)?,
                subject: row.try_get(3).map_err(|_| Error::Denied)?,
                argument: row.try_get(4).map_err(|_| Error::Denied)?,
                fence: row.try_get(5).map_err(|_| Error::Denied)?,
            })
        })
        .transpose()?;
    database.finish_request(Ok(()))?;
    Ok(claim)
}

fn complete_task(
    project: &CheckedProject,
    configuration: &str,
    claim: &TaskClaim,
    result: &Result<String>,
) -> Result<()> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let (state, outcome) = match result {
        Ok(value) => ("done", value.chars().take(1024).collect::<String>()),
        Err(error) => ("failed", safe_error(error)),
    };
    let rows = database.connection.query(
        "UPDATE diamond_g4_ops.tasks SET state=$6,outcome=$7,lease=NULL,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND id=$3 AND fence=$4 AND state='running' AND kind=$5 RETURNING id",
        &[&database.app, &claim.binding, &claim.id, &claim.fence, &claim.kind, &state, &outcome],
    )?;
    if rows.len() != 1 {
        return database.finish_request(Err(Error::Conflict));
    }
    database.finish_request(Ok(()))
}

fn safe_error(error: &Error) -> String {
    match error {
        Error::Limit(name) => format!("limit:{name}"),
        Error::Conflict => "conflict".into(),
        Error::Denied => "denied".into(),
        _ => "operation_failed".into(),
    }
}

fn rebuild_search(
    project: &CheckedProject,
    configuration: &str,
    binding_name: &str,
) -> Result<String> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let config = binding(&database, binding_name)?;
    let mut documents = Vec::new();
    for resource in &config.search_resources {
        let table = crate::relational::schema::table(&database.app, resource);
        let rows = database.connection.query(
            &format!("SELECT to_jsonb(t)::text FROM {table} t ORDER BY \"id\" LIMIT 10001"),
            &[],
        )?;
        if rows.len() > MAX_SEARCH_DOCUMENTS {
            return database.finish_request(Err(Error::Limit("search rebuild documents")));
        }
        for row in rows {
            let encoded: String = row.try_get(0).map_err(|_| Error::Denied)?;
            let value: serde_json::Value =
                super::wire::strict_from_slice(encoded.as_bytes()).map_err(|_| Error::Denied)?;
            let Some(id) = value.get("id").and_then(serde_json::Value::as_str) else {
                continue;
            };
            let repository = if resource == &config.resource {
                id
            } else {
                let Some(repository) = value.get("repository").and_then(serde_json::Value::as_str)
                else {
                    continue;
                };
                repository
            };
            if !crate::auth::valid_id(repository) {
                continue;
            }
            let title = ["name", "title", "tag", "slug"]
                .iter()
                .find_map(|field| value.get(field).and_then(serde_json::Value::as_str))
                .map(crate::relational::types::unhex)
                .transpose()?
                .unwrap_or_else(|| id.to_owned());
            let body = ["description", "body", "notes"]
                .iter()
                .filter_map(|field| value.get(field).and_then(serde_json::Value::as_str))
                .map(crate::relational::types::unhex)
                .collect::<Result<Vec<_>>>()?
                .join(" ");
            documents.push((
                artifact::hash(format!("{resource}:{id}").as_bytes()),
                repository.to_owned(),
                resource.clone(),
                title.chars().take(1024).collect::<String>(),
                body.chars().take(8192).collect::<String>(),
            ));
            if documents.len() > MAX_SEARCH_DOCUMENTS {
                return database.finish_request(Err(Error::Limit("search rebuild documents")));
            }
        }
    }
    database.connection.query(
        "DELETE FROM diamond_g4_ops.search_documents WHERE app=$1 AND binding=$2",
        &[&database.app, &binding_name],
    )?;
    for (document, repository, kind, title, body) in &documents {
        database.connection.query(
            "INSERT INTO diamond_g4_ops.search_documents(app,binding,document,repository,kind,title,body,generation) VALUES($1,$2,$3,$4,$5,$6,$7,1)",
            &[&database.app, &binding_name, document, repository, kind, title, body],
        )?;
    }
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({"indexed":documents.len()}).to_string())
}

fn mirror(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    claim: &TaskClaim,
) -> Result<String> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let config = binding(&database, &claim.binding)?;
    let store = OperationsStore::open(&mut database, grants, &claim.binding, false)?;
    let mirror_rows = database.connection.query(
        "SELECT repository,target,expected_object,generation FROM diamond_g4_ops.mirrors WHERE app=$1 AND binding=$2 AND id=$3 FOR UPDATE",
        &[&database.app, &claim.binding, &claim.subject],
    )?;
    let row = mirror_rows.first().ok_or(Error::Conflict)?;
    let repository: String = row.try_get(0).map_err(|_| Error::Denied)?;
    let target: String = row.try_get(1).map_err(|_| Error::Denied)?;
    let expected_object: String = row.try_get(2).map_err(|_| Error::Denied)?;
    let generation: i64 = row.try_get(3).map_err(|_| Error::Denied)?;
    if target != claim.argument {
        return database.finish_request(Err(Error::Conflict));
    }
    let grant = grants.git.get(&config.git).ok_or(Error::Denied)?;
    super::git::validate_grant(grant)?;
    let route = git_route(&mut database, grants, &config.git, &repository)?.ok_or(Error::Denied)?;
    let source = route
        .root
        .join(format!("{}-{}-{repository}.git", database.app, config.git));
    if !source.is_dir() || source.canonicalize()? != source {
        return database.finish_request(Err(Error::Denied));
    }
    let shard_rows = database.connection.query(
        "SELECT identity,state FROM diamond_g4_ops.shards WHERE app=$1 AND binding=$2 AND id=$3",
        &[&database.app, &claim.binding, &target],
    )?;
    if shard_rows.len() != 1
        || shard_rows[0]
            .try_get::<_, String>(1)
            .map_err(|_| Error::Denied)?
            != "active"
    {
        return database.finish_request(Err(Error::Conflict));
    }
    let target_identity: String = shard_rows[0].try_get(0).map_err(|_| Error::Denied)?;
    let target_root = store.root.join("replicas").join(&target);
    if !target_root.exists() {
        create_private_directory(&target_root)?;
        File::open(store.root.join("replicas"))?.sync_all()?;
    }
    private_directory(&target_root)?;
    super::git::prepare_shard_store(
        &mut database,
        &config.git,
        &grant.executable,
        &target_root,
        &target_identity,
    )?;
    let destination = target_root.join(format!("{}-{}-{repository}.git", store.app, config.git));
    if destination.exists() {
        let metadata = fs::symlink_metadata(&destination)?;
        if !metadata.is_dir() || destination.canonicalize()? != destination {
            return database.finish_request(Err(Error::Denied));
        }
        run_git(
            &grant.executable,
            &store.root,
            vec![
                "--git-dir".into(),
                destination.as_os_str().to_owned(),
                "fetch".into(),
                "--prune".into(),
                "--force".into(),
                source.as_os_str().to_owned(),
                "+refs/*:refs/*".into(),
            ],
        )?;
    } else {
        let temporary = target_root.join(format!(".part-{}", crate::authority::random_hex()?));
        run_git(
            &grant.executable,
            &store.root,
            vec![
                "clone".into(),
                "--mirror".into(),
                "--no-hardlinks".into(),
                source.as_os_str().to_owned(),
                temporary.as_os_str().to_owned(),
            ],
        )?;
        fs::rename(&temporary, &destination)?;
        File::open(&target_root)?.sync_all()?;
    }
    super::git::verify_shard_repository(
        &mut database,
        &config.git,
        &grant.executable,
        &target_root,
        &target_identity,
        &repository,
        &expected_object,
    )?;
    run_git(
        &grant.executable,
        &store.root,
        vec![
            "--git-dir".into(),
            destination.as_os_str().to_owned(),
            "fsck".into(),
            "--strict".into(),
            "--no-dangling".into(),
        ],
    )?;
    let head = run_git(
        &grant.executable,
        &store.root,
        vec![
            "--git-dir".into(),
            destination.as_os_str().to_owned(),
            "rev-parse".into(),
            "--verify".into(),
            "HEAD^{commit}".into(),
        ],
    )?;
    let object = String::from_utf8(head)
        .map_err(|_| Error::Denied)?
        .trim()
        .to_owned();
    if object != expected_object {
        return database.finish_request(Err(Error::Conflict));
    }
    database.connection.query(
        "INSERT INTO diamond_g4_ops.replicas(app,binding,repository,shard,object,verified) VALUES($1,$2,$3,$4,$5,true) ON CONFLICT(app,binding,repository,shard) DO UPDATE SET object=EXCLUDED.object,verified=true,updated=clock_timestamp()",
        &[&database.app, &claim.binding, &repository, &target, &object],
    )?;
    database.connection.query(
        "UPDATE diamond_g4_ops.mirrors SET state='verified',updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND id=$3 AND generation=$4",
        &[&database.app, &claim.binding, &claim.subject, &generation],
    )?;
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({"repository":repository,"target":target,"object":object}).to_string())
}

fn promote(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    claim: &TaskClaim,
) -> Result<String> {
    let mut fields = claim.argument.split(':');
    let target = fields.next().ok_or(Error::Denied)?;
    let expected_generation = fields
        .next()
        .ok_or(Error::Denied)?
        .parse::<i64>()
        .map_err(|_| Error::Denied)?;
    let expected_object = fields.next().ok_or(Error::Denied)?;
    if fields.next().is_some()
        || !super::resolve::identifier(target)
        || expected_generation <= 0
        || !valid_oid(expected_object)
        || !crate::auth::valid_id(&claim.subject)
    {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let config = binding(&database, &claim.binding)?;
    let binding_lock = super::git::binding_fence_key(&database.app, &config.git)?;
    database.connection.query(
        "SELECT pg_catalog.pg_advisory_xact_lock_shared(pg_catalog.hashtextextended($1,0))",
        &[&binding_lock],
    )?;
    let lock = super::git::repository_fence_key(&database.app, &config.git, &claim.subject)?;
    database.connection.query(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
        &[&lock],
    )?;
    let placement = database.connection.query(
        "SELECT shard,object,generation FROM diamond_g4_ops.placements WHERE app=$1 AND binding=$2 AND repository=$3 FOR UPDATE",
        &[&database.app, &claim.binding, &claim.subject],
    )?;
    if placement.len() != 1
        || placement[0]
            .try_get::<_, i64>(2)
            .map_err(|_| Error::Denied)?
            != expected_generation
        || placement[0]
            .try_get::<_, String>(1)
            .map_err(|_| Error::Denied)?
            != expected_object
        || placement[0]
            .try_get::<_, String>(0)
            .map_err(|_| Error::Denied)?
            == target
    {
        return database.finish_request(Err(Error::Conflict));
    }
    let replica = database.connection.query(
        "SELECT r.verified,s.identity,s.state FROM diamond_g4_ops.replicas r JOIN diamond_g4_ops.shards s ON s.app=r.app AND s.binding=r.binding AND s.id=r.shard WHERE r.app=$1 AND r.binding=$2 AND r.repository=$3 AND r.shard=$4 AND r.object=$5",
        &[&database.app, &claim.binding, &claim.subject, &target, &expected_object],
    )?;
    if replica.len() != 1
        || !replica[0]
            .try_get::<_, bool>(0)
            .map_err(|_| Error::Denied)?
        || replica[0]
            .try_get::<_, String>(2)
            .map_err(|_| Error::Denied)?
            != "active"
    {
        return database.finish_request(Err(Error::Conflict));
    }
    let identity: String = replica[0].try_get(1).map_err(|_| Error::Denied)?;
    let grant = grants.git.get(&config.git).ok_or(Error::Denied)?;
    let operations_grant = grants.operations.get(&claim.binding).ok_or(Error::Denied)?;
    let target_root = operations_grant.root.join("replicas").join(target);
    super::git::verify_shard_repository(
        &mut database,
        &config.git,
        &grant.executable,
        &target_root,
        &identity,
        &claim.subject,
        expected_object,
    )?;
    let rows = database.connection.query(
        "UPDATE diamond_g4_ops.placements SET shard=$5,generation=generation+1,updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND generation=$4 AND object=$6 RETURNING generation",
        &[&database.app, &claim.binding, &claim.subject, &expected_generation, &target, &expected_object],
    )?;
    let generation = rows
        .first()
        .ok_or(Error::Conflict)?
        .try_get::<_, i64>(0)
        .map_err(|_| Error::Denied)?;
    audit(
        &mut database,
        &claim.binding,
        "operations-worker",
        "placement_promote",
        &claim.subject,
    )?;
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({
        "repository": claim.subject,
        "shard": target,
        "object": expected_object,
        "generation": generation,
    })
    .to_string())
}

fn run_git(executable: &Path, home: &Path, arguments: Vec<OsString>) -> Result<Vec<u8>> {
    let mut command = Command::new(executable);
    command
        .args(arguments)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", home)
        .env("LANG", "C")
        .env("LC_ALL", "C")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_PROTOCOL_FROM_USER", "0")
        .env("GIT_ALLOW_PROTOCOL", "file")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn()?;
    let started = Instant::now();
    loop {
        if child.try_wait()?.is_some() {
            let output = child.wait_with_output()?;
            if !output.status.success()
                || output.stdout.len().saturating_add(output.stderr.len()) > MAX_COMMAND_OUTPUT
            {
                return Err(Error::Denied);
            }
            return Ok(output.stdout);
        }
        if started.elapsed() >= Duration::from_secs(30) {
            let _killed = child.kill();
            let _waited = child.wait();
            return Err(Error::Limit("operations Git command deadline"));
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn garbage_collect(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    binding_name: &str,
) -> Result<String> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let config = binding(&database, binding_name)?;
    let grant = grants.delivery.get(&config.delivery).ok_or(Error::Denied)?;
    let objects = grant.root.join("objects");
    private_directory(&objects)?;
    let mut referenced = BTreeSet::new();
    for sql in [
        "SELECT digest FROM diamond_g3.artifacts WHERE app=$1 AND binding=$2",
        "SELECT digest FROM diamond_g3.caches WHERE app=$1 AND binding=$2",
        "SELECT digest FROM diamond_g3.page_files WHERE app=$1 AND binding=$2",
    ] {
        for row in database
            .connection
            .query(sql, &[&database.app, &config.delivery])?
        {
            referenced.insert(row.try_get::<_, String>(0).map_err(|_| Error::Denied)?);
        }
    }
    for row in database.connection.query(
        "SELECT substring(digest from 8) FROM diamond_g3.oci_blobs WHERE app=$1 AND binding=$2",
        &[&database.app, &config.delivery],
    )? {
        referenced.insert(row.try_get::<_, String>(0).map_err(|_| Error::Denied)?);
    }
    let holds = database.connection.query(
        "SELECT digest FROM diamond_g4_ops.holds WHERE app=$1 AND binding=$2",
        &[&database.app, &binding_name],
    )?;
    for row in holds {
        referenced.insert(row.try_get::<_, String>(0).map_err(|_| Error::Denied)?);
    }
    let mut entries = fs::read_dir(&objects)?.collect::<std::io::Result<Vec<_>>>()?;
    if entries.len() > 100_000 {
        return database.finish_request(Err(Error::Limit("delivery object inventory")));
    }
    entries.sort_by_key(fs::DirEntry::file_name);
    let mut removed = 0_i32;
    let mut removed_bytes = 0_i64;
    for entry in entries {
        let name = entry.file_name();
        let Some(digest) = name.to_str() else {
            return database.finish_request(Err(Error::Denied));
        };
        if !valid_digest(digest) || !entry.file_type()?.is_file() {
            return database.finish_request(Err(Error::Denied));
        }
        if referenced.contains(digest) {
            continue;
        }
        let metadata = entry.metadata()?;
        if metadata.modified()?.elapsed().unwrap_or_default()
            < Duration::from_secs(config.gc_grace_seconds)
        {
            continue;
        }
        fs::remove_file(entry.path())?;
        removed += 1;
        removed_bytes =
            removed_bytes.saturating_add(i64::try_from(metadata.len()).unwrap_or(i64::MAX));
        if removed >= 1000 {
            break;
        }
    }
    File::open(&objects)?.sync_all()?;
    let id = crate::authority::random_hex()?;
    let retained = i32::try_from(referenced.len()).map_err(|_| Error::Denied)?;
    database.connection.query(
        "INSERT INTO diamond_g4_ops.gc_runs(app,binding,id,removed,retained,bytes) VALUES($1,$2,$3,$4,$5,$6)",
        &[&database.app, &binding_name, &id, &removed, &retained, &removed_bytes],
    )?;
    database.finish_request(Ok(()))?;
    Ok(
        serde_json::json!({"removed":removed,"retained":retained,"bytes":removed_bytes})
            .to_string(),
    )
}
