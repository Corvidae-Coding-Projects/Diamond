//! Bounded CI delivery, immutable artifacts, OCI metadata, and Pages state.

use super::{CheckedProject, Manifest, artifact, input, storage::PostgresStore};
use crate::{
    Error,
    authority::{Effect, Scope},
    error::Result,
    hir::CheckedProgram,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write as _},
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

const MARKER: &str = ".diamond-delivery-store";
const CONFIG_PATH: &str = ".diamond-ci.json";
const MAX_CONFIG_BYTES: usize = 65_536;
const MAX_LOG_BYTES: usize = 1_048_576;
const MAX_JOBS: usize = 32;

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
    executors: BTreeMap<String, ExecutorProfile>,
    #[serde(default = "artifact_limit")]
    max_artifact_bytes: u64,
    #[serde(default = "page_limit")]
    max_pages_bytes: u64,
    #[serde(default = "file_limit")]
    max_pages_files: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ExecutorProfile {
    memory_bytes: u64,
    max_processes: u64,
    max_file_bytes: u64,
}

const fn artifact_limit() -> u64 {
    64 * 1024 * 1024
}

const fn page_limit() -> u64 {
    64 * 1024 * 1024
}

const fn file_limit() -> usize {
    1000
}

impl Config {
    pub(super) fn check(&self, manifest: &Manifest, program: &CheckedProgram) -> Result<()> {
        let git = manifest.git.as_ref().ok_or(Error::Denied)?;
        if self.profile != "delivery-v1" || self.bindings.is_empty() || self.bindings.len() > 16 {
            return Err(input("delivery requires the delivery-v1 profile"));
        }
        for (name, binding) in &self.bindings {
            if !super::resolve::identifier(name)
                || !program.resources.contains_key(&binding.resource)
                || !git.bindings.contains_key(&binding.git)
                || binding.executors.is_empty()
                || binding.executors.len() > 8
                || !binding.executors.contains_key("standard")
                || binding.executors.iter().any(|(profile, limits)| {
                    !super::resolve::identifier(profile)
                        || !(64 * 1024 * 1024..=4 * 1024 * 1024 * 1024)
                            .contains(&limits.memory_bytes)
                        || !(1..=1024).contains(&limits.max_processes)
                        || !(1024 * 1024..=1024 * 1024 * 1024).contains(&limits.max_file_bytes)
                })
                || !(1..=1024 * 1024 * 1024).contains(&binding.max_artifact_bytes)
                || !(1..=1024 * 1024 * 1024).contains(&binding.max_pages_bytes)
                || !(1..=10_000).contains(&binding.max_pages_files)
                || !manifest.provides.contains(&format!("DeliveryRead<{name}>"))
                || !manifest
                    .provides
                    .contains(&format!("DbRead<{}>", binding.resource))
            {
                return Err(input(
                    "invalid delivery binding or absent Git/resource capabilities",
                ));
            }
        }
        Ok(())
    }
}

const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_g3;
CREATE TABLE IF NOT EXISTS diamond_g3.stores(app TEXT NOT NULL REFERENCES diamond_w2.apps(app), binding TEXT NOT NULL, shape TEXT NOT NULL, identity TEXT, PRIMARY KEY(app,binding));
CREATE TABLE IF NOT EXISTS diamond_g3.pipelines(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), event TEXT NOT NULL CHECK(octet_length(event) BETWEEN 1 AND 160), commit TEXT NOT NULL CHECK(octet_length(commit) IN (40,64)), reference TEXT NOT NULL CHECK(octet_length(reference) BETWEEN 1 AND 255), owner TEXT NOT NULL CHECK(octet_length(owner) BETWEEN 1 AND 160), config_digest TEXT NOT NULL CHECK(config_digest ~ '^[0-9a-f]{64}$'), state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','cancelled')), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), finished TIMESTAMPTZ, PRIMARY KEY(app,binding,repository,id), UNIQUE(app,binding,repository,event), FOREIGN KEY(app,binding) REFERENCES diamond_g3.stores(app,binding));
CREATE INDEX IF NOT EXISTS g3_pipeline_recent ON diamond_g3.pipelines(app,binding,repository,created DESC);
CREATE TABLE IF NOT EXISTS diamond_g3.jobs(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), pipeline TEXT NOT NULL CHECK(pipeline ~ '^[0-9a-f]{64}$'), name TEXT NOT NULL CHECK(octet_length(name) BETWEEN 1 AND 80), rank INTEGER NOT NULL CHECK(rank BETWEEN 0 AND 31), definition TEXT NOT NULL CHECK(octet_length(definition)<=32768), state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','cancelled')), attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8), fence BIGINT NOT NULL DEFAULT 0, lease TIMESTAMPTZ, log TEXT NOT NULL DEFAULT '' CHECK(octet_length(log)<=1048576), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), started TIMESTAMPTZ, finished TIMESTAMPTZ, PRIMARY KEY(app,binding,repository,id), UNIQUE(app,binding,repository,pipeline,name), FOREIGN KEY(app,binding,repository,pipeline) REFERENCES diamond_g3.pipelines(app,binding,repository,id));
CREATE INDEX IF NOT EXISTS g3_job_claim ON diamond_g3.jobs(app,state,created);
CREATE TABLE IF NOT EXISTS diamond_g3.artifacts(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), pipeline TEXT NOT NULL, job TEXT NOT NULL, name TEXT NOT NULL CHECK(octet_length(name) BETWEEN 1 AND 120), digest TEXT NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'), size BIGINT NOT NULL CHECK(size BETWEEN 0 AND 1073741824), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository,id), UNIQUE(app,binding,repository,pipeline,name));
CREATE TABLE IF NOT EXISTS diamond_g3.caches(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, key TEXT NOT NULL CHECK(octet_length(key) BETWEEN 1 AND 120), trust TEXT NOT NULL CHECK(octet_length(trust) BETWEEN 1 AND 255), digest TEXT NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'), size BIGINT NOT NULL CHECK(size BETWEEN 0 AND 1073741824), updated TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository,key,trust));
CREATE TABLE IF NOT EXISTS diamond_g3.environments(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, name TEXT NOT NULL CHECK(octet_length(name) BETWEEN 1 AND 80), pipeline TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','active','failed','stopped')), deployment TEXT NOT NULL DEFAULT '', updated TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository,name));
CREATE TABLE IF NOT EXISTS diamond_g3.page_deployments(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), site TEXT NOT NULL CHECK(octet_length(site) BETWEEN 1 AND 80), pipeline TEXT NOT NULL, manifest_digest TEXT NOT NULL CHECK(manifest_digest ~ '^[0-9a-f]{64}$'), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository,id));
CREATE TABLE IF NOT EXISTS diamond_g3.page_files(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, deployment TEXT NOT NULL, path TEXT NOT NULL CHECK(octet_length(path) BETWEEN 1 AND 1024), digest TEXT NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'), size BIGINT NOT NULL CHECK(size BETWEEN 0 AND 1073741824), media TEXT NOT NULL CHECK(octet_length(media) BETWEEN 1 AND 120), PRIMARY KEY(app,binding,repository,deployment,path));
CREATE TABLE IF NOT EXISTS diamond_g3.page_sites(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, site TEXT NOT NULL CHECK(octet_length(site) BETWEEN 1 AND 80), active TEXT NOT NULL CHECK(active ~ '^[0-9a-f]{64}$'), updated TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,site), UNIQUE(app,binding,repository,site));
CREATE TABLE IF NOT EXISTS diamond_g3.page_domains(app TEXT NOT NULL, binding TEXT NOT NULL, domain TEXT NOT NULL CHECK(domain=lower(domain) AND octet_length(domain) BETWEEN 4 AND 253), repository TEXT NOT NULL, site TEXT NOT NULL, updated TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,domain), FOREIGN KEY(app,binding,repository,site) REFERENCES diamond_g3.page_sites(app,binding,repository,site) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS diamond_g3.oci_blobs(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, digest TEXT NOT NULL CHECK(digest ~ '^sha256:[0-9a-f]{64}$'), size BIGINT NOT NULL CHECK(size BETWEEN 0 AND 1073741824), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository,digest));
CREATE TABLE IF NOT EXISTS diamond_g3.oci_manifests(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, name TEXT NOT NULL CHECK(octet_length(name) BETWEEN 1 AND 120), reference TEXT NOT NULL CHECK(octet_length(reference) BETWEEN 1 AND 120), digest TEXT NOT NULL CHECK(digest ~ '^sha256:[0-9a-f]{64}$'), body TEXT NOT NULL CHECK(octet_length(body)<=1048576), media TEXT NOT NULL CHECK(octet_length(media) BETWEEN 1 AND 160), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository,name,reference));";

