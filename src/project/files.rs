//! Private shared-filesystem attachments with policy-controlled publication.
mod operations;
mod transport;
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
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub(super) use transport::request;
pub(super) const MAX_BYTES: u64 = 8 * 1024 * 1024;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    profile: String,
    pub bindings: BTreeMap<String, Binding>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub resource: String,
    pub max_bytes: u64,
}
impl Config {
    pub(super) fn check(&self, manifest: &Manifest, program: &CheckedProgram) -> Result<()> {
        if self.profile != "shared-fs-v1"
            || manifest.identity.is_none()
            || program.storage.is_none()
            || self.bindings.is_empty()
            || self.bindings.len() > 16
        {
            return Err(input(
                "files require shared-fs-v1, accounts and relational storage",
            ));
        }
        for (name, binding) in &self.bindings {
            if !super::resolve::identifier(name)
                || !program.resources.contains_key(&binding.resource)
                || !(1..=MAX_BYTES).contains(&binding.max_bytes)
                || !manifest
                    .provides
                    .contains(&format!("DbRead<{}>", binding.resource))
                || !manifest
                    .provides
                    .contains(&format!("DbWrite<{}>", binding.resource))
            {
                return Err(input(
                    "invalid file binding or absent resource capabilities",
                ));
            }
        }
        Ok(())
    }
}
const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_w6;
CREATE TABLE IF NOT EXISTS diamond_w6.stores(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app), shape TEXT NOT NULL, identity TEXT);
CREATE TABLE IF NOT EXISTS diamond_w6.objects(app TEXT NOT NULL REFERENCES diamond_w6.stores(app), id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), binding TEXT NOT NULL, parent TEXT NOT NULL, owner TEXT NOT NULL, name TEXT NOT NULL, media TEXT NOT NULL, length BIGINT NOT NULL CHECK(length BETWEEN 0 AND 8388608), state TEXT NOT NULL CHECK(state IN ('reserved','receiving','staged','ready','deleting','deleted','failed')), token_hash TEXT NOT NULL, sha256 TEXT NOT NULL DEFAULT '', expires TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()+INTERVAL '15 minutes', PRIMARY KEY(app,id));
CREATE INDEX IF NOT EXISTS objects_expiry ON diamond_w6.objects(app,expires) WHERE state NOT IN ('ready','deleted','failed');";
pub(super) fn schema(store: &mut PostgresStore, config: &Config, initialize: bool) -> Result<()> {
    let shape = artifact::hash(&serde_json::to_vec(config).map_err(|_| Error::Denied)?);
    if initialize {
        store.connection.batch(SCHEMA)?;
        store.connection.query(
            "INSERT INTO diamond_w6.stores(app,shape) VALUES($1,$2) ON CONFLICT DO NOTHING",
            &[&store.app, &shape],
        )?;
    }
    let rows = store.connection.query(
        "SELECT shape FROM diamond_w6.stores WHERE app=$1",
        &[&store.app],
    )?;
    if rows.len() != 1 || rows[0].try_get::<_, String>(0).map_err(|_| Error::Denied)? != shape {
        return Err(input(
            "incompatible shared file metadata; stop servers and initialize a current schema",
        ));
    }
    Ok(())
}
#[derive(Debug)]
pub(super) struct Store {
    directory: PathBuf,
    identity: String,
    app: String,
}
impl Store {
    pub(super) fn open(
        project: &CheckedProject,
        configuration: &str,
        grants: &crate::RuntimeGrants,
        initialize: bool,
    ) -> Result<Option<Self>> {
        if project.manifest.files.is_none() {
            if grants.objects.is_some() {
                return Err(Error::Denied);
            }
            return Ok(None);
        }
        let directory = grants.objects.as_ref().ok_or(Error::Denied)?.clone();
        private_directory(&directory)?;
        let marker = directory.join(".diamond-shared-store");
        if initialize && !marker.exists() {
            let mut file = options().write(true).create_new(true).open(&marker)?;
            file.write_all(crate::authority::random_hex()?.as_bytes())?;
            file.sync_all()?;
            File::open(&directory)?.sync_all()?;
        }
        let identity = store_identity(&marker)?;
        let mut db = PostgresStore::open(project, configuration, false)?;
        if initialize {
            db.connection.query(
                "UPDATE diamond_w6.stores SET identity=$2 WHERE app=$1 AND identity IS NULL",
                &[&db.app, &identity],
            )?;
        }
        let rows = db.connection.query(
            "SELECT identity FROM diamond_w6.stores WHERE app=$1",
            &[&db.app],
        )?;
        if rows.len() != 1
            || rows[0]
                .try_get::<_, Option<String>>(0)
                .map_err(|_| Error::Denied)?
                .as_deref()
                != Some(&identity)
        {
            return Err(input("shared object store identity mismatch"));
        }
        let app = db.app.clone();
        db.finish_request(Ok(()))?;
        Ok(Some(Self {
            directory,
            identity,
            app,
        }))
    }
    fn verify_root(&self) -> Result<()> {
        private_directory(&self.directory)?;
        let identity = store_identity(&self.directory.join(".diamond-shared-store"))?;
        if identity != self.identity {
            return Err(input("shared object store identity changed"));
        }
        Ok(())
    }
    fn path(&self, id: &str, staged: bool) -> Result<PathBuf> {
        self.verify_root()?;
        if !identifier(id) {
            return Err(Error::Denied);
        }
        Ok(self.directory.join(format!(
            "{}-{id}.{}",
            self.app,
            if staged { "part" } else { "blob" }
        )))
    }
    fn remove(&self, id: &str) -> Result<()> {
        for staged in [true, false] {
            match fs::remove_file(self.path(id, staged)?) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
        }
        File::open(&self.directory)?.sync_all()?;
        Ok(())
    }
}
fn store_identity(marker: &Path) -> Result<String> {
    let mut file = options().read(true).open(marker)?;
    let mut identity = String::new();
    Read::by_ref(&mut file)
        .take(65)
        .read_to_string(&mut identity)?;
    if !identifier(&identity) {
        return Err(Error::Denied);
    }
    Ok(identity)
}
fn private_directory(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() || path.canonicalize()? != path {
        return Err(input("shared store must be a canonical private directory"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o077 != 0 {
            return Err(input("shared store directory must have mode 0700"));
        }
    }
    Ok(())
}
fn options() -> OpenOptions {
    let mut options = OpenOptions::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    options
}
fn identifier(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn grants(binding: &Binding, write: bool) -> BTreeSet<Effect> {
    let mut grants = BTreeSet::from([Effect::Read(binding.resource.clone())]);
    if write {
        grants.insert(Effect::Write(binding.resource.clone()));
    }
    grants
}
pub(super) fn maintain(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    initialize: bool,
) -> Result<String> {
    let store = Store::open(project, configuration, grants, initialize)?.ok_or(Error::Denied)?;
    if initialize {
        return Ok("{\"initialized\":true}".into());
    }
    let mut db = PostgresStore::open_control(project, configuration, None)?;
    let rows = db.connection.query("SELECT id FROM diamond_w6.objects WHERE app=$1 AND (state IN ('deleting','deleted','failed') OR (state IN ('reserved','receiving','staged') AND expires < clock_timestamp())) ORDER BY expires LIMIT 100 FOR UPDATE SKIP LOCKED", &[&db.app])?;
    for row in &rows {
        let id: String = row.try_get(0).map_err(|_| Error::Denied)?;
        store.remove(&id)?;
        db.connection.query(
            "DELETE FROM diamond_w6.objects WHERE app=$1 AND id=$2",
            &[&db.app, &id],
        )?;
    }
    db.finish_request(Ok(()))?;
    Ok(serde_json::json!({"removed":rows.len(),"maximum_batch":100}).to_string())
}

pub(super) fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .flat_map(|b| {
            const HEX: &[u8; 16] = b"0123456789abcdef";
            [
                char::from(HEX[usize::from(b >> 4)]),
                char::from(HEX[usize::from(b & 15)]),
            ]
        })
        .collect()
}

pub(super) fn cleaner(
    server: std::sync::Arc<super::connected::Server>,
) -> (
    tokio::sync::oneshot::Sender<()>,
    tokio::task::JoinHandle<()>,
) {
    let (stop, mut stopped) = tokio::sync::oneshot::channel();
    let job = tokio::spawn(async move {
        if server.objects.is_none() {
            return;
        }
        let mut ticks = tokio::time::interval(std::time::Duration::from_mins(1));
        ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! { _ = &mut stopped => break, _ = ticks.tick() => {} }
            let server = std::sync::Arc::clone(&server);
            let result = tokio::task::spawn_blocking(move || {
                maintain(
                    &server.project,
                    &server.secrets.configuration,
                    &server.runtime,
                    false,
                )
            })
            .await;
            if !matches!(result, Ok(Ok(_))) {
                eprintln!(
                    "Diamond shared-file reconciliation deferred; inspect storage availability."
                );
            }
        }
    });
    (stop, job)
}

pub(super) fn contracts() -> serde_json::Value {
    serde_json::json!({
        "target":"server; generated browser client streams native Files",
        "native_grant":"shared-fs-v1 private shared directory bound to PostgreSQL identity",
        "arguments":{"begin":"{parent: Text, filename: Text, length: canonical integer Text, media_type: Text}","put":"exact Content-Length octet stream; opaque upload ticket", "finish":"opaque upload ticket", "status":"attachment ID", "download":"attachment ID", "delete":"attachment ID"},
        "result":"generated ID/state/metadata or bounded attachment stream",
        "effects":"current parent read policy; mutations also require parent update policy and declared DbRead/DbWrite budgets",
        "maximum_stream_bytes":8_388_608,"stream_deadline_ms":30_000,"ticket_lifetime_seconds":900,
        "control_body_bytes":16_384,"control_body_deadline_ms":3000,
        "maximum_pending_per_principal":4,"maximum_application_bytes":536_870_912,"maximum_application_rows":10_000,
        "transaction":"adapter-owned database transitions; filesystem effects cannot roll back with PostgreSQL",
        "replay":"never automatic; explicit finish retry verifies prior publication; failed delivery may be unknown",
        "cleanup":"partial stream guard; owned request permits; bounded 100-row reconciliation on startup and every minute",
        "failure":"redacted policy/input/quota/deadline/storage failure; pending objects remain unreadable"
    })
}