pub(super) fn schema(store: &mut PostgresStore, config: &Config, initialize: bool) -> Result<()> {
    let shape = artifact::hash(&serde_json::to_vec(config).map_err(|_| Error::Denied)?);
    if initialize {
        store.connection.batch(SCHEMA)?;
        for binding in config.bindings.keys() {
            store.connection.query(
                "INSERT INTO diamond_g3.stores(app,binding,shape) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                &[&store.app, binding, &shape],
            )?;
        }
    }
    let rows = store.connection.query(
        "SELECT binding,shape FROM diamond_g3.stores WHERE app=$1 ORDER BY binding",
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
            "incompatible delivery metadata; stop instances and initialize the current schema",
        ));
    }
    Ok(())
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PipelineConfig {
    version: u32,
    jobs: Vec<JobDefinition>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct JobDefinition {
    name: String,
    #[serde(default = "standard_executor")]
    executor: String,
    #[serde(default)]
    needs: Vec<String>,
    script: String,
    #[serde(default = "job_timeout")]
    timeout_seconds: u64,
    #[serde(default)]
    artifact: Option<ArtifactOutput>,
    #[serde(default)]
    cache: Option<CacheOutput>,
    #[serde(default)]
    pages: Option<PagesOutput>,
    #[serde(default)]
    oci: Option<OciOutput>,
    #[serde(default)]
    environment: Option<String>,
}

const fn job_timeout() -> u64 {
    60
}

fn standard_executor() -> String {
    "standard".into()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ArtifactOutput {
    name: String,
    path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct CacheOutput {
    key: String,
    path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PagesOutput {
    site: String,
    path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct OciOutput {
    name: String,
    tag: String,
    path: String,
}

fn validate_pipeline(config: &PipelineConfig, binding: &Binding) -> Result<()> {
    if config.version != 1 || config.jobs.is_empty() || config.jobs.len() > MAX_JOBS {
        return Err(input("CI configuration requires version 1 and 1..32 jobs"));
    }
    let mut prior = BTreeSet::new();
    for job in &config.jobs {
        if !super::resolve::identifier(&job.name)
            || !prior.insert(job.name.clone())
            || !binding.executors.contains_key(&job.executor)
            || job.script.is_empty()
            || job.script.len() > 16_384
            || !(1..=300).contains(&job.timeout_seconds)
            || job.needs.len() > 16
            || job.needs.iter().any(|need| !prior.contains(need))
            || job
                .artifact
                .as_ref()
                .is_some_and(|value| !valid_label(&value.name) || !valid_relative(&value.path))
            || job
                .cache
                .as_ref()
                .is_some_and(|value| !valid_label(&value.key) || !valid_relative(&value.path))
            || job.pages.as_ref().is_some_and(|value| {
                !super::resolve::identifier(&value.site) || !valid_relative(&value.path)
            })
            || job.oci.as_ref().is_some_and(|value| {
                !valid_label(&value.name) || !valid_tag(&value.tag) || !valid_relative(&value.path)
            })
            || job
                .environment
                .as_ref()
                .is_some_and(|value| !super::resolve::identifier(value))
        {
            return Err(input("invalid bounded CI job definition"));
        }
    }
    Ok(())
}

fn valid_label(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 120
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b'/'))
}

fn valid_domain(value: &str) -> bool {
    (4..=253).contains(&value.len())
        && value.contains('.')
        && value == value.to_ascii_lowercase()
        && value.split('.').all(|label| {
            !label.is_empty()
                && label.len() <= 63
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        })
}

fn valid_relative(value: &str) -> bool {
    if value.is_empty()
        || value.len() > 1024
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'.' | b'_' | b'-'))
    {
        return false;
    }
    let path = Path::new(value);
    !path.is_absolute()
        && path.components().all(|part| {
            matches!(part, Component::Normal(_))
                && part.as_os_str().to_str().is_some_and(|name| name != ".")
        })
}

fn valid_tag(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 120
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn valid_oid(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn binding(database: &PostgresStore, name: &str) -> Result<Binding> {
    database
        .delivery
        .as_ref()
        .and_then(|config| config.bindings.get(name))
        .cloned()
        .ok_or(Error::Denied)
}

fn authorize(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    name: &str,
    repository: &str,
    write: bool,
) -> Result<Binding> {
    let binding = binding(database, name)?;
    let mut effects = BTreeSet::from([Effect::Read(binding.resource.clone())]);
    if write {
        effects.insert(Effect::Write(binding.resource.clone()));
    }
    database.attachment(
        &Scope::new(scope.principal(), &effects),
        &binding.resource,
        repository,
        write,
    )?;
    Ok(binding)
}

#[derive(Clone, Copy)]
pub(crate) struct ScheduleRequest<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) commit: &'a str,
    pub(crate) reference: &'a str,
    pub(crate) event: &'a str,
}

pub(crate) fn schedule(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: ScheduleRequest<'_>,
) -> Result<String> {
    let ScheduleRequest {
        binding: name,
        repository,
        commit,
        reference,
        event,
    } = request;
    let config = authorize(database, scope, name, repository, true)?;
    if !valid_oid(commit)
        || !reference.starts_with("refs/")
        || reference.len() > 255
        || event.is_empty()
        || event.len() > 160
    {
        return Err(input("invalid pipeline source identity"));
    }
    let value = super::git::call(
        database,
        scope,
        grants,
        super::git::Query {
            binding: &config.git,
            repository,
            operation: "blob",
            revision: commit,
            path: CONFIG_PATH,
            limit: 1,
            write: false,
        },
    )?;
    let source = value
        .get("text")
        .and_then(serde_json::Value::as_str)
        .ok_or(Error::Denied)?;
    if source.len() > MAX_CONFIG_BYTES {
        return Err(Error::Limit("CI configuration"));
    }
    let pipeline: PipelineConfig = super::wire::strict_from_slice(source.as_bytes())
        .map_err(|_| input("invalid .diamond-ci.json"))?;
    validate_pipeline(&pipeline, &config)?;
    let encoded = serde_json::to_string(&pipeline).map_err(|_| Error::Denied)?;
    let config_digest = artifact::hash(encoded.as_bytes());
    let owner = scope.principal().id().ok_or(Error::Unauthenticated)?;
    let existing = database.connection.query(
        "SELECT id,commit,reference,config_digest FROM diamond_g3.pipelines WHERE app=$1 AND binding=$2 AND repository=$3 AND event=$4",
        &[&database.app, &name, &repository, &event],
    )?;
    if let Some(row) = existing.first() {
        if row.try_get::<_, String>(1).map_err(|_| Error::Denied)? != commit
            || row.try_get::<_, String>(2).map_err(|_| Error::Denied)? != reference
            || row.try_get::<_, String>(3).map_err(|_| Error::Denied)? != config_digest
        {
            return Err(Error::Conflict);
        }
        return row.try_get(0).map_err(|_| Error::Denied);
    }
    let id = crate::authority::random_hex()?;
    database.connection.query(
        "INSERT INTO diamond_g3.pipelines(app,binding,repository,id,event,commit,reference,owner,config_digest,state) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'pending')",
        &[&database.app, &name, &repository, &id, &event, &commit, &reference, &owner, &config_digest],
    )?;
    for (rank, job) in pipeline.jobs.iter().enumerate() {
        let job_id = crate::authority::random_hex()?;
        let definition = serde_json::to_string(job).map_err(|_| Error::Denied)?;
        let rank = i32::try_from(rank).map_err(|_| Error::Denied)?;
        database.connection.query(
            "INSERT INTO diamond_g3.jobs(app,binding,repository,id,pipeline,name,rank,definition,state) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'pending')",
            &[&database.app, &name, &repository, &job_id, &id, &job.name, &rank, &definition],
        )?;
    }
    Ok(id)
}

pub(crate) fn cancel(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    name: &str,
    repository: &str,
    pipeline: &str,
) -> Result<String> {
    authorize(database, scope, name, repository, true)?;
    let rows = database.connection.query(
        "UPDATE diamond_g3.pipelines SET state='cancelled',finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND state IN ('pending','running') RETURNING id",
        &[&database.app, &name, &repository, &pipeline],
    )?;
    if rows.is_empty() {
        return Err(Error::Conflict);
    }
    database.connection.query(
        "UPDATE diamond_g3.jobs SET state='cancelled',fence=fence+1,lease=NULL,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND pipeline=$4 AND state IN ('pending','running')",
        &[&database.app, &name, &repository, &pipeline],
    )?;
    Ok("cancelled".into())
}

pub(crate) fn command(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    name: &str,
    repository: &str,
    action: &str,
    subject: &str,
    value: &str,
) -> Result<String> {
    authorize(database, scope, name, repository, true)?;
    let rows = match action {
        "artifact_delete" if value.is_empty() && crate::auth::valid_id(subject) => database
            .connection
            .query(
                "DELETE FROM diamond_g3.artifacts WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 RETURNING id",
                &[&database.app, &name, &repository, &subject],
            )?,
        "environment_stop" if value.is_empty() && super::resolve::identifier(subject) => database
            .connection
            .query(
                "UPDATE diamond_g3.environments SET state='stopped',updated=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND name=$4 AND state='active' RETURNING name",
                &[&database.app, &name, &repository, &subject],
            )?,
        "pages_activate"
            if super::resolve::identifier(subject) && crate::auth::valid_id(value) => database
                .connection
                .query(
                    "UPDATE diamond_g3.page_sites s SET active=$5,updated=clock_timestamp() WHERE s.app=$1 AND s.binding=$2 AND s.repository=$3 AND s.site=$4 AND EXISTS(SELECT 1 FROM diamond_g3.page_deployments d WHERE d.app=s.app AND d.binding=s.binding AND d.repository=s.repository AND d.site=s.site AND d.id=$5) RETURNING s.active",
                    &[&database.app, &name, &repository, &subject, &value],
                )?,
        "domain_set"
            if scope.principal().has_role("account_admin")
                && valid_domain(subject)
                && super::resolve::identifier(value) => database.connection.query(
                    "INSERT INTO diamond_g3.page_domains(app,binding,domain,repository,site) SELECT $1,$2,$4,$3,$5 WHERE EXISTS(SELECT 1 FROM diamond_g3.page_sites WHERE app=$1 AND binding=$2 AND repository=$3 AND site=$5) ON CONFLICT(app,binding,domain) DO UPDATE SET repository=EXCLUDED.repository,site=EXCLUDED.site,updated=clock_timestamp() WHERE diamond_g3.page_domains.repository=EXCLUDED.repository RETURNING domain",
                    &[&database.app, &name, &repository, &subject, &value],
                )?,
        "domain_remove"
            if scope.principal().has_role("account_admin")
                && value.is_empty()
                && valid_domain(subject) => database.connection.query(
                    "DELETE FROM diamond_g3.page_domains WHERE app=$1 AND binding=$2 AND repository=$3 AND domain=$4 RETURNING domain",
                    &[&database.app, &name, &repository, &subject],
                )?,
        _ => return Err(Error::Denied),
    };
    rows.first()
        .ok_or(Error::Conflict)?
        .try_get(0)
        .map_err(|_| Error::Denied)
}

pub(crate) fn query(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    name: &str,
    repository: &str,
    selector: &str,
    limit: i64,
) -> Result<serde_json::Value> {
    authorize(database, scope, name, repository, false)?;
    let (kind, selected) = selector.split_once(':').unwrap_or((selector, ""));
    if !(1..=50).contains(&limit)
        || ![
            "pipelines",
            "jobs",
            "artifacts",
            "environments",
            "images",
            "pages",
            "domains",
        ]
        .contains(&kind)
        || (!selected.is_empty() && !crate::auth::valid_id(selected))
        || matches!(kind, "jobs" | "artifacts") != !selected.is_empty()
    {
        return Err(input("invalid delivery query"));
    }
    let pipelines = database.connection.query(
        "SELECT id,commit,reference,state,created::text FROM diamond_g3.pipelines WHERE app=$1 AND binding=$2 AND repository=$3 AND ($4='' OR id=$4) ORDER BY created DESC,id LIMIT $5",
        &[&database.app, &name, &repository, &selected, &limit],
    )?;
    let pipeline_cards = pipelines
        .iter()
        .map(|row| {
            Ok(serde_json::json!({
                "id":row.try_get::<_,String>(0).map_err(|_|Error::Denied)?,
                "commit":row.try_get::<_,String>(1).map_err(|_|Error::Denied)?,
                "reference":row.try_get::<_,String>(2).map_err(|_|Error::Denied)?,
                "state":row.try_get::<_,String>(3).map_err(|_|Error::Denied)?,
                "created":row.try_get::<_,String>(4).map_err(|_|Error::Denied)?,
            }))
        })
        .collect::<Result<Vec<_>>>()?;
    let jobs = database.connection.query(
        "SELECT id,name,state,attempts::text,log FROM diamond_g3.jobs WHERE app=$1 AND binding=$2 AND repository=$3 AND pipeline=$4 ORDER BY rank",
        &[&database.app, &name, &repository, &selected],
    )?;
    let artifacts = database.connection.query(
        "SELECT id,name,digest,size::text FROM diamond_g3.artifacts WHERE app=$1 AND binding=$2 AND repository=$3 AND ($4='' OR pipeline=$4) ORDER BY created DESC LIMIT $5",
        &[&database.app, &name, &repository, &selected, &limit],
    )?;
    let environments = database.connection.query(
        "SELECT name,state,deployment,pipeline FROM diamond_g3.environments WHERE app=$1 AND binding=$2 AND repository=$3 ORDER BY name LIMIT $4",
        &[&database.app, &name, &repository, &limit],
    )?;
    let images = database.connection.query(
        "SELECT name,reference,digest FROM diamond_g3.oci_manifests WHERE app=$1 AND binding=$2 AND repository=$3 ORDER BY created DESC LIMIT $4",
        &[&database.app, &name, &repository, &limit],
    )?;
    let pages = database.connection.query(
        "SELECT site,active FROM diamond_g3.page_sites WHERE app=$1 AND binding=$2 AND repository=$3 ORDER BY site LIMIT $4",
        &[&database.app, &name, &repository, &limit],
    )?;
    let domains = database.connection.query(
        "SELECT domain,site FROM diamond_g3.page_domains WHERE app=$1 AND binding=$2 AND repository=$3 ORDER BY domain LIMIT $4",
        &[&database.app, &name, &repository, &limit],
    )?;
    let dashboard = serde_json::json!({
        "pipelines":pipeline_cards,
        "jobs":rows_to_cards(&jobs, &["id","name","state","attempts","log"])?,
        "artifacts":rows_to_cards(&artifacts, &["id","name","digest","size"])?,
        "environments":rows_to_cards(&environments, &["name","state","deployment","pipeline"])?,
        "images":rows_to_cards(&images, &["name","tag","digest"])?,
        "pages":rows_to_cards(&pages, &["site","deployment"])?,
        "domains":rows_to_cards(&domains, &["domain","site"])?,
    });
    dashboard
        .get(kind)
        .cloned()
        .map(|cards| serde_json::json!({"cards":cards}))
        .ok_or(Error::Denied)
}

fn rows_to_cards(rows: &[tokio_postgres::Row], fields: &[&str]) -> Result<Vec<serde_json::Value>> {
    rows.iter()
        .map(|row| {
            let mut value = serde_json::Map::new();
            for (index, field) in fields.iter().enumerate() {
                let text = row.try_get::<_, String>(index).map_err(|_| Error::Denied)?;
                value.insert((*field).into(), serde_json::Value::String(text));
            }
            Ok(serde_json::Value::Object(value))
        })
        .collect()
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Marker {
    app: String,
    binding: String,
    identity: String,
}

#[derive(Clone, Debug)]
struct Store {
    root: PathBuf,
    sandbox: PathBuf,
    limit: PathBuf,
    app: String,
}

impl Store {
    fn open(
        database: &mut PostgresStore,
        grants: &crate::RuntimeGrants,
        binding: &str,
        initialize: bool,
    ) -> Result<Self> {
        let grant = grants.delivery.get(binding).ok_or(Error::Denied)?;
        private_directory(&grant.root)?;
        executable(&grant.sandbox)?;
        executable(&grant.limit)?;
        let marker_path = grant.root.join(MARKER);
        let marker = if initialize && !marker_path.exists() {
            let marker = Marker {
                app: database.app.clone(),
                binding: binding.into(),
                identity: crate::authority::random_hex()?,
            };
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt as _;
                options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
            }
            let mut output = options.open(&marker_path)?;
            output.write_all(&serde_json::to_vec(&marker).map_err(|_| Error::Denied)?)?;
            output.sync_all()?;
            for child in ["objects", "jobs"] {
                let path = grant.root.join(child);
                let mut builder = fs::DirBuilder::new();
                #[cfg(unix)]
                {
                    use std::os::unix::fs::DirBuilderExt as _;
                    builder.mode(0o700);
                }
                builder.create(&path)?;
            }
            File::open(&grant.root)?.sync_all()?;
            marker
        } else {
            read_marker(&marker_path)?
        };
        if marker.app != database.app || marker.binding != binding {
            return Err(input(
                "delivery store belongs to another application or binding",
            ));
        }
        let rows = database.connection.query(
            "UPDATE diamond_g3.stores SET identity=COALESCE(identity,$3) WHERE app=$1 AND binding=$2 AND (identity IS NULL OR identity=$3) RETURNING identity",
            &[&database.app, &binding, &marker.identity],
        )?;
        if rows
            .first()
            .and_then(|row| row.try_get::<_, String>(0).ok())
            .as_deref()
            != Some(marker.identity.as_str())
        {
            return Err(input("delivery store identity changed"));
        }
        private_directory(&grant.root.join("objects"))?;
        private_directory(&grant.root.join("jobs"))?;
        Ok(Self {
            root: grant.root.clone(),
            sandbox: grant.sandbox.clone(),
            limit: grant.limit.clone(),
            app: database.app.clone(),
        })
    }

    fn object_path(&self, digest: &str) -> Result<PathBuf> {
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(Error::Denied);
        }
        Ok(self.root.join("objects").join(digest))
    }
}

fn read_marker(path: &Path) -> Result<Marker> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(path)?;
    if !file.metadata()?.is_file() {
        return Err(Error::Denied);
    }
    let mut bytes = Vec::new();
    file.take(4097).read_to_end(&mut bytes)?;
    if bytes.len() > 4096 {
        return Err(Error::Limit("delivery marker"));
    }
    super::wire::strict_from_slice(&bytes).map_err(|_| Error::Denied)
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
            return Err(input("delivery directories require mode 0700"));
        }
    }
    Ok(())
}

fn executable(path: &Path) -> Result<()> {
    if !path.is_absolute() || path.canonicalize()? != path {
        return Err(Error::Denied);
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() {
        return Err(Error::Denied);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt as _;
        if metadata.mode() & 0o022 != 0 || metadata.mode() & 0o111 == 0 {
            return Err(Error::Denied);
        }
    }
    Ok(())
}

pub(super) fn validate(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<()> {
    let Some(config) = &project.manifest.delivery else {
        return if grants.delivery.is_empty() {
            Ok(())
        } else {
            Err(Error::Denied)
        };
    };
    if grants.delivery.keys().collect::<BTreeSet<_>>()
        != config.bindings.keys().collect::<BTreeSet<_>>()
    {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    for binding in config.bindings.keys() {
        Store::open(&mut database, grants, binding, false)?;
    }
    database.finish_request(Ok(()))
}

pub(super) fn initialize(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<String> {
    let config = project.manifest.delivery.as_ref().ok_or(Error::Denied)?;
    if grants.delivery.keys().collect::<BTreeSet<_>>()
        != config.bindings.keys().collect::<BTreeSet<_>>()
    {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    for binding in config.bindings.keys() {
        Store::open(&mut database, grants, binding, true)?;
    }
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({"initialized":config.bindings.len()}).to_string())
}

#[derive(Debug)]
struct Claim {
    binding: String,
    repository: String,
    id: String,
    pipeline: String,
    commit: String,
    reference: String,
    definition: JobDefinition,
    fence: i64,
}

#[derive(Debug)]
struct StagedFile {
    digest: String,
    size: u64,
}

#[derive(Debug)]
struct StagedPage {
    path: String,
    digest: String,
    size: u64,
    media: String,
}

#[derive(Debug, Default)]
struct StagedOutputs {
    artifact: Option<(String, StagedFile)>,
    cache: Option<(String, StagedFile)>,
    pages: Option<(String, Vec<StagedPage>, String)>,
    oci: Option<(String, String, StagedFile, String, String)>,
    environment: Option<String>,
}

#[derive(Debug)]
struct Workspace(PathBuf);

impl Drop for Workspace {
    fn drop(&mut self) {
        let _removed = fs::remove_dir_all(&self.0);
    }
}

fn claim(project: &CheckedProject, configuration: &str) -> Result<Option<Claim>> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    database.connection.query(
        "UPDATE diamond_g3.jobs SET state='pending',fence=fence+1,lease=NULL WHERE app=$1 AND state='running' AND lease<clock_timestamp() AND attempts<3",
        &[&database.app],
    )?;
    database.connection.query(
        "UPDATE diamond_g3.jobs SET state='failed',fence=fence+1,lease=NULL,log='runner lease expired',finished=clock_timestamp() WHERE app=$1 AND state='running' AND lease<clock_timestamp() AND attempts>=3",
        &[&database.app],
    )?;
    database.connection.query(
        "UPDATE diamond_g3.pipelines p SET state='failed',finished=clock_timestamp() WHERE p.app=$1 AND p.state IN ('pending','running') AND EXISTS(SELECT 1 FROM diamond_g3.jobs j WHERE j.app=p.app AND j.binding=p.binding AND j.repository=p.repository AND j.pipeline=p.id AND j.state='failed')",
        &[&database.app],
    )?;
    database.connection.query(
        "UPDATE diamond_g3.jobs j SET state='cancelled',fence=fence+1,finished=clock_timestamp() FROM diamond_g3.pipelines p WHERE j.app=$1 AND p.app=j.app AND p.binding=j.binding AND p.repository=j.repository AND p.id=j.pipeline AND p.state='failed' AND j.state='pending'",
        &[&database.app],
    )?;
    let candidates = database.connection.query(
        "SELECT j.binding,j.repository,j.id,j.pipeline,p.commit,p.reference,j.definition,j.fence FROM diamond_g3.jobs j JOIN diamond_g3.pipelines p ON p.app=j.app AND p.binding=j.binding AND p.repository=j.repository AND p.id=j.pipeline WHERE j.app=$1 AND j.state='pending' AND p.state IN ('pending','running') ORDER BY p.created,j.rank FOR UPDATE OF j SKIP LOCKED LIMIT 32",
        &[&database.app],
    )?;
    let mut selected = None;
    for row in candidates {
        let binding: String = row.try_get(0).map_err(|_| Error::Denied)?;
        let repository: String = row.try_get(1).map_err(|_| Error::Denied)?;
        let id: String = row.try_get(2).map_err(|_| Error::Denied)?;
        let pipeline: String = row.try_get(3).map_err(|_| Error::Denied)?;
        let definition: String = row.try_get(6).map_err(|_| Error::Denied)?;
        let definition: JobDefinition =
            super::wire::strict_from_slice(definition.as_bytes()).map_err(|_| Error::Denied)?;
        let states = database.connection.query(
            "SELECT name,state FROM diamond_g3.jobs WHERE app=$1 AND binding=$2 AND repository=$3 AND pipeline=$4",
            &[&database.app, &binding, &repository, &pipeline],
        )?;
        let states = states
            .iter()
            .map(|row| {
                Ok((
                    row.try_get::<_, String>(0).map_err(|_| Error::Denied)?,
                    row.try_get::<_, String>(1).map_err(|_| Error::Denied)?,
                ))
            })
            .collect::<Result<BTreeMap<_, _>>>()?;
        if definition.needs.iter().any(|name| {
            states
                .get(name)
                .is_some_and(|state| matches!(state.as_str(), "failed" | "cancelled"))
        }) {
            database.connection.query(
                "UPDATE diamond_g3.jobs SET state='failed',log='dependency did not succeed',finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND state='pending'",
                &[&database.app, &binding, &repository, &id],
            )?;
            database.connection.query(
                "UPDATE diamond_g3.pipelines SET state='failed',finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND state IN ('pending','running')",
                &[&database.app, &binding, &repository, &pipeline],
            )?;
            database.connection.query(
                "UPDATE diamond_g3.jobs SET state='cancelled',fence=fence+1,lease=NULL,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND pipeline=$4 AND state='pending'",
                &[&database.app, &binding, &repository, &pipeline],
            )?;
            continue;
        }
        if definition
            .needs
            .iter()
            .all(|name| states.get(name).is_some_and(|state| state == "succeeded"))
        {
            let rows = database.connection.query(
                "UPDATE diamond_g3.jobs SET state='running',attempts=attempts+1,fence=fence+1,lease=clock_timestamp()+($5::bigint*INTERVAL '1 second'),started=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND state='pending' RETURNING fence",
                &[&database.app, &binding, &repository, &id, &i64::try_from(definition.timeout_seconds + 30).map_err(|_|Error::Denied)?],
            )?;
            let Some(claimed) = rows.first() else {
                continue;
            };
            database.connection.query(
                "UPDATE diamond_g3.pipelines SET state='running' WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND state='pending'",
                &[&database.app, &binding, &repository, &pipeline],
            )?;
            selected = Some(Claim {
                binding,
                repository,
                id,
                pipeline,
                commit: row.try_get(4).map_err(|_| Error::Denied)?,
                reference: row.try_get(5).map_err(|_| Error::Denied)?,
                definition,
                fence: claimed.try_get(0).map_err(|_| Error::Denied)?,
            });
            break;
        }
    }
    database.finish_request(Ok(selected))
}

fn create_workspace(
    store: &Store,
    git_root: &Path,
    git_executable: &Path,
    git_binding: &str,
    claim: &Claim,
) -> Result<Workspace> {
    let source = git_root.join(format!(
        "{}-{}-{}.git",
        store.app, git_binding, claim.repository
    ));
    if source.canonicalize()? != source {
        return Err(Error::Denied);
    }
    let systemd =
        store.sandbox.file_name().and_then(std::ffi::OsStr::to_str) == Some("systemd-run");
    let workspace = if systemd {
        let temporary = PathBuf::from("/tmp");
        if temporary.canonicalize()? != temporary {
            return Err(Error::Denied);
        }
        temporary.join(format!("diamond-delivery-{}-{}", claim.id, claim.fence))
    } else {
        store
            .root
            .join("jobs")
            .join(format!("{}-{}", claim.id, claim.fence))
    };
    let status = Command::new(git_executable)
        .env_clear()
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .args(["clone", "--no-checkout", "--no-hardlinks", "--"])
        .arg(&source)
        .arg(&workspace)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    if !status.success() || workspace.canonicalize()? != workspace {
        let _removed = fs::remove_dir_all(&workspace);
        return Err(input("CI source checkout failed"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        fs::set_permissions(&workspace, fs::Permissions::from_mode(0o700))?;
    }
    let checkout = Command::new(git_executable)
        .env_clear()
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .args(["-C"])
        .arg(&workspace)
        .args(["checkout", "--detach"])
        .arg(&claim.commit)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    if !checkout.success() {
        let _removed = fs::remove_dir_all(&workspace);
        return Err(input("CI commit checkout failed"));
    }
    fs::remove_dir_all(workspace.join(".git"))?;
    Ok(Workspace(workspace))
}

fn restore_cache(
    database: &mut PostgresStore,
    store: &Store,
    claim: &Claim,
    workspace: &Path,
) -> Result<()> {
    let Some(cache) = &claim.definition.cache else {
        return Ok(());
    };
    let rows = database.connection.query(
        "SELECT digest,size FROM diamond_g3.caches WHERE app=$1 AND binding=$2 AND repository=$3 AND key=$4 AND trust=$5",
        &[&database.app, &claim.binding, &claim.repository, &cache.key, &claim.reference],
    )?;
    let Some(row) = rows.first() else {
        return Ok(());
    };
    let digest: String = row.try_get(0).map_err(|_| Error::Denied)?;
    let size: i64 = row.try_get(1).map_err(|_| Error::Denied)?;
    let source = store.object_path(&digest)?;
    let target = private_workspace_target(workspace, &cache.path)?;
    copy_bounded(
        &source,
        &target,
        u64::try_from(size).map_err(|_| Error::Denied)?,
    )?;
    Ok(())
}

fn private_workspace_target(workspace: &Path, relative: &str) -> Result<PathBuf> {
    let root = workspace.canonicalize()?;
    let relative = Path::new(relative);
    let mut current = root.clone();
    let parent = relative.parent().ok_or(Error::Denied)?;
    for component in parent.components() {
        let Component::Normal(name) = component else {
            return Err(Error::Denied);
        };
        current.push(name);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.is_dir() && current.canonicalize()?.starts_with(&root) => {}
            Ok(_) => return Err(Error::Denied),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let mut builder = fs::DirBuilder::new();
                #[cfg(unix)]
                {
                    use std::os::unix::fs::DirBuilderExt as _;
                    builder.mode(0o700);
                }
                builder.create(&current)?;
            }
            Err(error) => return Err(error.into()),
        }
    }
    let target = root.join(relative);
    if fs::symlink_metadata(&target).is_ok() {
        return Err(Error::Conflict);
    }
    Ok(target)
}

fn bwrap_command(
    store: &Store,
    workspace: &Path,
    job: &JobDefinition,
    profile: &ExecutorProfile,
) -> Command {
    let mut command = Command::new(&store.limit);
    command
        .env_clear()
        .arg(format!("--as={}", profile.memory_bytes))
        .arg(format!("--cpu={}", job.timeout_seconds))
        .arg(format!("--nproc={}", profile.max_processes))
        .arg(format!("--fsize={}", profile.max_file_bytes))
        .arg("--")
        .arg(&store.sandbox)
        .args([
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--bind",
        ])
        .arg(workspace)
        .args([
            "/workspace",
            "--chdir",
            "/workspace",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "CI",
            "true",
            "--",
            "/bin/sh",
            "-eu",
            "-c",
        ])
        .arg(&job.script);
    command
}

fn systemd_command(
    store: &Store,
    workspace: &Path,
    job: &JobDefinition,
    profile: &ExecutorProfile,
) -> Result<Command> {
    for name in ["usr", "bin", "lib", "lib64", "tmp"] {
        let target = workspace.join(name);
        if fs::symlink_metadata(&target).is_ok() {
            return Err(input("CI checkout conflicts with reserved sandbox path"));
        }
        let mut builder = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt as _;
            builder.mode(0o700);
        }
        builder.create(&target)?;
    }
    let mut command = Command::new(&store.sandbox);
    command.env_clear();
    for name in ["DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"] {
        let value = std::env::var_os(name)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| input("systemd sandbox requires the user service bus"))?;
        command.env(name, value);
    }
    command
        .args(["--user", "--pipe", "--wait", "--collect"])
        .args(["-p", "PrivateNetwork=yes"])
        .arg("-p")
        .arg(format!("RootDirectory={}", workspace.display()))
        .args([
            "-p",
            "BindReadOnlyPaths=/usr",
            "-p",
            "BindReadOnlyPaths=/bin",
        ])
        .args([
            "-p",
            "BindReadOnlyPaths=/lib",
            "-p",
            "BindReadOnlyPaths=/lib64",
        ])
        .args(["-p", "WorkingDirectory=/", "-p", "NoNewPrivileges=yes"])
        .args(["-p", "RestrictSUIDSGID=yes", "-p", "LockPersonality=yes"])
        .args([
            "-p",
            "MemoryDenyWriteExecute=yes",
            "-p",
            "PrivateDevices=yes",
        ])
        .arg("-p")
        .arg(format!("MemoryMax={}", profile.memory_bytes))
        .arg("-p")
        .arg(format!("TasksMax={}", profile.max_processes))
        .arg("-p")
        .arg(format!("LimitFSIZE={}", profile.max_file_bytes))
        .arg("-p")
        .arg(format!("RuntimeMaxSec={}s", job.timeout_seconds))
        .args(["-p", "UMask=0077", "-E", "PATH=/usr/bin:/bin"])
        .args(["-E", "HOME=/tmp", "-E", "CI=true"])
        .args(["/bin/sh", "-eu", "-c"])
        .arg(&job.script);
    Ok(command)
}

fn collect_output<R: Read + Send + 'static>(
    stream: R,
    sender: mpsc::Sender<std::io::Result<Vec<u8>>>,
) {
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stream
            .take(u64::try_from(MAX_LOG_BYTES + 1).unwrap_or(u64::MAX))
            .read_to_end(&mut bytes)
            .map(|_| bytes);
        let _sent = sender.send(result);
    });
}

fn run_sandbox(
    store: &Store,
    workspace: &Path,
    job: &JobDefinition,
    profile: &ExecutorProfile,
) -> Result<String> {
    let backend = store
        .sandbox
        .file_name()
        .and_then(std::ffi::OsStr::to_str)
        .ok_or(Error::Denied)?;
    let mut command = match backend {
        "bwrap" => bwrap_command(store, workspace, job, profile),
        "systemd-run" => systemd_command(store, workspace, job, profile)?,
        _ => return Err(input("unsupported CI sandbox executable")),
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn()?;
    let stdout = child.stdout.take().ok_or(Error::Denied)?;
    let stderr = child.stderr.take().ok_or(Error::Denied)?;
    let (sender, receiver) = mpsc::channel();
    collect_output(stdout, sender.clone());
    collect_output(stderr, sender.clone());
    drop(sender);
    let deadline = Instant::now()
        + Duration::from_secs(job.timeout_seconds + if backend == "systemd-run" { 5 } else { 0 });
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _killed = child.kill();
            let _waited = child.wait();
            return Err(Error::Limit("CI job deadline"));
        }
        thread::sleep(Duration::from_millis(20));
    };
    let mut log = Vec::new();
    for value in receiver {
        let value = value?;
        if log.len().saturating_add(value.len()) > MAX_LOG_BYTES {
            return Err(Error::Limit("CI job log"));
        }
        log.extend_from_slice(&value);
    }
    let log = String::from_utf8_lossy(&log).into_owned();
    if !status.success() {
        return Err(Error::Input(format!("CI job failed: {}", safe_log(&log))));
    }
    Ok(safe_log(&log))
}

fn safe_log(value: &str) -> String {
    value
        .chars()
        .filter(|character| !character.is_control() || matches!(character, '\n' | '\r' | '\t'))
        .take(MAX_LOG_BYTES)
        .collect()
}

fn copy_bounded(source: &Path, target: &Path, maximum: u64) -> Result<u64> {
    let mut input = OpenOptions::new().read(true).open(source)?;
    if !input.metadata()?.is_file() {
        return Err(Error::Denied);
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut output = options.open(target)?;
    let mut buffer = vec![0_u8; 65_536].into_boxed_slice();
    let mut total = 0_u64;
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        total = total
            .checked_add(u64::try_from(read).map_err(|_| Error::Denied)?)
            .ok_or(Error::Limit("delivery object"))?;
        if total > maximum {
            return Err(Error::Limit("delivery object"));
        }
        output.write_all(&buffer[..read])?;
    }
    output.sync_all()?;
    Ok(total)
}

fn stage_file(store: &Store, workspace: &Path, source: &Path, maximum: u64) -> Result<StagedFile> {
    if !fs::symlink_metadata(source)?.is_file() {
        return Err(Error::Denied);
    }
    let root = workspace.canonicalize()?;
    let canonical = source.canonicalize()?;
    if !canonical.is_file() || !canonical.starts_with(&root) {
        return Err(Error::Denied);
    }
    let mut input = OpenOptions::new().read(true).open(&canonical)?;
    let mut digest = Sha256::new();
    let mut bytes = vec![0_u8; 65_536].into_boxed_slice();
    let mut size = 0_u64;
    loop {
        let read = input.read(&mut bytes)?;
        if read == 0 {
            break;
        }
        size = size
            .checked_add(u64::try_from(read).map_err(|_| Error::Denied)?)
            .ok_or(Error::Limit("delivery object"))?;
        if size > maximum {
            return Err(Error::Limit("delivery object"));
        }
        digest.update(&bytes[..read]);
    }
    let digest = hex(&digest.finalize());
    let final_path = store.object_path(&digest)?;
    if final_path.exists() {
        if final_path.metadata()?.len() != size {
            return Err(Error::Conflict);
        }
    } else {
        let temporary = store.root.join("objects").join(format!(
            ".{}-{}",
            digest,
            crate::authority::random_hex()?
        ));
        copy_bounded(&canonical, &temporary, maximum)?;
        fs::rename(&temporary, &final_path)?;
        File::open(final_path.parent().ok_or(Error::Denied)?)?.sync_all()?;
    }
    Ok(StagedFile { digest, size })
}

fn stage_pages(
    store: &Store,
    workspace: &Path,
    root: &Path,
    maximum_bytes: u64,
    maximum_files: usize,
) -> Result<Vec<StagedPage>> {
    let workspace = workspace.canonicalize()?;
    if !fs::symlink_metadata(root)?.is_dir() {
        return Err(Error::Denied);
    }
    let root = root.canonicalize()?;
    if !root.is_dir() || !root.starts_with(&workspace) {
        return Err(Error::Denied);
    }
    let mut pending = vec![root.clone()];
    let mut files = Vec::new();
    let mut total = 0_u64;
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            let metadata = fs::symlink_metadata(entry.path())?;
            if metadata.file_type().is_symlink() {
                return Err(input("Pages output cannot contain symlinks"));
            }
            if metadata.is_dir() {
                pending.push(entry.path());
                continue;
            }
            if !metadata.is_file() || files.len() >= maximum_files {
                return Err(Error::Limit("Pages files"));
            }
            total = total
                .checked_add(metadata.len())
                .ok_or(Error::Limit("Pages bytes"))?;
            if total > maximum_bytes {
                return Err(Error::Limit("Pages bytes"));
            }
            let relative = entry
                .path()
                .strip_prefix(&root)
                .map_err(|_| Error::Denied)?
                .to_str()
                .ok_or(Error::Denied)?
                .replace('\\', "/");
            if !valid_relative(&relative) {
                return Err(Error::Denied);
            }
            let staged = stage_file(store, &workspace, &entry.path(), maximum_bytes)?;
            files.push(StagedPage {
                path: relative.clone(),
                digest: staged.digest,
                size: staged.size,
                media: media_type(&relative).into(),
            });
        }
    }
    files.sort_by(|left, right| left.path.cmp(&right.path));
    if files.is_empty() || !files.iter().any(|file| file.path == "index.html") {
        return Err(input("Pages output requires index.html"));
    }
    Ok(files)
}

fn media_type(path: &str) -> &'static str {
    match Path::new(path).extension().and_then(|value| value.to_str()) {
        Some("html") => "text/html; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("js") => "text/javascript; charset=utf-8",
        Some("json") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("webp") => "image/webp",
        Some("txt") => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

fn stage_outputs(
    store: &Store,
    binding: &Binding,
    claim: &Claim,
    workspace: &Path,
) -> Result<StagedOutputs> {
    let mut outputs = StagedOutputs {
        environment: claim.definition.environment.clone(),
        ..StagedOutputs::default()
    };
    if let Some(value) = &claim.definition.artifact {
        outputs.artifact = Some((
            value.name.clone(),
            stage_file(
                store,
                workspace,
                &workspace.join(&value.path),
                binding.max_artifact_bytes,
            )?,
        ));
    }
    if let Some(value) = &claim.definition.cache {
        outputs.cache = Some((
            value.key.clone(),
            stage_file(
                store,
                workspace,
                &workspace.join(&value.path),
                binding.max_artifact_bytes,
            )?,
        ));
    }
    if let Some(value) = &claim.definition.pages {
        let files = stage_pages(
            store,
            workspace,
            &workspace.join(&value.path),
            binding.max_pages_bytes,
            binding.max_pages_files,
        )?;
        let manifest = serde_json::to_vec(
            &files
                .iter()
                .map(|file| (&file.path, &file.digest, file.size, &file.media))
                .collect::<Vec<_>>(),
        )
        .map_err(|_| Error::Denied)?;
        outputs.pages = Some((value.site.clone(), files, sha256(&manifest)));
    }
    if let Some(value) = &claim.definition.oci {
        let layer = stage_file(
            store,
            workspace,
            &workspace.join(&value.path),
            binding.max_artifact_bytes,
        )?;
        let config_bytes = b"{}";
        let config_path = store.root.join("objects").join(sha256(config_bytes));
        if !config_path.exists() {
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt as _;
                options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
            }
            let mut file = options.open(&config_path)?;
            file.write_all(config_bytes)?;
            file.sync_all()?;
        }
        let config_digest = format!("sha256:{}", sha256(config_bytes));
        let manifest = serde_json::json!({
            "schemaVersion":2,
            "mediaType":"application/vnd.oci.image.manifest.v1+json",
            "config":{"mediaType":"application/vnd.oci.empty.v1+json","digest":config_digest,"size":config_bytes.len()},
            "layers":[{"mediaType":"application/vnd.oci.image.layer.v1.tar","digest":format!("sha256:{}",layer.digest),"size":layer.size}]
        })
        .to_string();
        outputs.oci = Some((
            value.name.clone(),
            value.tag.clone(),
            layer,
            config_digest,
            manifest,
        ));
    }
    Ok(outputs)
}

fn publish_outputs(
    database: &mut PostgresStore,
    claim: &Claim,
    outputs: &StagedOutputs,
    log: &str,
) -> Result<()> {
    let current = database.connection.query(
        "SELECT id FROM diamond_g3.jobs WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND pipeline=$5 AND fence=$6 AND state='running' AND lease>clock_timestamp() FOR UPDATE",
        &[&database.app, &claim.binding, &claim.repository, &claim.id, &claim.pipeline, &claim.fence],
    )?;
    if current.is_empty() {
        return Err(Error::Conflict);
    }
    if let Some((name, file)) = &outputs.artifact {
        let id = crate::authority::random_hex()?;
        let size = i64::try_from(file.size).map_err(|_| Error::Denied)?;
        database.connection.query(
            "INSERT INTO diamond_g3.artifacts(app,binding,repository,id,pipeline,job,name,digest,size) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            &[&database.app, &claim.binding, &claim.repository, &id, &claim.pipeline, &claim.id, name, &file.digest, &size],
        )?;
    }
    if let Some((key, file)) = &outputs.cache {
        let size = i64::try_from(file.size).map_err(|_| Error::Denied)?;
        database.connection.query(
            "INSERT INTO diamond_g3.caches(app,binding,repository,key,trust,digest,size) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(app,binding,repository,key,trust) DO UPDATE SET digest=EXCLUDED.digest,size=EXCLUDED.size,updated=clock_timestamp()",
            &[&database.app, &claim.binding, &claim.repository, key, &claim.reference, &file.digest, &size],
        )?;
    }
    let mut deployment = String::new();
    if let Some((site, files, manifest_digest)) = &outputs.pages {
        deployment = crate::authority::random_hex()?;
        database.connection.query(
            "INSERT INTO diamond_g3.page_deployments(app,binding,repository,id,site,pipeline,manifest_digest) VALUES($1,$2,$3,$4,$5,$6,$7)",
            &[&database.app, &claim.binding, &claim.repository, &deployment, site, &claim.pipeline, manifest_digest],
        )?;
        for file in files {
            let size = i64::try_from(file.size).map_err(|_| Error::Denied)?;
            database.connection.query(
                "INSERT INTO diamond_g3.page_files(app,binding,repository,deployment,path,digest,size,media) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                &[&database.app, &claim.binding, &claim.repository, &deployment, &file.path, &file.digest, &size, &file.media],
            )?;
        }
        let site_rows = database.connection.query(
            "INSERT INTO diamond_g3.page_sites(app,binding,repository,site,active) VALUES($1,$2,$3,$4,$5) ON CONFLICT(app,binding,site) DO UPDATE SET repository=EXCLUDED.repository,active=EXCLUDED.active,updated=clock_timestamp() WHERE diamond_g3.page_sites.repository=EXCLUDED.repository RETURNING repository",
            &[&database.app, &claim.binding, &claim.repository, site, &deployment],
        )?;
        if site_rows.len() != 1 {
            return Err(Error::Conflict);
        }
    }
    if let Some((name, tag, layer, config_digest, manifest)) = &outputs.oci {
        let layer_digest = format!("sha256:{}", layer.digest);
        let size = i64::try_from(layer.size).map_err(|_| Error::Denied)?;
        database.connection.query(
            "INSERT INTO diamond_g3.oci_blobs(app,binding,repository,digest,size) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
            &[&database.app, &claim.binding, &claim.repository, &layer_digest, &size],
        )?;
        database.connection.query(
            "INSERT INTO diamond_g3.oci_blobs(app,binding,repository,digest,size) VALUES($1,$2,$3,$4,2) ON CONFLICT DO NOTHING",
            &[&database.app, &claim.binding, &claim.repository, config_digest],
        )?;
        let manifest_digest = format!("sha256:{}", sha256(manifest.as_bytes()));
        database.connection.query(
            "INSERT INTO diamond_g3.oci_manifests(app,binding,repository,name,reference,digest,body,media) VALUES($1,$2,$3,$4,$5,$6,$7,'application/vnd.oci.image.manifest.v1+json') ON CONFLICT(app,binding,repository,name,reference) DO UPDATE SET digest=EXCLUDED.digest,body=EXCLUDED.body,media=EXCLUDED.media,created=clock_timestamp()",
            &[&database.app, &claim.binding, &claim.repository, name, tag, &manifest_digest, manifest],
        )?;
    }
    if let Some(environment) = &outputs.environment {
        database.connection.query(
            "INSERT INTO diamond_g3.environments(app,binding,repository,name,pipeline,state,deployment) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(app,binding,repository,name) DO UPDATE SET pipeline=EXCLUDED.pipeline,state=EXCLUDED.state,deployment=EXCLUDED.deployment,updated=clock_timestamp()",
            &[&database.app, &claim.binding, &claim.repository, environment, &claim.pipeline, &"active", &deployment],
        )?;
    }
    database.connection.query(
        "UPDATE diamond_g3.jobs SET state='succeeded',lease=NULL,log=$7,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND pipeline=$5 AND fence=$6",
        &[&database.app, &claim.binding, &claim.repository, &claim.id, &claim.pipeline, &claim.fence, &log],
    )?;
    finish_pipeline(database, claim)
}

fn finish_pipeline(database: &mut PostgresStore, claim: &Claim) -> Result<()> {
    let states = database.connection.query(
        "SELECT state FROM diamond_g3.jobs WHERE app=$1 AND binding=$2 AND repository=$3 AND pipeline=$4",
        &[&database.app, &claim.binding, &claim.repository, &claim.pipeline],
    )?;
    let values = states
        .iter()
        .map(|row| row.try_get::<_, String>(0).map_err(|_| Error::Denied))
        .collect::<Result<Vec<_>>>()?;
    let terminal = if values.iter().any(|state| state == "failed") {
        Some("failed")
    } else if values.iter().all(|state| state == "succeeded") {
        Some("succeeded")
    } else {
        None
    };
    if let Some(state) = terminal {
        database.connection.query(
            "UPDATE diamond_g3.pipelines SET state=$5,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND state IN ('pending','running')",
            &[&database.app, &claim.binding, &claim.repository, &claim.pipeline, &state],
        )?;
        if state == "failed" {
            database.connection.query(
                "UPDATE diamond_g3.jobs SET state='cancelled',fence=fence+1,lease=NULL,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND pipeline=$4 AND state='pending'",
                &[&database.app, &claim.binding, &claim.repository, &claim.pipeline],
            )?;
        }
        if state == "succeeded" {
            database.connection.query(
                "UPDATE diamond_git.gates SET pipeline_succeeded=true WHERE app=$1 AND repository=$2 AND reference=$3 AND object=$4",
                &[&database.app, &claim.repository, &claim.reference, &claim.commit],
            )?;
        }
    }
    Ok(())
}

fn fail_claim(
    project: &CheckedProject,
    configuration: &str,
    claim: &Claim,
    error: &Error,
) -> Result<()> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let log = safe_log(&error.to_string());
    database.connection.query(
        "UPDATE diamond_g3.jobs SET state='failed',lease=NULL,log=$7,finished=clock_timestamp() WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND pipeline=$5 AND fence=$6 AND state='running'",
        &[&database.app, &claim.binding, &claim.repository, &claim.id, &claim.pipeline, &claim.fence, &log],
    )?;
    finish_pipeline(&mut database, claim)?;
    database.finish_request(Ok(()))
}

fn execute_claim(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    claim: &Claim,
) -> Result<()> {
    let delivery = project
        .manifest
        .delivery
        .as_ref()
        .and_then(|config| config.bindings.get(&claim.binding))
        .cloned()
        .ok_or(Error::Denied)?;
    let git = grants.git.get(&delivery.git).ok_or(Error::Denied)?;
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let store = Store::open(&mut database, grants, &claim.binding, false)?;
    let workspace = create_workspace(&store, &git.root, &git.executable, &delivery.git, claim)?;
    restore_cache(&mut database, &store, claim, &workspace.0)?;
    database.finish_request(Ok(()))?;
    let profile = delivery
        .executors
        .get(&claim.definition.executor)
        .ok_or(Error::Denied)?;
    let log = run_sandbox(&store, &workspace.0, &claim.definition, profile)?;
    let outputs = stage_outputs(&store, &delivery, claim, &workspace.0)?;
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    publish_outputs(&mut database, claim, &outputs, &log)?;
    database.finish_request(Ok(()))
}

pub(super) fn work(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    once: bool,
) -> Result<String> {
    validate(project, configuration, grants)?;
    let mut completed = 0_u64;
    loop {
        let Some(job) = claim(project, configuration)? else {
            if once {
                break;
            }
            thread::sleep(Duration::from_millis(250));
            continue;
        };
        match execute_claim(project, configuration, grants, &job) {
            Ok(()) => completed = completed.saturating_add(1),
            Err(error) => fail_claim(project, configuration, &job, &error)?,
        }
        if once {
            break;
        }
    }
    Ok(serde_json::json!({"completed":completed}).to_string())
}

fn sha256(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write as _;
    let mut value = String::with_capacity(bytes.len().saturating_mul(2));
    for byte in bytes {
        let _written = write!(value, "{byte:02x}");
    }
    value
}

#[derive(Clone, Copy)]
pub(super) enum FetchKind<'a> {
    Artifact(&'a str),
    Blob(&'a str),
    Manifest { name: &'a str, reference: &'a str },
    Tags(&'a str),
}

pub(super) struct Fetch {
    pub(super) body: FetchBody,
    pub(super) size: u64,
    pub(super) media: String,
    pub(super) digest: Option<String>,
}

pub(super) enum FetchBody {
    Bytes(Vec<u8>),
    File(File),
}

pub(super) fn git_binding(project: &CheckedProject, binding: &str) -> Result<String> {
    project
        .manifest
        .delivery
        .as_ref()
        .and_then(|config| config.bindings.get(binding))
        .map(|value| value.git.clone())
        .ok_or(Error::Denied)
}

pub(super) fn fetch(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    binding: &str,
    repository: &str,
    kind: FetchKind<'_>,
) -> Result<Fetch> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let store = Store::open(&mut database, grants, binding, false)?;
    let fetched = match kind {
        FetchKind::Artifact(id) => {
            if !crate::auth::valid_id(id) {
                return Err(Error::Denied);
            }
            let rows = database.connection.query(
                "SELECT digest,size FROM diamond_g3.artifacts WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4",
                &[&database.app, &binding, &repository, &id],
            )?;
            let row = rows.first().ok_or(Error::Denied)?;
            let digest: String = row.try_get(0).map_err(|_| Error::Denied)?;
            let size: i64 = row.try_get(1).map_err(|_| Error::Denied)?;
            let (file, size) = open_object(&store, &digest, size)?;
            Fetch {
                body: FetchBody::File(file),
                size,
                media: "application/octet-stream".into(),
                digest: Some(format!("sha256:{digest}")),
            }
        }
        FetchKind::Blob(digest) => {
            let digest = digest.strip_prefix("sha256:").ok_or(Error::Denied)?;
            let rows = database.connection.query(
                "SELECT size FROM diamond_g3.oci_blobs WHERE app=$1 AND binding=$2 AND repository=$3 AND digest=$4",
                &[&database.app, &binding, &repository, &format!("sha256:{digest}")],
            )?;
            let size: i64 = rows
                .first()
                .ok_or(Error::Denied)?
                .try_get(0)
                .map_err(|_| Error::Denied)?;
            let (file, size) = open_object(&store, digest, size)?;
            Fetch {
                body: FetchBody::File(file),
                size,
                media: "application/octet-stream".into(),
                digest: Some(format!("sha256:{digest}")),
            }
        }
        FetchKind::Manifest { name, reference } => {
            let digest_reference = reference
                .strip_prefix("sha256:")
                .is_some_and(|digest| valid_oid(digest) && digest.len() == 64);
            if !valid_label(name) || (!valid_tag(reference) && !digest_reference) {
                return Err(Error::Denied);
            }
            let rows = database.connection.query(
                "SELECT digest,body,media FROM diamond_g3.oci_manifests WHERE app=$1 AND binding=$2 AND repository=$3 AND name=$4 AND (reference=$5 OR digest=$5) ORDER BY created DESC LIMIT 1",
                &[&database.app, &binding, &repository, &name, &reference],
            )?;
            let row = rows.first().ok_or(Error::Denied)?;
            let bytes = row
                .try_get::<_, String>(1)
                .map_err(|_| Error::Denied)?
                .into_bytes();
            Fetch {
                digest: Some(row.try_get(0).map_err(|_| Error::Denied)?),
                size: u64::try_from(bytes.len()).map_err(|_| Error::Denied)?,
                body: FetchBody::Bytes(bytes),
                media: row.try_get(2).map_err(|_| Error::Denied)?,
            }
        }
        FetchKind::Tags(name) => {
            if !valid_label(name) {
                return Err(Error::Denied);
            }
            let rows = database.connection.query(
                "SELECT reference FROM diamond_g3.oci_manifests WHERE app=$1 AND binding=$2 AND repository=$3 AND name=$4 ORDER BY reference LIMIT 1000",
                &[&database.app, &binding, &repository, &name],
            )?;
            let tags = rows
                .iter()
                .map(|row| row.try_get::<_, String>(0).map_err(|_| Error::Denied))
                .collect::<Result<Vec<_>>>()?;
            let bytes = serde_json::to_vec(&serde_json::json!({"name":name,"tags":tags}))
                .map_err(|_| Error::Denied)?;
            Fetch {
                size: u64::try_from(bytes.len()).map_err(|_| Error::Denied)?,
                body: FetchBody::Bytes(bytes),
                media: "application/json".into(),
                digest: None,
            }
        }
    };
    database.finish_request(Ok(()))?;
    Ok(fetched)
}

fn open_object(store: &Store, digest: &str, size: i64) -> Result<(File, u64)> {
    let expected = u64::try_from(size).map_err(|_| Error::Denied)?;
    let path = store.object_path(digest)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() != expected {
        return Err(Error::Denied);
    }
    Ok((file, expected))
}

#[derive(Clone)]
struct PagesState {
    project: CheckedProject,
    configuration: String,
    grant: crate::integrations::DeliveryGrant,
    binding: String,
    permits: std::sync::Arc<tokio::sync::Semaphore>,
}

pub(super) fn serve_pages(
    project: &CheckedProject,
    address: std::net::SocketAddr,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    binding: &str,
) -> Result<()> {
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(input("serve Pages outside an async runtime"));
    }
    validate(project, configuration, grants)?;
    if project
        .manifest
        .delivery
        .as_ref()
        .is_none_or(|config| !config.bindings.contains_key(binding))
    {
        return Err(Error::Denied);
    }
    let grant = grants.delivery.get(binding).cloned().ok_or(Error::Denied)?;
    let state = std::sync::Arc::new(PagesState {
        project: project.clone(),
        configuration: configuration.into(),
        grant,
        binding: binding.into(),
        permits: std::sync::Arc::new(tokio::sync::Semaphore::new(32)),
    });
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(4)
        .enable_all()
        .build()?;
    let result = runtime.block_on(async move {
        let listener = tokio::net::TcpListener::bind(address).await?;
        let app = axum::Router::new()
            .fallback(pages_request)
            .with_state(state);
        axum::serve(listener, app).await.map_err(Error::Io)
    });
    drop(runtime);
    result
}

async fn pages_request(
    axum::extract::State(state): axum::extract::State<std::sync::Arc<PagesState>>,
    request: axum::extract::Request,
) -> axum::response::Response {
    use axum::http::{StatusCode, header};
    let head = request.method() == "HEAD";
    let allowed = request.method() == "GET" || head;
    let no_body = !request.headers().contains_key(header::TRANSFER_ENCODING)
        && request
            .headers()
            .get(header::CONTENT_LENGTH)
            .and_then(|value| value.to_str().ok())
            .is_none_or(|value| value == "0");
    let cookie_free = !request.headers().contains_key(header::COOKIE)
        && !request.headers().contains_key(header::AUTHORIZATION);
    let path = request.uri().path().to_owned();
    let host = pages_domain_host(&request);
    if !allowed || !no_body || !cookie_free || request.uri().query().is_some() {
        return pages_text_response(StatusCode::BAD_REQUEST, b"bad request", head);
    }
    let Ok(permit) = std::sync::Arc::clone(&state.permits).try_acquire_owned() else {
        return pages_text_response(StatusCode::SERVICE_UNAVAILABLE, b"unavailable", head);
    };
    let state_for_work = std::sync::Arc::clone(&state);
    let fetched =
        tokio::task::spawn_blocking(move || page(&state_for_work, &path, host.as_deref())).await;
    match fetched {
        Ok(Ok(fetch)) => pages_response(StatusCode::OK, fetch, head, permit),
        Ok(Err(Error::Denied | Error::Input(_))) => {
            pages_text_response(StatusCode::NOT_FOUND, b"not found", head)
        }
        _ => pages_text_response(StatusCode::SERVICE_UNAVAILABLE, b"unavailable", head),
    }
}

fn pages_domain_host(request: &axum::extract::Request) -> Option<String> {
    let mut values = request.headers().get_all(axum::http::header::HOST).iter();
    let mut value = values.next()?.to_str().ok()?.to_ascii_lowercase();
    if values.next().is_some() {
        return None;
    }
    if let Some((name, port)) = value.rsplit_once(':')
        && !name.contains(':')
        && !port.is_empty()
        && port.bytes().all(|byte| byte.is_ascii_digit())
    {
        value.truncate(name.len());
    }
    valid_domain(&value).then_some(value)
}

fn page(state: &PagesState, path: &str, host: Option<&str>) -> Result<Fetch> {
    let mut database = PostgresStore::open_control(&state.project, &state.configuration, None)?;
    let value = path.strip_prefix('/').ok_or(Error::Denied)?;
    let domain = if let Some(host) = host {
        database.connection.query(
            "SELECT site FROM diamond_g3.page_domains WHERE app=$1 AND binding=$2 AND domain=$3",
            &[&database.app, &state.binding, &host],
        )?
    } else {
        Vec::new()
    };
    let mapped_site = domain
        .first()
        .map(|row| row.try_get::<_, String>(0).map_err(|_| Error::Denied))
        .transpose()?;
    let (site_name, path) = if let Some(site) = mapped_site.as_deref() {
        (site, value)
    } else {
        value.split_once('/').unwrap_or((value, ""))
    };
    if !super::resolve::identifier(site_name) {
        return Err(Error::Denied);
    }
    let path = if path.is_empty() {
        "index.html".to_owned()
    } else if path.ends_with('/') {
        format!("{path}index.html")
    } else {
        path.to_owned()
    };
    if !valid_relative(&path) {
        return Err(Error::Denied);
    }
    let mut grants = crate::RuntimeGrants::default();
    grants
        .delivery
        .insert(state.binding.clone(), state.grant.clone());
    let store = Store::open(&mut database, &grants, &state.binding, false)?;
    let rows = database.connection.query(
        "SELECT f.digest,f.size,f.media FROM diamond_g3.page_sites s JOIN diamond_g3.page_files f ON f.app=s.app AND f.binding=s.binding AND f.repository=s.repository AND f.deployment=s.active WHERE s.app=$1 AND s.binding=$2 AND s.site=$3 AND f.path=$4",
        &[&database.app, &state.binding, &site_name, &path],
    )?;
    let row = rows.first().ok_or(Error::Denied)?;
    let digest: String = row.try_get(0).map_err(|_| Error::Denied)?;
    let byte_count: i64 = row.try_get(1).map_err(|_| Error::Denied)?;
    let media: String = row.try_get(2).map_err(|_| Error::Denied)?;
    let (file, byte_count) = open_object(&store, &digest, byte_count)?;
    database.finish_request(Ok(()))?;
    Ok(Fetch {
        body: FetchBody::File(file),
        size: byte_count,
        media,
        digest: None,
    })
}

fn pages_response(
    status: axum::http::StatusCode,
    fetch: Fetch,
    head: bool,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> axum::response::Response {
    use axum::{body::Body, http::header};
    use futures_util::StreamExt as _;
    let body = if head {
        drop(permit);
        Body::empty()
    } else {
        match fetch.body {
            FetchBody::Bytes(bytes) => {
                drop(permit);
                Body::from(bytes)
            }
            FetchBody::File(file) => {
                let stream = tokio_util::io::ReaderStream::with_capacity(
                    tokio::fs::File::from_std(file),
                    65_536,
                );
                let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
                let stream = futures_util::stream::unfold(
                    (stream, deadline, permit),
                    |(mut stream, deadline, permit)| async move {
                        match tokio::time::timeout_at(deadline, stream.next()).await {
                            Ok(Some(chunk)) => Some((chunk, (stream, deadline, permit))),
                            Ok(None) => None,
                            Err(_) => Some((
                                Err(std::io::Error::new(
                                    std::io::ErrorKind::TimedOut,
                                    "Pages download deadline",
                                )),
                                (stream, deadline, permit),
                            )),
                        }
                    },
                );
                Body::from_stream(stream)
            }
        }
    };
    axum::response::Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, fetch.media)
        .header(header::CONTENT_LENGTH, fetch.size.to_string())
        .header(header::CACHE_CONTROL, "public, max-age=60")
        .header("x-content-type-options", "nosniff")
        .header("referrer-policy", "no-referrer")
        .header("permissions-policy", "camera=(), microphone=(), geolocation=()")
        .header("cross-origin-resource-policy", "same-site")
        .header(
            "content-security-policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        .body(body)
        .unwrap_or_else(|_| axum::response::Response::new(Body::empty()))
}

fn pages_text_response(
    status: axum::http::StatusCode,
    bytes: &'static [u8],
    head: bool,
) -> axum::response::Response {
    use axum::{body::Body, http::header};
    axum::response::Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain")
        .header(header::CONTENT_LENGTH, bytes.len().to_string())
        .header(header::CACHE_CONTROL, "no-store")
        .header("x-content-type-options", "nosniff")
        .header("referrer-policy", "no-referrer")
        .header(
            "permissions-policy",
            "camera=(), microphone=(), geolocation=()",
        )
        .header(
            "content-security-policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        .body(if head {
            Body::empty()
        } else {
            Body::from(bytes)
        })
        .unwrap_or_else(|_| axum::response::Response::new(Body::empty()))
}
