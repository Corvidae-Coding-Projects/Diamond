//! Policy-bound access to installed Git without a shell or managed filesystem API.

use super::{CheckedProject, Manifest, artifact, input, storage::PostgresStore};
use crate::{
    Error,
    authority::{Effect, Scope},
    error::Result,
    hir::CheckedProgram,
    integrations::GitGrant,
};
use base64::Engine as _;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write as _,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq as _;

const MARKER: &str = ".diamond-git-store";
const OUTPUT_LIMIT: usize = 4 * 1024 * 1024;
const BLOB_LIMIT: usize = 1024 * 1024;
const COMMAND_DEADLINE: Duration = Duration::from_secs(10);
pub(super) const MAX_TRANSPORT_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    profile: String,
    pub(super) bindings: BTreeMap<String, Binding>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub(super) resource: String,
}

impl Config {
    pub(super) fn check(&self, manifest: &Manifest, program: &CheckedProgram) -> Result<()> {
        if self.profile != "git-v2"
            || manifest.identity.is_none()
            || program.storage.is_none()
            || self.bindings.is_empty()
            || self.bindings.len() > 16
        {
            return Err(input(
                "Git requires git-v2, accounts and relational storage",
            ));
        }
        for (name, binding) in &self.bindings {
            if !super::resolve::identifier(name)
                || !program.resources.contains_key(&binding.resource)
                || !manifest.provides.contains(&format!("GitRead<{name}>"))
                || !manifest
                    .provides
                    .contains(&format!("DbRead<{}>", binding.resource))
            {
                return Err(input(
                    "invalid Git binding or absent Git/resource capabilities",
                ));
            }
        }
        Ok(())
    }
}

const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_git;
CREATE TABLE IF NOT EXISTS diamond_git.stores(app TEXT NOT NULL REFERENCES diamond_w2.apps(app), binding TEXT NOT NULL, shape TEXT NOT NULL, identity TEXT, generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), PRIMARY KEY(app,binding));
CREATE TABLE IF NOT EXISTS diamond_git.repositories(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,binding,repository), FOREIGN KEY(app,binding) REFERENCES diamond_git.stores(app,binding));
CREATE TABLE IF NOT EXISTS diamond_git.ref_states(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, reference TEXT NOT NULL, object TEXT NOT NULL CHECK(octet_length(object) IN (40,64)), PRIMARY KEY(app,binding,repository,reference), FOREIGN KEY(app,binding,repository) REFERENCES diamond_git.repositories(app,binding,repository));
CREATE TABLE IF NOT EXISTS diamond_git.protections(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, pattern TEXT NOT NULL CHECK(octet_length(pattern)<=255), required_role TEXT NOT NULL CHECK(required_role IN ('authenticated','account_admin')), allow_force BOOLEAN NOT NULL, allow_delete BOOLEAN NOT NULL, required_approvals INTEGER NOT NULL DEFAULT 0 CHECK(required_approvals BETWEEN 0 AND 100), require_pipeline BOOLEAN NOT NULL DEFAULT false, require_discussions BOOLEAN NOT NULL DEFAULT false, require_signed_commits BOOLEAN NOT NULL DEFAULT false, scan_secrets BOOLEAN NOT NULL DEFAULT false, generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), PRIMARY KEY(app,binding,repository,pattern), FOREIGN KEY(app,binding) REFERENCES diamond_git.stores(app,binding));
CREATE TABLE IF NOT EXISTS diamond_git.gates(app TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, reference TEXT NOT NULL, object TEXT NOT NULL CHECK(octet_length(object) IN (40,64)), approvals INTEGER NOT NULL CHECK(approvals BETWEEN 0 AND 100), pipeline_succeeded BOOLEAN NOT NULL, discussions_resolved BOOLEAN NOT NULL, expires TIMESTAMPTZ NOT NULL, generation BIGINT NOT NULL DEFAULT 1 CHECK(generation>0), PRIMARY KEY(app,binding,repository,reference,object), FOREIGN KEY(app,binding) REFERENCES diamond_git.stores(app,binding));
CREATE TABLE IF NOT EXISTS diamond_git.deploy_keys(app TEXT NOT NULL, id TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, fingerprint TEXT NOT NULL CHECK(octet_length(fingerprint)<=128), public_key TEXT NOT NULL CHECK(octet_length(public_key)<=512), label TEXT NOT NULL CHECK(octet_length(label)<=160), can_write BOOLEAN NOT NULL, created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), revoked BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY(app,id), UNIQUE(app,fingerprint), FOREIGN KEY(app,binding) REFERENCES diamond_git.stores(app,binding));
CREATE INDEX IF NOT EXISTS git_deploy_key_repository ON diamond_git.deploy_keys(app,binding,repository);
CREATE TABLE IF NOT EXISTS diamond_git.certificate_principals(app TEXT NOT NULL, id TEXT NOT NULL, principal TEXT NOT NULL CHECK(octet_length(principal)<=160), binding TEXT NOT NULL, repository TEXT NOT NULL, can_write BOOLEAN NOT NULL, expires TIMESTAMPTZ NOT NULL, revoked BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY(app,id), UNIQUE(app,principal), FOREIGN KEY(app,binding) REFERENCES diamond_git.stores(app,binding));
CREATE TABLE IF NOT EXISTS diamond_git.push_events(app TEXT NOT NULL, nonce TEXT NOT NULL, binding TEXT NOT NULL, repository TEXT NOT NULL, actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','deploy','certificate')), actor TEXT NOT NULL CHECK(octet_length(actor)<=160), store_generation BIGINT NOT NULL CHECK(store_generation>0), updates TEXT NOT NULL CHECK(octet_length(updates)<=524288), state TEXT NOT NULL CHECK(state IN ('prepared','committed')), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), committed TIMESTAMPTZ, delivered TIMESTAMPTZ, PRIMARY KEY(app,nonce), FOREIGN KEY(app,binding) REFERENCES diamond_git.stores(app,binding));
CREATE INDEX IF NOT EXISTS git_push_event_delivery ON diamond_git.push_events(app,state,created);";

pub(super) fn schema(store: &mut PostgresStore, config: &Config, initialize: bool) -> Result<()> {
    let shape = artifact::hash(&serde_json::to_vec(config).map_err(|_| Error::Denied)?);
    if initialize {
        store.connection.batch(SCHEMA)?;
        for binding in config.bindings.keys() {
            store.connection.query(
                "INSERT INTO diamond_git.stores(app,binding,shape) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                &[&store.app, binding, &shape],
            )?;
        }
    }
    let rows = store.connection.query(
        "SELECT binding,shape FROM diamond_git.stores WHERE app=$1 ORDER BY binding",
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
            "incompatible Git metadata; stop servers and initialize the current schema",
        ));
    }
    Ok(())
}

#[derive(Clone, Debug)]
struct Store {
    root: PathBuf,
    executable: PathBuf,
    identity: String,
    generation: i64,
    app: String,
    binding: String,
}

impl Store {
    fn open(
        database: &mut PostgresStore,
        grants: &crate::RuntimeGrants,
        binding: &str,
        initialize: bool,
    ) -> Result<Self> {
        let config = database
            .git
            .as_ref()
            .and_then(|config| config.bindings.get(binding))
            .ok_or(Error::Denied)?;
        if !database
            .git
            .as_ref()
            .is_some_and(|git| git.bindings.contains_key(binding))
            || !database.program.resources.contains_key(&config.resource)
        {
            return Err(Error::Denied);
        }
        let grant = grants.git.get(binding).ok_or(Error::Denied)?;
        validate_grant(grant)?;
        let marker = grant.root.join(MARKER);
        if initialize && !marker.exists() {
            let mut file = options().write(true).create_new(true).open(&marker)?;
            file.write_all(crate::authority::random_hex()?.as_bytes())?;
            file.sync_all()?;
            File::open(&grant.root)?.sync_all()?;
        }
        let identity = store_identity(&marker)?;
        if initialize {
            database.connection.query(
                "UPDATE diamond_git.stores SET identity=$3 WHERE app=$1 AND binding=$2 AND identity IS NULL",
                &[&database.app, &binding, &identity],
            )?;
        }
        let rows = database.connection.query(
            "SELECT identity,generation FROM diamond_git.stores WHERE app=$1 AND binding=$2",
            &[&database.app, &binding],
        )?;
        if rows.len() != 1
            || rows[0]
                .try_get::<_, Option<String>>(0)
                .map_err(|_| Error::Denied)?
                .as_deref()
                != Some(&identity)
            || rows[0].try_get::<_, i64>(1).map_err(|_| Error::Denied)? <= 0
        {
            return Err(input("Git store identity mismatch"));
        }
        let generation = rows[0].try_get::<_, i64>(1).map_err(|_| Error::Denied)?;
        let store = Self {
            root: grant.root.clone(),
            executable: grant.executable.clone(),
            identity,
            generation,
            app: database.app.clone(),
            binding: binding.into(),
        };
        if initialize {
            store.install_hook()?;
        }
        store.verify()?;
        Ok(store)
    }

    fn verify(&self) -> Result<()> {
        private_directory(&self.root)?;
        executable(&self.executable)?;
        if store_identity(&self.root.join(MARKER))? != self.identity {
            return Err(input("Git store identity changed"));
        }
        validate_hook(&self.root, "pre-receive")?;
        validate_hook(&self.root, "post-receive")?;
        private_directory(&self.event_path())?;
        Ok(())
    }

    fn hook_path(&self) -> PathBuf {
        self.root.join(".diamond-hooks")
    }

    fn event_path(&self) -> PathBuf {
        self.root.join(".diamond-events")
    }

    fn install_hook(&self) -> Result<()> {
        let directory = self.hook_path();
        if !directory.exists() {
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            builder.create(&directory)?;
        }
        private_directory(&directory)?;
        let events = self.event_path();
        if !events.exists() {
            let mut builder = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            builder.create(&events)?;
        }
        private_directory(&events)?;
        for hook in ["pre-receive", "post-receive"] {
            let temporary = directory.join(format!(".{hook}-{}", crate::authority::random_hex()?));
            let mut output = options().write(true).create_new(true).open(&temporary)?;
            output.write_all(hook_source(hook)?.as_bytes())?;
            output.sync_all()?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&temporary, fs::Permissions::from_mode(0o500))?;
            }
            fs::rename(&temporary, directory.join(hook))?;
        }
        File::open(&directory)?.sync_all()?;
        Ok(())
    }

    fn repository_path(&self, repository: &str) -> Result<PathBuf> {
        self.verify()?;
        if !crate::auth::valid_id(repository) {
            return Err(Error::Denied);
        }
        Ok(self
            .root
            .join(format!("{}-{}-{repository}.git", self.app, self.binding)))
    }

    fn create(&self, repository: &str) -> Result<serde_json::Value> {
        let path = self.repository_path(repository)?;
        let created = match fs::symlink_metadata(&path) {
            Ok(metadata) => {
                if !metadata.is_dir() || path.canonicalize()? != path {
                    return Err(Error::Denied);
                }
                self.require_bare(&path)?;
                false
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                self.run(
                    &[
                        "init".into(),
                        "--bare".into(),
                        "--initial-branch=main".into(),
                        "--object-format=sha1".into(),
                        path.as_os_str().to_owned(),
                    ],
                    16_384,
                )?;
                if path.canonicalize()? != path {
                    return Err(Error::Denied);
                }
                true
            }
            Err(error) => return Err(error.into()),
        };
        for (key, value) in [
            ("core.hooksPath", self.hook_path()),
            ("receive.fsckObjects", PathBuf::from("true")),
            (
                "receive.maxInputSize",
                PathBuf::from(MAX_TRANSPORT_BYTES.to_string()),
            ),
            ("transfer.fsckObjects", PathBuf::from("true")),
            ("uploadpack.allowFilter", PathBuf::from("true")),
            ("receive.advertisePushOptions", PathBuf::from("true")),
        ] {
            self.run(
                &[
                    "--git-dir".into(),
                    path.as_os_str().to_owned(),
                    "config".into(),
                    "--local".into(),
                    key.into(),
                    value.into_os_string(),
                ],
                16_384,
            )?;
        }
        self.require_repository(&path)?;
        File::open(&self.root)?.sync_all()?;
        Ok(serde_json::json!({"state":if created { "created" } else { "existing" }}))
    }

    fn require_bare(&self, path: &Path) -> Result<()> {
        let output = self.run(
            &[
                "--git-dir".into(),
                path.as_os_str().to_owned(),
                "rev-parse".into(),
                "--is-bare-repository".into(),
            ],
            32,
        )?;
        if output != b"true\n" {
            return Err(Error::Denied);
        }
        Ok(())
    }

    fn require_repository(&self, path: &Path) -> Result<()> {
        self.require_bare(path)?;
        let hook_path = self.hook_path();
        let hook_path = hook_path.to_str().ok_or(Error::Denied)?;
        let output = self.run(
            &[
                "--git-dir".into(),
                path.as_os_str().to_owned(),
                "config".into(),
                "--local".into(),
                "--no-includes".into(),
                "--null".into(),
                "--list".into(),
            ],
            16_384,
        )?;
        let mut settings = BTreeMap::new();
        for entry in output
            .split(|byte| *byte == 0)
            .filter(|entry| !entry.is_empty())
        {
            let newline = entry
                .iter()
                .position(|byte| *byte == b'\n')
                .ok_or(Error::Denied)?;
            let key = text(&entry[..newline])?;
            let value = text(entry.get(newline + 1..).ok_or(Error::Denied)?)?;
            if settings.insert(key, value).is_some() {
                return Err(Error::Denied);
            }
        }
        if settings.len() != 9
            || settings.get("core.repositoryformatversion") != Some(&"0")
            || !matches!(settings.get("core.filemode"), Some(&"true" | &"false"))
            || settings.get("core.bare") != Some(&"true")
            || settings.get("core.hookspath") != Some(&hook_path)
            || settings.get("receive.fsckobjects") != Some(&"true")
            || settings.get("receive.maxinputsize") != Some(&"1073741824")
            || settings.get("transfer.fsckobjects") != Some(&"true")
            || settings.get("uploadpack.allowfilter") != Some(&"true")
            || settings.get("receive.advertisepushoptions") != Some(&"true")
        {
            return Err(input("Git repository configuration changed"));
        }
        Ok(())
    }

    fn query(
        &self,
        repository: &str,
        operation: &str,
        revision: &str,
        path: &str,
        limit: i64,
    ) -> Result<serde_json::Value> {
        let repository_path = self.repository_path(repository)?;
        if repository_path.canonicalize()? != repository_path {
            return Err(Error::Denied);
        }
        self.require_repository(&repository_path)?;
        if !(1..=200).contains(&limit) {
            return Err(input("Git result limit must be 1..200"));
        }
        match operation {
            "head" => self.head(&repository_path),
            "tree" => self.tree(&repository_path, revision, path, limit),
            "blob" => self.blob(&repository_path, revision, path),
            "history" => self.history(&repository_path, revision, limit),
            "diff" => self.diff(&repository_path, revision, path),
            _ => Err(Error::Denied),
        }
    }

    fn head(&self, repository: &Path) -> Result<serde_json::Value> {
        let symbolic = self.run_repo(repository, &["symbolic-ref", "-q", "HEAD"], 4096)?;
        let reference = text(&symbolic)?.trim_end().to_owned();
        let object = match self.resolve(repository, "HEAD") {
            Ok(value) => serde_json::json!({"tag":"Some","value":value}),
            Err(Error::Propagation(_)) => serde_json::json!({"tag":"None"}),
            Err(error) => return Err(error),
        };
        Ok(serde_json::json!({"reference":reference,"object":object}))
    }

    fn tree(
        &self,
        repository: &Path,
        revision: &str,
        path: &str,
        limit: i64,
    ) -> Result<serde_json::Value> {
        valid_subpath(path)?;
        let object = self.resolve(repository, revision)?;
        let treeish = if path.is_empty() {
            object.clone()
        } else {
            format!("{object}:{path}")
        };
        let output = self.run_repo_os(
            repository,
            &["ls-tree".into(), "-z".into(), "-l".into(), treeish.into()],
            OUTPUT_LIMIT,
        )?;
        let maximum = usize::try_from(limit).map_err(|_| Error::Denied)?;
        let mut entries = Vec::new();
        let mut truncated = false;
        for raw in output
            .split(|byte| *byte == 0)
            .filter(|entry| !entry.is_empty())
        {
            if entries.len() == maximum {
                truncated = true;
                break;
            }
            let tab = raw
                .iter()
                .position(|byte| *byte == b'\t')
                .ok_or(Error::Denied)?;
            let (metadata, name_with_tab) = raw.split_at(tab);
            let name = name_with_tab.get(1..).ok_or(Error::Denied)?;
            let metadata = text(metadata)?;
            let mut fields = metadata.split_ascii_whitespace();
            let mode = fields.next().ok_or(Error::Denied)?;
            let kind = fields.next().ok_or(Error::Denied)?;
            let object = fields.next().ok_or(Error::Denied)?;
            let size = fields.next().ok_or(Error::Denied)?;
            if fields.next().is_some()
                || !valid_oid(object)
                || !matches!(kind, "blob" | "tree" | "commit")
                || !mode.bytes().all(|byte| (b'0'..=b'7').contains(&byte))
            {
                return Err(Error::Denied);
            }
            let size = if size == "-" {
                serde_json::json!({"tag":"None"})
            } else {
                serde_json::json!({"tag":"Some","value":size})
            };
            entries.push(serde_json::json!({
                "mode":mode,
                "kind":kind,
                "object":object,
                "size":size,
                "name":text(name)?,
            }));
        }
        Ok(
            serde_json::json!({"revision":object,"path":path,"entries":entries,"truncated":truncated}),
        )
    }

    fn blob(&self, repository: &Path, revision: &str, path: &str) -> Result<serde_json::Value> {
        valid_subpath(path)?;
        if path.is_empty() {
            return Err(Error::Denied);
        }
        let commit = self.resolve(repository, revision)?;
        let expression = format!("{commit}:{path}");
        let object = self.resolve_object(repository, &expression)?;
        let kind = self.run_repo_os(
            repository,
            &["cat-file".into(), "-t".into(), object.clone().into()],
            32,
        )?;
        if text(&kind)?.trim_end() != "blob" {
            return Err(Error::Denied);
        }
        let size = self.run_repo_os(
            repository,
            &["cat-file".into(), "-s".into(), object.clone().into()],
            32,
        )?;
        let size = text(&size)?
            .trim_end()
            .parse::<usize>()
            .map_err(|_| Error::Denied)?;
        if size > BLOB_LIMIT {
            return Err(Error::Limit("Git blob preview"));
        }
        let bytes = self.run_repo_os(
            repository,
            &["cat-file".into(), "blob".into(), object.clone().into()],
            BLOB_LIMIT,
        )?;
        if bytes.len() != size || bytes.contains(&0) {
            return Err(input("Git blob is binary or changed during read"));
        }
        Ok(
            serde_json::json!({"revision":commit,"path":path,"object":object,"size":size.to_string(),"text":text(&bytes)?}),
        )
    }

    fn history(&self, repository: &Path, revision: &str, limit: i64) -> Result<serde_json::Value> {
        let object = self.resolve(repository, revision)?;
        let output = self.run_repo_os(
            repository,
            &[
                "log".into(),
                "-z".into(),
                "--no-show-signature".into(),
                "--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%s".into(),
                format!("-n{limit}").into(),
                object.clone().into(),
            ],
            OUTPUT_LIMIT,
        )?;
        if output.last() != Some(&0) {
            return Err(Error::Denied);
        }
        let fields = output[..output.len() - 1]
            .split(|byte| *byte == 0)
            .map(text)
            .collect::<Result<Vec<_>>>()?;
        if fields.len() % 6 != 0 {
            return Err(Error::Denied);
        }
        let mut commits = Vec::new();
        for fields in fields.chunks_exact(6) {
            if !valid_oid(fields[0])
                || fields[1]
                    .split_ascii_whitespace()
                    .any(|parent| !valid_oid(parent))
            {
                return Err(Error::Denied);
            }
            commits.push(serde_json::json!({"object":fields[0],"parents":fields[1],"author_name":fields[2],"author_email":fields[3],"authored_at":fields[4],"subject":fields[5]}));
        }
        Ok(serde_json::json!({"revision":object,"commits":commits}))
    }

    fn diff(&self, repository: &Path, old: &str, new: &str) -> Result<serde_json::Value> {
        let old = self.resolve(repository, old)?;
        let new = self.resolve(repository, new)?;
        let output = self.run_repo_os(
            repository,
            &[
                "diff".into(),
                "--no-ext-diff".into(),
                "--no-textconv".into(),
                "--find-renames".into(),
                "--unified=3".into(),
                old.clone().into(),
                new.clone().into(),
                "--".into(),
            ],
            OUTPUT_LIMIT,
        )?;
        Ok(serde_json::json!({"old":old,"new":new,"text":text(&output)?}))
    }

    fn resolve(&self, repository: &Path, revision: &str) -> Result<String> {
        valid_revision(revision)?;
        self.resolve_object(repository, &format!("{revision}^{{commit}}"))
    }

    fn resolve_object(&self, repository: &Path, expression: &str) -> Result<String> {
        if expression.len() > 4224 || expression.bytes().any(|byte| byte == 0) {
            return Err(Error::Denied);
        }
        let output = self.run_repo_os(
            repository,
            &[
                "rev-parse".into(),
                "--verify".into(),
                "--end-of-options".into(),
                expression.into(),
            ],
            128,
        )?;
        let object = text(&output)?.trim_end().to_owned();
        if !valid_oid(&object) {
            return Err(Error::Denied);
        }
        Ok(object)
    }

    fn run_repo(&self, repository: &Path, arguments: &[&str], limit: usize) -> Result<Vec<u8>> {
        let mut owned = vec!["--git-dir".into(), repository.as_os_str().to_owned()];
        owned.extend(arguments.iter().map(Into::into));
        self.run(&owned, limit)
    }

    fn run_repo_os(
        &self,
        repository: &Path,
        arguments: &[std::ffi::OsString],
        limit: usize,
    ) -> Result<Vec<u8>> {
        let mut owned = vec!["--git-dir".into(), repository.as_os_str().to_owned()];
        owned.extend_from_slice(arguments);
        self.run(&owned, limit)
    }

    fn run(&self, arguments: &[std::ffi::OsString], limit: usize) -> Result<Vec<u8>> {
        self.verify()?;
        let mut command = Command::new(&self.executable);
        command
            .args([
                "-c",
                "protocol.file.allow=never",
                "-c",
                "core.askPass=",
                "-c",
                "credential.helper=",
                "-c",
                "pack.threads=1",
                "-c",
                "core.deltaBaseCacheLimit=16777216",
            ])
            .args(arguments)
            .env_clear()
            .env("HOME", &self.root)
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("LC_ALL", "C.UTF-8")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|_| git_failure())?;
        let stdout = child.stdout.take().ok_or(Error::Denied)?;
        let stderr = child.stderr.take().ok_or(Error::Denied)?;
        let stdout_reader = thread::spawn(move || read_bounded(stdout, limit));
        let stderr_reader = thread::spawn(move || read_bounded(stderr, 16_384));
        let deadline = Instant::now() + COMMAND_DEADLINE;
        let status = loop {
            if let Some(status) = child.try_wait().map_err(|_| git_failure())? {
                break status;
            }
            if Instant::now() >= deadline {
                let _killed = child.kill();
                let _waited = child.wait();
                return Err(Error::Limit("Git command deadline"));
            }
            thread::sleep(Duration::from_millis(5));
        };
        let stdout = stdout_reader.join().map_err(|_| git_failure())??;
        let _stderr = stderr_reader.join().map_err(|_| git_failure())??;
        if !status.success() {
            return Err(git_failure());
        }
        Ok(stdout)
    }
}

#[derive(Debug)]
pub(super) struct Transport {
    pub(super) executable: PathBuf,
    pub(super) root: PathBuf,
    pub(super) repository: String,
    pub(super) repository_id: String,
    pub(super) identity: String,
    pub(super) generation: i64,
    pub(super) app: String,
    pub(super) binding: String,
    pub(super) actor: String,
    pub(super) actor_kind: String,
    pub(super) fence: Option<ReceiveFence>,
}

#[derive(Clone, Copy)]
pub(super) struct TransportRequest<'a> {
    pub(super) token: Option<&'a str>,
    pub(super) binding: &'a str,
    pub(super) repository: &'a str,
    pub(super) write: bool,
}

#[derive(Debug)]
pub(super) struct ReceiveFence {
    release: std::sync::mpsc::Sender<()>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Drop for ReceiveFence {
    fn drop(&mut self) {
        let _sent = self.release.send(());
        if let Some(thread) = self.thread.take() {
            let _joined = thread.join();
        }
    }
}

fn receive_fence(
    configuration: &str,
    app: &str,
    binding: &str,
    shared: bool,
) -> Result<ReceiveFence> {
    if !valid_app(app) || !super::resolve::identifier(binding) {
        return Err(Error::Denied);
    }
    let key = format!("diamond-git:{app}:{binding}");
    let configuration = zeroize::Zeroizing::new(configuration.to_owned());
    let (ready_send, ready_receive) = std::sync::mpsc::sync_channel(1);
    let (release_send, release_receive) = std::sync::mpsc::channel();
    let thread = std::thread::Builder::new()
        .name("diamond-git-fence".into())
        .spawn(move || {
            let acquired = (|| {
                let mut connection = crate::host::HostRuntime::connect_worker(&configuration)?;
                let operation = if shared {
                    "SELECT pg_catalog.pg_advisory_lock_shared(pg_catalog.hashtextextended($1,0))"
                } else {
                    "SELECT pg_catalog.pg_advisory_lock(pg_catalog.hashtextextended($1,0))"
                };
                if connection.query(operation, &[&key])?.len() != 1 {
                    return Err(Error::Denied);
                }
                ready_send.send(Ok(())).map_err(|_| Error::Denied)?;
                let _released = release_receive.recv();
                drop(connection);
                Ok(())
            })();
            if let Err(error) = acquired {
                let _sent = ready_send.send(Err(error));
            }
        })?;
    match ready_receive.recv().map_err(|_| Error::Denied)? {
        Ok(()) => Ok(ReceiveFence {
            release: release_send,
            thread: Some(thread),
        }),
        Err(error) => {
            let _joined = thread.join();
            Err(error)
        }
    }
}

pub(super) fn initialize(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<String> {
    let git = project.manifest.git.as_ref().ok_or(Error::Denied)?;
    if grants.git.keys().collect::<BTreeSet<_>>() != git.bindings.keys().collect::<BTreeSet<_>>() {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let mut initialized = Vec::new();
    for binding in git.bindings.keys() {
        Store::open(&mut database, grants, binding, true)?;
        initialized.push(binding);
    }
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({"initialized":initialized}).to_string())
}

pub(super) fn promote(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    binding: &str,
    expected_generation: i64,
) -> Result<String> {
    if expected_generation <= 0
        || !project
            .manifest
            .git
            .as_ref()
            .is_some_and(|git| git.bindings.contains_key(binding))
    {
        return Err(Error::Denied);
    }
    let app = artifact::hash(project.manifest.name.as_bytes());
    let _fence = receive_fence(configuration, &app, binding, false)?;
    let grant = grants.git.get(binding).ok_or(Error::Denied)?;
    validate_grant(grant)?;
    let marker = grant.root.join(MARKER);
    if !marker.exists() {
        let mut file = options().write(true).create_new(true).open(&marker)?;
        file.write_all(crate::authority::random_hex()?.as_bytes())?;
        file.sync_all()?;
        File::open(&grant.root)?.sync_all()?;
    }
    let identity = store_identity(&marker)?;
    let generation = expected_generation.checked_add(1).ok_or(Error::Denied)?;
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let rows = database.connection.query(
        "SELECT identity,generation FROM diamond_git.stores WHERE app=$1 AND binding=$2",
        &[&database.app, &binding],
    )?;
    if rows.len() != 1
        || rows[0].try_get::<_, i64>(1).map_err(|_| Error::Denied)? != expected_generation
        || rows[0]
            .try_get::<_, Option<String>>(0)
            .map_err(|_| Error::Denied)?
            .as_deref()
            == Some(identity.as_str())
    {
        return database.finish_request(Err(Error::Conflict));
    }
    let store = Store {
        root: grant.root.clone(),
        executable: grant.executable.clone(),
        identity: identity.clone(),
        generation,
        app: database.app.clone(),
        binding: binding.to_owned(),
    };
    store.install_hook()?;
    prepare_promoted_repositories(&store, &mut database)?;
    let target_inventory = store_inventory(&store)?;
    let expected_inventory = database_inventory(&mut database, binding)?;
    if target_inventory.repositories != expected_inventory.repositories
        || target_inventory.refs != expected_inventory.refs
    {
        return database.finish_request(Err(Error::Conflict));
    }
    let unfinished = database.connection.query(
        "SELECT nonce FROM diamond_git.push_events WHERE app=$1 AND binding=$2 AND state='prepared' LIMIT 1",
        &[&database.app, &binding],
    )?;
    if !unfinished.is_empty() {
        return database.finish_request(Err(Error::Conflict));
    }
    File::open(&store.root)?.sync_all()?;
    let changed = database.connection.query(
        "UPDATE diamond_git.stores SET identity=$4,generation=$3 WHERE app=$1 AND binding=$2 AND generation=$5 RETURNING generation",
        &[&database.app, &binding, &generation, &identity, &expected_generation],
    )?;
    if changed.len() != 1 {
        return database.finish_request(Err(Error::Conflict));
    }
    database.finish_request(Ok(()))?;
    Ok(serde_json::json!({"binding":binding,"generation":generation}).to_string())
}

fn prepare_promoted_repositories(store: &Store, database: &mut PostgresStore) -> Result<()> {
    let binding = store.binding.as_str();
    let prefix = format!("{}-{binding}-", database.app);
    for entry in fs::read_dir(&store.root)? {
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        let Some(repository) = name
            .strip_prefix(&prefix)
            .and_then(|value| value.strip_suffix(".git"))
        else {
            continue;
        };
        if !crate::auth::valid_id(repository) {
            return database.finish_request(Err(Error::Denied));
        }
        let path = entry.path();
        if !entry.file_type()?.is_dir() || path.canonicalize()? != path {
            return database.finish_request(Err(Error::Denied));
        }
        store.run(
            &[
                "--git-dir".into(),
                path.as_os_str().to_owned(),
                "config".into(),
                "--local".into(),
                "core.hooksPath".into(),
                store.hook_path().into_os_string(),
            ],
            16_384,
        )?;
        store.require_repository(&path)?;
    }
    Ok(())
}

#[derive(Default)]
struct GitInventory {
    repositories: BTreeSet<String>,
    refs: BTreeSet<(String, String, String)>,
}

fn database_inventory(database: &mut PostgresStore, binding: &str) -> Result<GitInventory> {
    let repository_rows = database.connection.query(
        "SELECT repository FROM diamond_git.repositories WHERE app=$1 AND binding=$2 ORDER BY repository LIMIT 10001",
        &[&database.app, &binding],
    )?;
    if repository_rows.len() > 10_000 {
        return database.finish_request(Err(Error::Limit("Git repository inventory")));
    }
    let repositories = repository_rows
        .into_iter()
        .map(|row| row.try_get::<_, String>(0).map_err(|_| Error::Denied))
        .collect::<Result<BTreeSet<_>>>()?;
    let ref_rows = database.connection.query(
        "SELECT repository,reference,object FROM diamond_git.ref_states WHERE app=$1 AND binding=$2 ORDER BY repository,reference LIMIT 100001",
        &[&database.app, &binding],
    )?;
    if ref_rows.len() > 100_000 {
        return database.finish_request(Err(Error::Limit("Git ref inventory")));
    }
    let refs = ref_rows
        .into_iter()
        .map(|row| {
            Ok((
                row.try_get::<_, String>(0).map_err(|_| Error::Denied)?,
                row.try_get::<_, String>(1).map_err(|_| Error::Denied)?,
                row.try_get::<_, String>(2).map_err(|_| Error::Denied)?,
            ))
        })
        .collect::<Result<BTreeSet<_>>>()?;
    Ok(GitInventory { repositories, refs })
}

fn store_inventory(store: &Store) -> Result<GitInventory> {
    let prefix = format!("{}-{}-", store.app, store.binding);
    let mut repositories = BTreeSet::new();
    let mut refs = BTreeSet::new();
    let mut entries = 0_usize;
    for entry in fs::read_dir(&store.root)? {
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            return Err(Error::Denied);
        };
        if matches!(name, MARKER | ".diamond-hooks" | ".diamond-events") {
            continue;
        }
        entries += 1;
        if entries > 10_000 {
            return Err(Error::Limit("Git repository inventory"));
        }
        let repository = name
            .strip_prefix(&prefix)
            .and_then(|value| value.strip_suffix(".git"))
            .filter(|value| crate::auth::valid_id(value))
            .ok_or(Error::Denied)?;
        let path = entry.path();
        if !entry.file_type()?.is_dir() || path.canonicalize()? != path {
            return Err(Error::Denied);
        }
        store.require_repository(&path)?;
        repositories.insert(repository.to_owned());
        let output = store.run(
            &[
                "--git-dir".into(),
                path.as_os_str().to_owned(),
                "for-each-ref".into(),
                "--format=%(refname) %(objectname)".into(),
                "refs/heads".into(),
                "refs/tags".into(),
            ],
            OUTPUT_LIMIT,
        )?;
        for line in text(&output)?.lines() {
            if refs.len() >= 100_000 {
                return Err(Error::Limit("Git ref inventory"));
            }
            let (reference, object) = line.split_once(' ').ok_or(Error::Denied)?;
            valid_reference(reference)?;
            if !valid_oid(object) {
                return Err(Error::Denied);
            }
            refs.insert((
                repository.to_owned(),
                reference.to_owned(),
                object.to_owned(),
            ));
        }
    }
    Ok(GitInventory { repositories, refs })
}

pub(super) fn reconcile_events(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<String> {
    let git = project.manifest.git.as_ref().ok_or(Error::Denied)?;
    if grants.git.keys().collect::<BTreeSet<_>>() != git.bindings.keys().collect::<BTreeSet<_>>() {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let mut committed = 0_usize;
    let mut prepared = 0_usize;
    let mut remove = Vec::new();
    for binding in git.bindings.keys() {
        let fence_app = database.app.clone();
        let _fence = receive_fence(configuration, &fence_app, binding, false)?;
        let store = Store::open(&mut database, grants, binding, false)?;
        let mut entries = fs::read_dir(store.event_path())?.collect::<std::io::Result<Vec<_>>>()?;
        if entries.len() > 2048 {
            return database.finish_request(Err(Error::Limit("Git event spool files")));
        }
        entries.sort_by_key(fs::DirEntry::file_name);
        for entry in entries {
            let (nonce, is_prepared, mut path, event) =
                read_reconciliation_event(&database, binding, &entry)?;
            if is_prepared {
                prepared += 1;
                if let Some(committed_path) =
                    recover_prepared_event(&mut database, &store, &nonce, &path, &event)?
                {
                    path = committed_path;
                } else {
                    remove.push((store.event_path(), path));
                    continue;
                }
            }
            persist_reconciled_event(&mut database, &event)?;
            committed += 1;
            remove.push((store.event_path(), path));
        }
        let inventory = store_inventory(&store)?;
        replace_database_inventory(&mut database, binding, &inventory)?;
    }
    database.finish_request(Ok(()))?;
    for (directory, path) in remove {
        fs::remove_file(path)?;
        File::open(directory)?.sync_all()?;
    }
    Ok(serde_json::json!({"committed":committed,"prepared":prepared}).to_string())
}

fn read_reconciliation_event(
    database: &PostgresStore,
    binding: &str,
    entry: &fs::DirEntry,
) -> Result<(String, bool, PathBuf, SpoolEvent)> {
    let name = entry.file_name();
    let name = name.to_str().ok_or(Error::Denied)?;
    let (nonce, prepared) = if let Some(nonce) = name.strip_suffix(".prepared") {
        (nonce, true)
    } else if let Some(nonce) = name.strip_suffix(".committed") {
        (nonce, false)
    } else {
        return Err(Error::Denied);
    };
    if !valid_identity(nonce) {
        return Err(Error::Denied);
    }
    let path = entry.path();
    let bytes = read_private_file(&path, 600 * 1024)?;
    let event: SpoolEvent = super::wire::strict_from_slice(&bytes).map_err(|_| Error::Denied)?;
    if event.claim.nonce != nonce
        || event.claim.app != database.app
        || event.claim.binding != binding
        || !crate::auth::valid_id(&event.claim.repository)
        || !valid_actor(&event.claim.actor)
        || !matches!(
            event.claim.actor_kind.as_str(),
            "user" | "deploy" | "certificate"
        )
        || event.claim.store_generation <= 0
        || event.updates.is_empty()
        || event.updates.len() > 256
    {
        return Err(Error::Denied);
    }
    for update in &event.updates {
        if !valid_oid(&update.old)
            || !valid_oid(&update.new)
            || update.old.len() != update.new.len()
        {
            return Err(Error::Denied);
        }
        valid_reference(&update.reference)?;
    }
    Ok((nonce.to_owned(), prepared, path, event))
}

fn recover_prepared_event(
    database: &mut PostgresStore,
    store: &Store,
    nonce: &str,
    path: &Path,
    event: &SpoolEvent,
) -> Result<Option<PathBuf>> {
    let repository = store.repository_path(&event.claim.repository)?;
    if repository.canonicalize()? != repository {
        return Err(Error::Denied);
    }
    store.require_repository(&repository)?;
    let current = event
        .updates
        .iter()
        .map(|update| reference_object(store, &repository, update))
        .collect::<Result<Vec<_>>>()?;
    let matches = |field: fn(&RefUpdate) -> &String| {
        current
            .iter()
            .zip(&event.updates)
            .all(|(actual, update)| actual == field(update))
    };
    if matches(|update| &update.new) {
        let committed = store.event_path().join(format!("{nonce}.committed"));
        fs::rename(path, &committed)?;
        File::open(store.event_path())?.sync_all()?;
        return Ok(Some(committed));
    }
    if !matches(|update| &update.old) {
        return Err(Error::Conflict);
    }
    database.connection.query(
        "DELETE FROM diamond_git.push_events WHERE app=$1 AND nonce=$2 AND binding=$3 AND repository=$4 AND store_generation=$5 AND state='prepared'",
        &[&database.app, &event.claim.nonce, &event.claim.binding, &event.claim.repository, &event.claim.store_generation],
    )?;
    Ok(None)
}

fn persist_reconciled_event(database: &mut PostgresStore, event: &SpoolEvent) -> Result<()> {
    let updates = serialize_updates(&event.updates)?;
    database.connection.batch("BEGIN")?;
    let persisted = (|| {
        let rows = database.connection.query(
            "INSERT INTO diamond_git.push_events(app,nonce,binding,repository,actor_kind,actor,store_generation,updates,state,committed) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'committed',pg_catalog.clock_timestamp()) ON CONFLICT(app,nonce) DO UPDATE SET state='committed',committed=COALESCE(diamond_git.push_events.committed,EXCLUDED.committed) WHERE diamond_git.push_events.binding=EXCLUDED.binding AND diamond_git.push_events.repository=EXCLUDED.repository AND diamond_git.push_events.actor_kind=EXCLUDED.actor_kind AND diamond_git.push_events.actor=EXCLUDED.actor AND diamond_git.push_events.store_generation=EXCLUDED.store_generation AND diamond_git.push_events.updates=EXCLUDED.updates RETURNING nonce",
            &[&database.app, &event.claim.nonce, &event.claim.binding, &event.claim.repository, &event.claim.actor_kind, &event.claim.actor, &event.claim.store_generation, &updates],
        )?;
        if rows.len() != 1 {
            return Err(Error::Conflict);
        }
        Ok(())
    })();
    match persisted {
        Ok(()) => database.connection.batch("COMMIT"),
        Err(error) => {
            let _rollback = database.connection.batch("ROLLBACK");
            Err(error)
        }
    }
}

fn reference_object(store: &Store, repository: &Path, update: &RefUpdate) -> Result<String> {
    let output = store.run_repo(
        repository,
        &[
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            &update.reference,
        ],
        4096,
    )?;
    let mut object = None;
    for line in text(&output)?.lines() {
        let (reference, candidate) = line.split_once(' ').ok_or(Error::Denied)?;
        valid_reference(reference)?;
        if !valid_oid(candidate) {
            return Err(Error::Denied);
        }
        if reference == update.reference && object.replace(candidate.to_owned()).is_some() {
            return Err(Error::Denied);
        }
    }
    Ok(object.unwrap_or_else(|| "0".repeat(update.old.len())))
}

fn replace_database_inventory(
    database: &mut PostgresStore,
    binding: &str,
    inventory: &GitInventory,
) -> Result<()> {
    if inventory.repositories != database_inventory(database, binding)?.repositories {
        return Err(Error::Conflict);
    }
    let refs = inventory
        .refs
        .iter()
        .map(|(repository, reference, object)| {
            serde_json::json!({
                "repository":repository,
                "reference":reference,
                "object":object,
            })
        })
        .collect::<Vec<_>>();
    let refs = serde_json::to_string(&refs).map_err(|_| Error::Denied)?;
    if refs.len() > OUTPUT_LIMIT {
        return Err(Error::Limit("Git ref inventory"));
    }
    database.connection.batch("BEGIN")?;
    let replaced = (|| {
        database.connection.query(
            "DELETE FROM diamond_git.ref_states WHERE app=$1 AND binding=$2",
            &[&database.app, &binding],
        )?;
        database.connection.query(
            "INSERT INTO diamond_git.ref_states(app,binding,repository,reference,object) SELECT $1,$2,inventory.repository,inventory.reference,inventory.object FROM pg_catalog.jsonb_to_recordset($3::text::jsonb) AS inventory(repository text,reference text,object text)",
            &[&database.app, &binding, &refs],
        )?;
        Ok(())
    })();
    match replaced {
        Ok(()) => database.connection.batch("COMMIT"),
        Err(error) => {
            let _rollback = database.connection.batch("ROLLBACK");
            Err(error)
        }
    }
}

pub(super) fn credential_operation(binding: &str, write: bool) -> String {
    format!("git:{binding}:{}", if write { "write" } else { "read" })
}

pub(super) fn credential_scope(project: &CheckedProject, operation: &str) -> Option<String> {
    let mut parts = operation.split(':');
    if parts.next()? != "git" {
        return None;
    }
    let binding_name = parts.next()?;
    let access = parts.next()?;
    if parts.next().is_some() || !matches!(access, "read" | "write") {
        return None;
    }
    let binding = project.manifest.git.as_ref()?.bindings.get(binding_name)?;
    let capability = if access == "write" {
        format!("GitWrite<{binding_name}>")
    } else {
        format!("GitRead<{binding_name}>")
    };
    let database_capability = if access == "write" {
        format!("DbWrite<{}>", binding.resource)
    } else {
        format!("DbRead<{}>", binding.resource)
    };
    if !project.manifest.provides.contains(&capability)
        || !project.manifest.provides.contains(&database_capability)
    {
        return None;
    }
    let storage = project.server.storage.as_ref()?;
    let field = storage.resources.get(&binding.resource)?.scope.as_ref()?;
    let record = project
        .server
        .records
        .get(&project.server.resources[&binding.resource])?;
    let crate::value::Type::Id(tag) = &record.fields.get(field)?.ty else {
        return None;
    };
    Some(tag.clone())
}

pub(super) fn authorize_transport(
    project: &CheckedProject,
    configuration: &str,
    pool: Option<&std::sync::Arc<crate::relational::Pool>>,
    grants: &crate::RuntimeGrants,
    request: TransportRequest<'_>,
) -> Result<Transport> {
    let mut database = PostgresStore::open_with(project, configuration, false, pool)?;
    let result = (|| {
        let config = project
            .manifest
            .git
            .as_ref()
            .and_then(|git| git.bindings.get(request.binding))
            .ok_or(Error::Denied)?;
        let effects = if request.write {
            BTreeSet::from([
                Effect::Read(config.resource.clone()),
                Effect::Write(config.resource.clone()),
            ])
        } else {
            BTreeSet::from([Effect::Read(config.resource.clone())])
        };
        let anonymous = crate::authority::Principal::Anonymous;
        let grant = request
            .token
            .map(|value| database.git_grant(value, request.binding, request.write, project))
            .transpose()?;
        if request.write && grant.is_none() {
            return Err(Error::Unauthenticated);
        }
        let principal = grant.as_ref().map_or(&anonymous, |value| &value.principal);
        let scope = grant.as_ref().map_or_else(
            || Scope::new(principal, &effects),
            |value| Scope::restricted(principal, &effects, &value.restrictions, false),
        );
        database.attachment(&scope, &config.resource, request.repository, request.write)?;
        let store = Store::open(&mut database, grants, request.binding, false)?;
        let path = store.repository_path(request.repository)?;
        if path.canonicalize()? != path {
            return Err(Error::Denied);
        }
        store.require_repository(&path)?;
        let repository = path
            .file_name()
            .and_then(std::ffi::OsStr::to_str)
            .ok_or(Error::Denied)?
            .to_owned();
        let fence = if request.write {
            Some(receive_fence(
                configuration,
                &store.app,
                &store.binding,
                false,
            )?)
        } else {
            None
        };
        Ok(Transport {
            executable: store.executable,
            root: store.root,
            repository,
            repository_id: request.repository.to_owned(),
            identity: store.identity,
            generation: store.generation,
            app: store.app,
            binding: store.binding,
            actor: principal.id().unwrap_or_default().to_owned(),
            actor_kind: "user".into(),
            fence,
        })
    })();
    database.finish_request(result)
}

pub(super) fn authorized_ssh_key(
    project: &CheckedProject,
    configuration: &str,
    fingerprint: &str,
) -> Result<(String, String)> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let account = match database.ssh_key_by_fingerprint(fingerprint) {
        Ok(key) => Some((key.id, key.public_key)),
        Err(Error::Unauthenticated) => None,
        Err(error) => return database.finish_request(Err(error)),
    };
    let deploy = database.connection.query(
        "SELECT id,public_key FROM diamond_git.deploy_keys WHERE app=$1 AND fingerprint=$2 AND NOT revoked",
        &[&database.app, &fingerprint],
    )?;
    let result = match (account, deploy.as_slice()) {
        (Some(value), []) => Ok(value),
        (None, [row]) => Ok((
            row.try_get(0).map_err(|_| Error::Denied)?,
            row.try_get(1).map_err(|_| Error::Denied)?,
        )),
        _ => Err(Error::Unauthenticated),
    };
    database.finish_request(result)
}

pub(super) fn authorized_ssh_principals(
    project: &CheckedProject,
    configuration: &str,
) -> Result<Vec<(String, String)>> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let rows = database.connection.query(
        "SELECT id,principal FROM diamond_git.certificate_principals WHERE app=$1 AND NOT revoked AND expires>pg_catalog.clock_timestamp() ORDER BY principal LIMIT 257",
        &[&database.app],
    )?;
    if rows.len() > 256 {
        return database.finish_request(Err(Error::Limit("SSH certificate principals")));
    }
    let result = rows
        .into_iter()
        .map(|row| {
            let id = row.try_get(0).map_err(|_| Error::Denied)?;
            let principal: String = row.try_get(1).map_err(|_| Error::Denied)?;
            if !valid_certificate_principal(&principal) {
                return Err(Error::Denied);
            }
            Ok((id, principal))
        })
        .collect::<Result<Vec<_>>>();
    database.finish_request(result)
}

enum SshSubject {
    User(crate::authority::Principal),
    Limited {
        kind: &'static str,
        id: String,
        binding: String,
        repository: String,
        can_write: bool,
    },
}

fn ssh_subject(database: &mut PostgresStore, key_id: &str) -> Result<SshSubject> {
    if !valid_identity(key_id) {
        return Err(Error::Unauthenticated);
    }
    let user = database.connection.query(
        "SELECT k.username,u.administrator FROM diamond_identity.ssh_keys k JOIN diamond_identity.users u ON u.app=k.app AND u.username=k.username WHERE k.app=$1 AND k.id=$2 AND NOT k.revoked AND k.epoch=u.epoch AND NOT u.disabled AND u.verified",
        &[&database.app, &key_id],
    )?;
    let deploy = database.connection.query(
        "SELECT binding,repository,can_write FROM diamond_git.deploy_keys WHERE app=$1 AND id=$2 AND NOT revoked",
        &[&database.app, &key_id],
    )?;
    let certificate = database.connection.query(
        "SELECT binding,repository,can_write FROM diamond_git.certificate_principals WHERE app=$1 AND id=$2 AND NOT revoked AND expires>pg_catalog.clock_timestamp()",
        &[&database.app, &key_id],
    )?;
    match (user.as_slice(), deploy.as_slice(), certificate.as_slice()) {
        ([row], [], []) => {
            let username: String = row.try_get(0).map_err(|_| Error::Denied)?;
            let administrator: bool = row.try_get(1).map_err(|_| Error::Denied)?;
            Ok(SshSubject::User(crate::authority::Principal::User {
                id: username,
                roles: if administrator {
                    BTreeSet::from(["account_admin".into()])
                } else {
                    BTreeSet::new()
                },
            }))
        }
        ([], [row], []) => Ok(SshSubject::Limited {
            kind: "deploy",
            id: key_id.to_owned(),
            binding: row.try_get(0).map_err(|_| Error::Denied)?,
            repository: row.try_get(1).map_err(|_| Error::Denied)?,
            can_write: row.try_get(2).map_err(|_| Error::Denied)?,
        }),
        ([], [], [row]) => Ok(SshSubject::Limited {
            kind: "certificate",
            id: key_id.to_owned(),
            binding: row.try_get(0).map_err(|_| Error::Denied)?,
            repository: row.try_get(1).map_err(|_| Error::Denied)?,
            can_write: row.try_get(2).map_err(|_| Error::Denied)?,
        }),
        _ => Err(Error::Unauthenticated),
    }
}

fn authorize_ssh_transport(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    key_id: &str,
    binding: &str,
    repository: &str,
    write: bool,
) -> Result<Transport> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    let result = (|| {
        let config = project
            .manifest
            .git
            .as_ref()
            .and_then(|git| git.bindings.get(binding))
            .ok_or(Error::Denied)?;
        let subject = ssh_subject(&mut database, key_id)?;
        let (actor, actor_kind) = match &subject {
            SshSubject::User(principal) => {
                let mut effects = BTreeSet::from([Effect::Read(config.resource.clone())]);
                if write {
                    effects.insert(Effect::Write(config.resource.clone()));
                }
                let scope = Scope::new(principal, &effects);
                database.attachment(&scope, &config.resource, repository, write)?;
                (
                    principal.id().ok_or(Error::Unauthenticated)?.to_owned(),
                    "user".to_owned(),
                )
            }
            SshSubject::Limited {
                kind,
                id,
                binding: granted_binding,
                repository: granted_repository,
                can_write,
            } => {
                if granted_binding != binding
                    || granted_repository != repository
                    || (write && !can_write)
                {
                    return Err(Error::Denied);
                }
                (id.clone(), (*kind).to_owned())
            }
        };
        let store = Store::open(&mut database, grants, binding, false)?;
        let path = store.repository_path(repository)?;
        if path.canonicalize()? != path {
            return Err(Error::Denied);
        }
        store.require_repository(&path)?;
        let repository_file = path
            .file_name()
            .and_then(std::ffi::OsStr::to_str)
            .ok_or(Error::Denied)?
            .to_owned();
        let fence = if write {
            Some(receive_fence(
                configuration,
                &store.app,
                &store.binding,
                false,
            )?)
        } else {
            None
        };
        Ok(Transport {
            executable: store.executable,
            root: store.root,
            repository: repository_file,
            repository_id: repository.to_owned(),
            identity: store.identity,
            generation: store.generation,
            app: store.app,
            binding: store.binding,
            actor,
            actor_kind,
            fence,
        })
    })();
    database.finish_request(result)
}

pub(super) fn run_ssh(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
    key_id: &str,
    original_command: &str,
) -> Result<()> {
    let (service, binding, repository, write) = parse_ssh_command(original_command)?;
    let transport = authorize_ssh_transport(
        project,
        configuration,
        grants,
        key_id,
        binding,
        repository,
        write,
    )?;
    let protocol = std::env::var("GIT_PROTOCOL")
        .ok()
        .filter(|value| value == "version=2");
    let mut command = Command::new(&transport.executable);
    command
        .args([
            "-c",
            "protocol.file.allow=never",
            "-c",
            "core.askPass=",
            "-c",
            "credential.helper=",
            "-c",
            "pack.threads=1",
            "-c",
            "core.deltaBaseCacheLimit=16777216",
            service,
        ])
        .arg(transport.root.join(&transport.repository))
        .env_clear()
        .env("HOME", &transport.root)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("LC_ALL", "C.UTF-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    if let Some(protocol) = protocol {
        command.env("GIT_PROTOCOL", protocol);
    }
    if write {
        let policy = policy_environment(&transport)?;
        command
            .env("DIAMOND_GIT_STORE_ROOT", &transport.root)
            .env("DIAMOND_GIT_EXECUTABLE", &transport.executable)
            .env("DIAMOND_GIT_POLICY_DATABASE", configuration)
            .env("DIAMOND_GIT_POLICY_CLAIM", policy.claim)
            .env("DIAMOND_GIT_POLICY_SIGNATURE", policy.signature);
    }
    let mut child = command.spawn().map_err(|_| git_failure())?;
    let child_input = child.stdin.take().ok_or_else(git_failure)?;
    let child_output = child.stdout.take().ok_or_else(git_failure)?;
    let failed = Arc::new(AtomicBool::new(false));
    let input_failed = Arc::clone(&failed);
    let _input = thread::spawn(move || {
        if copy_transport(std::io::stdin(), child_input, MAX_TRANSPORT_BYTES).is_err() {
            input_failed.store(true, Ordering::Relaxed);
        }
    });
    let output_failed = Arc::clone(&failed);
    let output = thread::spawn(move || {
        let result = copy_transport(child_output, std::io::stdout(), MAX_TRANSPORT_BYTES);
        if result.is_err() {
            output_failed.store(true, Ordering::Relaxed);
        }
        result
    });
    let deadline = Instant::now() + Duration::from_mins(15);
    loop {
        if let Some(status) = child.try_wait().map_err(|_| git_failure())? {
            let output_ok = output.join().is_ok_and(|result| result.is_ok());
            return if status.success() && output_ok && !failed.load(Ordering::Relaxed) {
                Ok(())
            } else {
                Err(git_failure())
            };
        }
        if failed.load(Ordering::Relaxed) || Instant::now() >= deadline {
            let _killed = child.kill();
            let _waited = child.wait();
            return Err(Error::Limit("Git SSH transport budget"));
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn copy_transport(mut input: impl Read, mut output: impl Write, maximum: u64) -> io::Result<()> {
    let mut total = 0_u64;
    let mut buffer = vec![0_u8; 65_536].into_boxed_slice();
    loop {
        let count = input.read(&mut buffer)?;
        if count == 0 {
            return output.flush();
        }
        total = total.saturating_add(u64::try_from(count).unwrap_or(u64::MAX));
        if total > maximum {
            return Err(io::Error::other("Git SSH transfer limit"));
        }
        output.write_all(&buffer[..count])?;
        output.flush()?;
    }
}

fn parse_ssh_command(command: &str) -> Result<(&str, &str, &str, bool)> {
    if command.is_empty()
        || command.len() > 512
        || command.bytes().any(|byte| byte.is_ascii_control())
    {
        return Err(Error::Denied);
    }
    let (requested, argument) = command.split_once(' ').ok_or(Error::Denied)?;
    let (service, write) = match requested {
        "git-upload-pack" => ("upload-pack", false),
        "git-receive-pack" => ("receive-pack", true),
        "git-upload-archive" => ("upload-archive", false),
        _ => return Err(Error::Denied),
    };
    let path = argument
        .strip_prefix('\'')
        .and_then(|value| value.strip_suffix('\''))
        .ok_or(Error::Denied)?
        .strip_prefix('/')
        .unwrap_or_else(|| &argument[1..argument.len() - 1]);
    if path.contains(['\'', '\\', ' ']) || path.matches('/').count() != 1 {
        return Err(Error::Denied);
    }
    let (binding, repository) = path.split_once('/').ok_or(Error::Denied)?;
    let repository = repository.strip_suffix(".git").ok_or(Error::Denied)?;
    if !super::resolve::identifier(binding) || !crate::auth::valid_id(repository) {
        return Err(Error::Denied);
    }
    Ok((service, binding, repository, write))
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyClaim {
    version: u32,
    app: String,
    binding: String,
    repository: String,
    actor: String,
    actor_kind: String,
    store_generation: i64,
    nonce: String,
    expires: u64,
}

pub(super) struct PolicyEnvironment {
    pub(super) claim: String,
    pub(super) signature: String,
}

pub(super) fn policy_environment(transport: &Transport) -> Result<PolicyEnvironment> {
    if transport.actor.is_empty() {
        return Err(Error::Unauthenticated);
    }
    let claim = PolicyClaim {
        version: 2,
        app: transport.app.clone(),
        binding: transport.binding.clone(),
        repository: transport.repository_id.clone(),
        actor: transport.actor.clone(),
        actor_kind: transport.actor_kind.clone(),
        store_generation: transport.generation,
        nonce: crate::authority::random_hex()?,
        expires: unix_time()?.saturating_add(20 * 60),
    };
    let bytes = serde_json::to_vec(&claim).map_err(|_| Error::Denied)?;
    let claim = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes);
    let signature = hmac_hex(transport.identity.as_bytes(), claim.as_bytes());
    Ok(PolicyEnvironment { claim, signature })
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Protection<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) pattern: &'a str,
    pub(crate) required_role: &'a str,
    pub(crate) changes: ChangePolicy,
    pub(crate) review: ReviewPolicy,
    pub(crate) content: ContentPolicy,
    pub(crate) enabled: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ChangePolicy {
    pub(crate) force: bool,
    pub(crate) delete: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ReviewPolicy {
    pub(crate) approvals: i64,
    pub(crate) pipeline: bool,
    pub(crate) discussions: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ContentPolicy {
    pub(crate) signed_commits: bool,
    pub(crate) secret_scan: bool,
}

pub(crate) fn protect(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: Protection<'_>,
) -> Result<String> {
    if !scope.principal().has_role("account_admin")
        || !matches!(
            request.required_role,
            "" | "authenticated" | "account_admin"
        )
    {
        return Err(Error::Denied);
    }
    valid_reference_pattern(request.pattern)?;
    if !(0..=100).contains(&request.review.approvals) {
        return Err(input("Git required approvals must be 0..100"));
    }
    let config = database
        .git
        .as_ref()
        .and_then(|git| git.bindings.get(request.binding))
        .cloned()
        .ok_or(Error::Denied)?;
    let effects = BTreeSet::from([
        Effect::Read(config.resource.clone()),
        Effect::Write(config.resource.clone()),
    ]);
    let adapter_scope = Scope::new(scope.principal(), &effects);
    database.attachment(&adapter_scope, &config.resource, request.repository, true)?;
    let store = Store::open(database, grants, request.binding, false)?;
    let path = store.repository_path(request.repository)?;
    if path.canonicalize()? != path {
        return Err(Error::Denied);
    }
    store.require_repository(&path)?;
    if request.enabled {
        let approvals = i32::try_from(request.review.approvals).map_err(|_| Error::Denied)?;
        database.connection.query(
            "INSERT INTO diamond_git.protections(app,binding,repository,pattern,required_role,allow_force,allow_delete,required_approvals,require_pipeline,require_discussions,require_signed_commits,scan_secrets) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) ON CONFLICT(app,binding,repository,pattern) DO UPDATE SET required_role=EXCLUDED.required_role,allow_force=EXCLUDED.allow_force,allow_delete=EXCLUDED.allow_delete,required_approvals=EXCLUDED.required_approvals,require_pipeline=EXCLUDED.require_pipeline,require_discussions=EXCLUDED.require_discussions,require_signed_commits=EXCLUDED.require_signed_commits,scan_secrets=EXCLUDED.scan_secrets,generation=diamond_git.protections.generation+1",
            &[&database.app, &request.binding, &request.repository, &request.pattern, &request.required_role, &request.changes.force, &request.changes.delete, &approvals, &request.review.pipeline, &request.review.discussions, &request.content.signed_commits, &request.content.secret_scan],
        )?;
        Ok("protected".into())
    } else {
        let rows = database.connection.query(
            "DELETE FROM diamond_git.protections WHERE app=$1 AND binding=$2 AND repository=$3 AND pattern=$4 RETURNING pattern",
            &[&database.app, &request.binding, &request.repository, &request.pattern],
        )?;
        if rows.len() != 1 {
            return Err(Error::Denied);
        }
        Ok("unprotected".into())
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Gate<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) reference: &'a str,
    pub(crate) object: &'a str,
    pub(crate) approvals: i64,
    pub(crate) pipeline_succeeded: bool,
    pub(crate) discussions_resolved: bool,
    pub(crate) lifetime_seconds: i64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ReviewGate<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) reference: &'a str,
    pub(crate) object: &'a str,
    pub(crate) discussions_resolved: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DeployKey<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) label: &'a str,
    pub(crate) public_key: &'a str,
    pub(crate) can_write: bool,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct CertificatePrincipal<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) principal: &'a str,
    pub(crate) can_write: bool,
    pub(crate) lifetime_seconds: i64,
}

fn authorize_admin_repository(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    binding: &str,
    repository: &str,
) -> Result<()> {
    if !scope.principal().has_role("account_admin") {
        return Err(Error::Denied);
    }
    authorize_repository_write(database, scope, grants, binding, repository)
}

fn authorize_repository_write(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    binding: &str,
    repository: &str,
) -> Result<()> {
    let config = database
        .git
        .as_ref()
        .and_then(|git| git.bindings.get(binding))
        .cloned()
        .ok_or(Error::Denied)?;
    let effects = BTreeSet::from([
        Effect::Read(config.resource.clone()),
        Effect::Write(config.resource.clone()),
    ]);
    let adapter_scope = Scope::new(scope.principal(), &effects);
    database.attachment(&adapter_scope, &config.resource, repository, true)?;
    let store = Store::open(database, grants, binding, false)?;
    let path = store.repository_path(repository)?;
    if path.canonicalize()? != path {
        return Err(Error::Denied);
    }
    store.require_repository(&path)
}

pub(crate) fn set_gate(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: Gate<'_>,
) -> Result<String> {
    valid_reference(request.reference)?;
    if !valid_oid(request.object)
        || !(0..=100).contains(&request.approvals)
        || !(60..=604_800).contains(&request.lifetime_seconds)
    {
        return Err(input("invalid Git gate"));
    }
    authorize_admin_repository(database, scope, grants, request.binding, request.repository)?;
    let approvals = i32::try_from(request.approvals).map_err(|_| Error::Denied)?;
    database.connection.query(
        "INSERT INTO diamond_git.gates(app,binding,repository,reference,object,approvals,pipeline_succeeded,discussions_resolved,expires) VALUES($1,$2,$3,$4,$5,$6,$7,$8,pg_catalog.clock_timestamp()+$9::bigint*INTERVAL '1 second') ON CONFLICT(app,binding,repository,reference,object) DO UPDATE SET approvals=EXCLUDED.approvals,pipeline_succeeded=EXCLUDED.pipeline_succeeded,discussions_resolved=EXCLUDED.discussions_resolved,expires=EXCLUDED.expires,generation=diamond_git.gates.generation+1",
        &[&database.app, &request.binding, &request.repository, &request.reference, &request.object, &approvals, &request.pipeline_succeeded, &request.discussions_resolved, &request.lifetime_seconds],
    )?;
    Ok("gate-recorded".into())
}

pub(crate) fn set_review_gate(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: ReviewGate<'_>,
) -> Result<String> {
    valid_reference(request.reference)?;
    if !valid_oid(request.object) {
        return Err(input("invalid Git review gate"));
    }
    authorize_repository_write(database, scope, grants, request.binding, request.repository)?;
    database.connection.query(
        "INSERT INTO diamond_git.gates(app,binding,repository,reference,object,approvals,pipeline_succeeded,discussions_resolved,expires) VALUES($1,$2,$3,$4,$5,1,false,$6,pg_catalog.clock_timestamp()+INTERVAL '24 hours') ON CONFLICT(app,binding,repository,reference,object) DO UPDATE SET approvals=GREATEST(diamond_git.gates.approvals,1),discussions_resolved=diamond_git.gates.discussions_resolved OR EXCLUDED.discussions_resolved,expires=GREATEST(diamond_git.gates.expires,EXCLUDED.expires),generation=diamond_git.gates.generation+1",
        &[&database.app, &request.binding, &request.repository, &request.reference, &request.object, &request.discussions_resolved],
    )?;
    Ok("review-gate-recorded".into())
}

pub(crate) fn add_deploy_key(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: DeployKey<'_>,
) -> Result<String> {
    if request.label.is_empty()
        || request.label.len() > 160
        || request.label.chars().any(char::is_control)
    {
        return Err(input("invalid deploy key label"));
    }
    authorize_admin_repository(database, scope, grants, request.binding, request.repository)?;
    let (public_key, fingerprint) = super::accounts::parse_public_key(request.public_key)?;
    let rows = database.connection.query(
        "SELECT (SELECT count(*) FROM diamond_git.deploy_keys WHERE app=$1 AND binding=$2 AND repository=$3 AND NOT revoked),(SELECT count(*) FROM diamond_identity.ssh_keys WHERE app=$1 AND fingerprint=$4)",
        &[&database.app, &request.binding, &request.repository, &fingerprint],
    )?;
    let deploy_count = rows[0].try_get::<_, i64>(0).map_err(|_| Error::Denied)?;
    let account_count = rows[0].try_get::<_, i64>(1).map_err(|_| Error::Denied)?;
    if deploy_count >= 64 {
        return Err(Error::Limit("Git deploy key count"));
    }
    if account_count != 0 {
        return Err(Error::Conflict);
    }
    let id = crate::authority::random_hex()?;
    database.connection.query(
        "INSERT INTO diamond_git.deploy_keys(app,id,binding,repository,fingerprint,public_key,label,can_write) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
        &[&database.app, &id, &request.binding, &request.repository, &fingerprint, &public_key, &request.label, &request.can_write],
    )?;
    Ok(id)
}

pub(crate) fn revoke_deploy_key(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    binding: &str,
    repository: &str,
    id: &str,
) -> Result<String> {
    if !valid_identity(id) {
        return Err(input("invalid deploy key id"));
    }
    authorize_admin_repository(database, scope, grants, binding, repository)?;
    let rows = database.connection.query(
        "UPDATE diamond_git.deploy_keys SET revoked=true WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND NOT revoked RETURNING id",
        &[&database.app, &binding, &repository, &id],
    )?;
    if rows.len() != 1 {
        return Err(Error::Denied);
    }
    Ok("deploy-key-revoked".into())
}

pub(crate) fn add_certificate_principal(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: CertificatePrincipal<'_>,
) -> Result<String> {
    if !valid_certificate_principal(request.principal)
        || !(60..=2_592_000).contains(&request.lifetime_seconds)
    {
        return Err(input("invalid SSH certificate principal"));
    }
    authorize_admin_repository(database, scope, grants, request.binding, request.repository)?;
    let id = crate::authority::random_hex()?;
    database.connection.query(
        "INSERT INTO diamond_git.certificate_principals(app,id,principal,binding,repository,can_write,expires) VALUES($1,$2,$3,$4,$5,$6,pg_catalog.clock_timestamp()+$7::bigint*INTERVAL '1 second')",
        &[&database.app, &id, &request.principal, &request.binding, &request.repository, &request.can_write, &request.lifetime_seconds],
    )?;
    Ok(id)
}

pub(crate) fn revoke_certificate_principal(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    binding: &str,
    repository: &str,
    id: &str,
) -> Result<String> {
    if !valid_identity(id) {
        return Err(input("invalid SSH certificate principal id"));
    }
    authorize_admin_repository(database, scope, grants, binding, repository)?;
    let rows = database.connection.query(
        "UPDATE diamond_git.certificate_principals SET revoked=true WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND NOT revoked RETURNING id",
        &[&database.app, &binding, &repository, &id],
    )?;
    if rows.len() != 1 {
        return Err(Error::Denied);
    }
    Ok("certificate-principal-revoked".into())
}

pub(crate) fn pending_events(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    binding: &str,
    limit: i64,
) -> Result<String> {
    if !scope.principal().has_role("account_admin")
        || !(1..=100).contains(&limit)
        || !database
            .git
            .as_ref()
            .is_some_and(|git| git.bindings.contains_key(binding))
    {
        return Err(Error::Denied);
    }
    let rows = database.connection.query(
        "SELECT nonce,repository,actor_kind,actor,store_generation,updates,committed::text FROM diamond_git.push_events WHERE app=$1 AND binding=$2 AND state='committed' AND delivered IS NULL ORDER BY committed,nonce LIMIT $3",
        &[&database.app, &binding, &limit],
    )?;
    let mut events = Vec::with_capacity(rows.len());
    for row in rows {
        let updates: String = row.try_get(5).map_err(|_| Error::Denied)?;
        let updates: serde_json::Value =
            serde_json::from_str(&updates).map_err(|_| Error::Denied)?;
        events.push(serde_json::json!({
            "id":row.try_get::<_,String>(0).map_err(|_|Error::Denied)?,
            "repository":row.try_get::<_,String>(1).map_err(|_|Error::Denied)?,
            "actor_kind":row.try_get::<_,String>(2).map_err(|_|Error::Denied)?,
            "actor":row.try_get::<_,String>(3).map_err(|_|Error::Denied)?,
            "store_generation":row.try_get::<_,i64>(4).map_err(|_|Error::Denied)?,
            "updates":updates,
            "committed":row.try_get::<_,String>(6).map_err(|_|Error::Denied)?,
        }));
    }
    Ok(serde_json::Value::Array(events).to_string())
}

pub(crate) fn acknowledge_event(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    binding: &str,
    nonce: &str,
) -> Result<String> {
    if !scope.principal().has_role("account_admin")
        || !valid_identity(nonce)
        || !database
            .git
            .as_ref()
            .is_some_and(|git| git.bindings.contains_key(binding))
    {
        return Err(Error::Denied);
    }
    let rows = database.connection.query(
        "UPDATE diamond_git.push_events SET delivered=COALESCE(delivered,pg_catalog.clock_timestamp()) WHERE app=$1 AND binding=$2 AND nonce=$3 AND state='committed' RETURNING nonce",
        &[&database.app, &binding, &nonce],
    )?;
    if rows.len() != 1 {
        return Err(Error::Denied);
    }
    Ok("event-acknowledged".into())
}

pub(crate) fn pre_receive_hook() -> Result<()> {
    let (claim, updates, configuration) = hook_request("pre-receive")?;
    enforce_updates(&configuration, &claim, &updates)?;
    record_prepared_event(&configuration, &claim, &updates)
}

pub(crate) fn post_receive_hook() -> Result<()> {
    let (claim, updates, configuration) = hook_request("post-receive")?;
    let serialized = serialize_updates(&updates)?;
    commit_spool_event(&claim, &updates)?;
    let mut database = crate::host::HostRuntime::connect_worker(&configuration)?;
    if persist_committed_event(&mut database, &claim, &updates, &serialized).is_err() {
        return Err(Error::OutcomeUnknown(
            "Git refs committed but the durable push event needs reconciliation".into(),
        ));
    }
    Ok(())
}

fn persist_committed_event(
    database: &mut crate::host::HostRuntime,
    claim: &PolicyClaim,
    updates: &[RefUpdate],
    serialized: &str,
) -> Result<()> {
    database.batch("BEGIN")?;
    let result = (|| {
        let rows = database.query(
            "UPDATE diamond_git.push_events SET state='committed',committed=pg_catalog.clock_timestamp() WHERE app=$1 AND nonce=$2 AND binding=$3 AND repository=$4 AND store_generation=$5 AND updates=$6 AND state='prepared' RETURNING nonce",
            &[&claim.app, &claim.nonce, &claim.binding, &claim.repository, &claim.store_generation, &serialized],
        )?;
        if rows.len() != 1 {
            return Err(Error::Conflict);
        }
        for update in updates {
            if update.new.bytes().all(|byte| byte == b'0') {
                database.query(
                    "DELETE FROM diamond_git.ref_states WHERE app=$1 AND binding=$2 AND repository=$3 AND reference=$4",
                    &[&claim.app, &claim.binding, &claim.repository, &update.reference],
                )?;
            } else {
                database.query(
                    "INSERT INTO diamond_git.ref_states(app,binding,repository,reference,object) VALUES($1,$2,$3,$4,$5) ON CONFLICT(app,binding,repository,reference) DO UPDATE SET object=EXCLUDED.object",
                    &[&claim.app, &claim.binding, &claim.repository, &update.reference, &update.new],
                )?;
            }
        }
        Ok(())
    })();
    match result {
        Ok(()) => database.batch("COMMIT"),
        Err(error) => {
            let _rolled_back = database.batch("ROLLBACK");
            Err(error)
        }
    }
}

fn hook_request(
    expected_mode: &str,
) -> Result<(PolicyClaim, Vec<RefUpdate>, zeroize::Zeroizing<String>)> {
    if std::env::var("DIAMOND_GIT_HOOK_MODE").as_deref() != Ok(expected_mode) {
        return Err(Error::Denied);
    }
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    private_directory(&root)?;
    let identity = store_identity(&root.join(MARKER))?;
    let encoded = required_environment("DIAMOND_GIT_POLICY_CLAIM")?;
    let signature = required_environment("DIAMOND_GIT_POLICY_SIGNATURE")?;
    let expected = hmac_hex(identity.as_bytes(), encoded.as_bytes());
    if signature.len() != expected.len()
        || !bool::from(signature.as_bytes().ct_eq(expected.as_bytes()))
    {
        return Err(Error::Denied);
    }
    let claim_bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(encoded)
        .map_err(|_| Error::Denied)?;
    if claim_bytes.len() > 2048 {
        return Err(Error::Denied);
    }
    let claim: PolicyClaim =
        super::wire::strict_from_slice(&claim_bytes).map_err(|_| Error::Denied)?;
    let now = unix_time()?;
    if claim.version != 2
        || !valid_app(&claim.app)
        || !super::resolve::identifier(&claim.binding)
        || !crate::auth::valid_id(&claim.repository)
        || !valid_actor(&claim.actor)
        || !matches!(claim.actor_kind.as_str(), "user" | "deploy" | "certificate")
        || claim.store_generation <= 0
        || !valid_identity(&claim.nonce)
        || now > claim.expires
        || claim.expires.saturating_sub(now) > 20 * 60
    {
        return Err(Error::Denied);
    }
    let repository = root.join(format!(
        "{}-{}-{}.git",
        claim.app, claim.binding, claim.repository
    ));
    if std::env::current_dir()?.canonicalize()? != repository.canonicalize()? {
        return Err(Error::Denied);
    }
    let updates = read_updates(std::io::stdin().lock())?;
    let configuration =
        zeroize::Zeroizing::new(required_environment("DIAMOND_GIT_POLICY_DATABASE")?);
    Ok((claim, updates, configuration))
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct RefUpdate {
    old: String,
    new: String,
    reference: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SpoolEvent {
    claim: PolicyClaim,
    updates: Vec<RefUpdate>,
}

fn serialize_updates(updates: &[RefUpdate]) -> Result<String> {
    let serialized = serde_json::to_string(updates).map_err(|_| Error::Denied)?;
    if serialized.len() > 512 * 1024 {
        return Err(Error::Limit("Git push event"));
    }
    Ok(serialized)
}

fn record_prepared_event(
    configuration: &str,
    claim: &PolicyClaim,
    updates: &[RefUpdate],
) -> Result<()> {
    let serialized = serialize_updates(updates)?;
    let mut database = crate::host::HostRuntime::connect_worker(configuration)?;
    database.query(
        "INSERT INTO diamond_git.push_events(app,nonce,binding,repository,actor_kind,actor,store_generation,updates,state) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'prepared')",
        &[&claim.app, &claim.nonce, &claim.binding, &claim.repository, &claim.actor_kind, &claim.actor, &claim.store_generation, &serialized],
    )?;
    write_spool_event(claim, updates)?;
    Ok(())
}

fn event_directory_at(root: &Path) -> Result<PathBuf> {
    private_directory(root)?;
    let directory = root.join(".diamond-events");
    private_directory(&directory)?;
    Ok(directory)
}

fn spool_bytes(claim: &PolicyClaim, updates: &[RefUpdate]) -> Result<Vec<u8>> {
    let bytes = serde_json::to_vec(&SpoolEvent {
        claim: claim.clone(),
        updates: updates.to_vec(),
    })
    .map_err(|_| Error::Denied)?;
    if bytes.len() > 600 * 1024 {
        return Err(Error::Limit("Git push event spool"));
    }
    Ok(bytes)
}

fn write_spool_event(claim: &PolicyClaim, updates: &[RefUpdate]) -> Result<()> {
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    write_spool_event_at(&root, claim, updates)
}

fn write_spool_event_at(root: &Path, claim: &PolicyClaim, updates: &[RefUpdate]) -> Result<()> {
    let directory = event_directory_at(root)?;
    let path = directory.join(format!("{}.prepared", claim.nonce));
    let mut file = options().write(true).create_new(true).open(path)?;
    file.write_all(&spool_bytes(claim, updates)?)?;
    file.sync_all()?;
    File::open(directory)?.sync_all()?;
    Ok(())
}

fn commit_spool_event(claim: &PolicyClaim, updates: &[RefUpdate]) -> Result<()> {
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    commit_spool_event_at(&root, claim, updates)
}

fn commit_spool_event_at(root: &Path, claim: &PolicyClaim, updates: &[RefUpdate]) -> Result<()> {
    let directory = event_directory_at(root)?;
    let prepared = directory.join(format!("{}.prepared", claim.nonce));
    let committed = directory.join(format!("{}.committed", claim.nonce));
    let actual = read_private_file(&prepared, 600 * 1024)?;
    if actual != spool_bytes(claim, updates)? {
        return Err(Error::Denied);
    }
    fs::rename(prepared, committed)?;
    File::open(directory)?.sync_all()?;
    Ok(())
}

fn read_private_file(path: &Path, limit: usize) -> Result<Vec<u8>> {
    let mut file = options().read(true).open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file()
        || usize::try_from(metadata.len())
            .ok()
            .is_none_or(|size| size > limit)
    {
        return Err(Error::Denied);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o777 != 0o600 || metadata.uid() != fs::metadata("/proc/self")?.uid() {
            return Err(Error::Denied);
        }
    }
    read_bounded(&mut file, limit)
}

fn read_updates(reader: impl Read) -> Result<Vec<RefUpdate>> {
    let mut bytes = Vec::new();
    reader.take(512 * 1024 + 1).read_to_end(&mut bytes)?;
    if bytes.is_empty() || bytes.len() > 512 * 1024 || !bytes.ends_with(b"\n") {
        return Err(Error::Denied);
    }
    let text = std::str::from_utf8(&bytes).map_err(|_| Error::Denied)?;
    let mut updates = Vec::new();
    for line in text.lines() {
        if updates.len() >= 256 {
            return Err(Error::Limit("Git ref updates"));
        }
        let mut fields = line.split(' ');
        let old = fields.next().ok_or(Error::Denied)?;
        let new = fields.next().ok_or(Error::Denied)?;
        let reference = fields.next().ok_or(Error::Denied)?;
        if fields.next().is_some() || !valid_oid(old) || !valid_oid(new) || old.len() != new.len() {
            return Err(Error::Denied);
        }
        valid_reference(reference)?;
        updates.push(RefUpdate {
            old: old.into(),
            new: new.into(),
            reference: reference.into(),
        });
    }
    Ok(updates)
}

fn enforce_updates(configuration: &str, claim: &PolicyClaim, updates: &[RefUpdate]) -> Result<()> {
    let mut database = crate::host::HostRuntime::connect(configuration)?;
    let stores = database.query(
        "SELECT identity,generation FROM diamond_git.stores WHERE app=$1 AND binding=$2",
        &[&claim.app, &claim.binding],
    )?;
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    let identity = store_identity(&root.join(MARKER))?;
    if stores.len() != 1
        || stores[0]
            .try_get::<_, Option<String>>(0)
            .map_err(|_| Error::Denied)?
            .as_deref()
            != Some(identity.as_str())
        || stores[0].try_get::<_, i64>(1).map_err(|_| Error::Denied)? != claim.store_generation
    {
        return Err(Error::Denied);
    }
    let administrator = authorize_claim_actor(&mut database, claim)?;
    let rules = load_protection_rules(&mut database, claim)?;
    for update in updates {
        enforce_ref_update(&mut database, claim, update, &rules, administrator)?;
    }
    Ok(())
}

fn load_protection_rules(
    database: &mut crate::host::HostRuntime,
    claim: &PolicyClaim,
) -> Result<Vec<ProtectionRule>> {
    let rows = database.query(
        "SELECT pattern,required_role,allow_force,allow_delete,required_approvals,require_pipeline,require_discussions,require_signed_commits,scan_secrets FROM diamond_git.protections WHERE app=$1 AND binding=$2 AND repository=$3 ORDER BY pattern LIMIT 257",
        &[&claim.app, &claim.binding, &claim.repository],
    )?;
    if rows.len() > 256 {
        return Err(Error::Limit("Git protection rules"));
    }
    let mut rules = Vec::with_capacity(rows.len());
    for row in rows {
        let pattern: String = row.try_get(0).map_err(|_| Error::Denied)?;
        valid_reference_pattern(&pattern)?;
        let rule = ProtectionRule {
            pattern,
            required_role: row.try_get(1).map_err(|_| Error::Denied)?,
            changes: ChangePolicy {
                force: row.try_get(2).map_err(|_| Error::Denied)?,
                delete: row.try_get(3).map_err(|_| Error::Denied)?,
            },
            review: ReviewPolicy {
                approvals: i64::from(row.try_get::<_, i32>(4).map_err(|_| Error::Denied)?),
                pipeline: row.try_get(5).map_err(|_| Error::Denied)?,
                discussions: row.try_get(6).map_err(|_| Error::Denied)?,
            },
            content: ContentPolicy {
                signed_commits: row.try_get(7).map_err(|_| Error::Denied)?,
                secret_scan: row.try_get(8).map_err(|_| Error::Denied)?,
            },
        };
        if !matches!(
            rule.required_role.as_str(),
            "authenticated" | "account_admin"
        ) || !(0..=100).contains(&rule.review.approvals)
        {
            return Err(Error::Denied);
        }
        rules.push(rule);
    }
    Ok(rules)
}

struct ProtectionRule {
    pattern: String,
    required_role: String,
    changes: ChangePolicy,
    review: ReviewPolicy,
    content: ContentPolicy,
}

fn enforce_ref_update(
    database: &mut crate::host::HostRuntime,
    claim: &PolicyClaim,
    update: &RefUpdate,
    rules: &[ProtectionRule],
    administrator: bool,
) -> Result<()> {
    let matching = rules
        .iter()
        .filter(|rule| pattern_matches(&rule.pattern, &update.reference))
        .collect::<Vec<_>>();
    if matching.is_empty() {
        return Ok(());
    }
    if matching
        .iter()
        .any(|rule| rule.required_role == "account_admin")
        && !administrator
    {
        return Err(Error::Denied);
    }
    let zero = |object: &str| object.bytes().all(|byte| byte == b'0');
    if zero(&update.new) {
        if matching.iter().any(|rule| !rule.changes.delete) {
            return Err(Error::Denied);
        }
        return Ok(());
    }
    if !zero(&update.old)
        && matching.iter().any(|rule| !rule.changes.force)
        && !is_ancestor(&update.old, &update.new)?
    {
        return Err(Error::Denied);
    }
    let approvals = matching
        .iter()
        .map(|rule| rule.review.approvals)
        .max()
        .unwrap_or(0);
    let pipeline = matching.iter().any(|rule| rule.review.pipeline);
    let discussions = matching.iter().any(|rule| rule.review.discussions);
    if approvals > 0 || pipeline || discussions {
        enforce_gate(database, claim, update, approvals, pipeline, discussions)?;
    }
    if matching.iter().any(|rule| rule.content.signed_commits) {
        enforce_signed_commits(database, update)?;
    }
    if matching.iter().any(|rule| rule.content.secret_scan) {
        enforce_secret_scan(update)?;
    }
    Ok(())
}

fn authorize_claim_actor(
    database: &mut crate::host::HostRuntime,
    claim: &PolicyClaim,
) -> Result<bool> {
    let rows = match claim.actor_kind.as_str() {
        "user" => database.query(
            "SELECT administrator FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled",
            &[&claim.app, &claim.actor],
        )?,
        "deploy" => database.query(
            "SELECT false FROM diamond_git.deploy_keys WHERE app=$1 AND id=$2 AND binding=$3 AND repository=$4 AND can_write AND NOT revoked",
            &[&claim.app, &claim.actor, &claim.binding, &claim.repository],
        )?,
        "certificate" => database.query(
            "SELECT false FROM diamond_git.certificate_principals WHERE app=$1 AND id=$2 AND binding=$3 AND repository=$4 AND can_write AND NOT revoked AND expires>pg_catalog.clock_timestamp()",
            &[&claim.app, &claim.actor, &claim.binding, &claim.repository],
        )?,
        _ => return Err(Error::Denied),
    };
    if rows.len() != 1 {
        return Err(Error::Denied);
    }
    rows[0].try_get(0).map_err(|_| Error::Denied)
}

fn enforce_gate(
    database: &mut crate::host::HostRuntime,
    claim: &PolicyClaim,
    update: &RefUpdate,
    required_approvals: i64,
    require_pipeline: bool,
    require_discussions: bool,
) -> Result<()> {
    let rows = database.query(
        "SELECT approvals,pipeline_succeeded,discussions_resolved FROM diamond_git.gates WHERE app=$1 AND binding=$2 AND repository=$3 AND reference=$4 AND object=$5 AND expires>pg_catalog.clock_timestamp()",
        &[&claim.app, &claim.binding, &claim.repository, &update.reference, &update.new],
    )?;
    if rows.len() != 1
        || i64::from(rows[0].try_get::<_, i32>(0).map_err(|_| Error::Denied)?) < required_approvals
        || (require_pipeline && !rows[0].try_get::<_, bool>(1).map_err(|_| Error::Denied)?)
        || (require_discussions && !rows[0].try_get::<_, bool>(2).map_err(|_| Error::Denied)?)
    {
        return Err(Error::Denied);
    }
    Ok(())
}

fn introduced_commits(update: &RefUpdate) -> Result<Vec<String>> {
    let zero = |object: &str| object.bytes().all(|byte| byte == b'0');
    let mut arguments = vec![
        "rev-list".into(),
        "--reverse".into(),
        "--max-count=513".into(),
        update.new.clone().into(),
    ];
    if !zero(&update.old) {
        arguments.push(format!("^{}", update.old).into());
    }
    let output = run_hook_git(&arguments, 65_536)?;
    let mut commits = Vec::new();
    for line in text(&output)?.lines() {
        if !valid_oid(line) || line.len() != update.new.len() || commits.len() >= 512 {
            return Err(Error::Limit("Git introduced commit count"));
        }
        commits.push(line.to_owned());
    }
    Ok(commits)
}

fn enforce_signed_commits(
    database: &mut crate::host::HostRuntime,
    update: &RefUpdate,
) -> Result<()> {
    let rows = database.query(
        "SELECT u.email,k.public_key FROM diamond_identity.ssh_keys k JOIN diamond_identity.users u ON u.app=k.app AND u.username=k.username WHERE k.app=$1 AND NOT k.revoked AND k.epoch=u.epoch AND u.verified AND NOT u.disabled ORDER BY u.email,k.id LIMIT 1025",
        &[&required_claim_app()?],
    )?;
    if rows.is_empty() || rows.len() > 1024 {
        return Err(Error::Denied);
    }
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    let path = root.join(format!(
        ".diamond-signers-{}",
        crate::authority::random_hex()?
    ));
    let result = (|| {
        let mut file = options().write(true).create_new(true).open(&path)?;
        let mut written = 0_usize;
        for row in rows {
            let email: String = row.try_get(0).map_err(|_| Error::Denied)?;
            let public_key: String = row.try_get(1).map_err(|_| Error::Denied)?;
            if email.is_empty()
                || email.len() > 254
                || email
                    .bytes()
                    .any(|byte| byte.is_ascii_control() || matches!(byte, b' ' | b','))
                || public_key.len() > 512
            {
                return Err(Error::Denied);
            }
            let line = format!("{email} namespaces=\"git\" {public_key}\n");
            written = written.saturating_add(line.len());
            if written > 1024 * 1024 {
                return Err(Error::Limit("Git allowed signer data"));
            }
            file.write_all(line.as_bytes())?;
        }
        file.sync_all()?;
        let signer_path = path.to_str().ok_or(Error::Denied)?;
        for commit in introduced_commits(update)? {
            run_hook_git(
                &[
                    "-c".into(),
                    "gpg.format=ssh".into(),
                    "-c".into(),
                    format!("gpg.ssh.allowedSignersFile={signer_path}").into(),
                    "verify-commit".into(),
                    "--raw".into(),
                    commit.into(),
                ],
                16_384,
            )?;
        }
        Ok(())
    })();
    let removed = fs::remove_file(&path);
    if result.is_ok() {
        removed?;
    }
    result
}

fn required_claim_app() -> Result<String> {
    let encoded = required_environment("DIAMOND_GIT_POLICY_CLAIM")?;
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(encoded)
        .map_err(|_| Error::Denied)?;
    let claim: PolicyClaim = super::wire::strict_from_slice(&bytes).map_err(|_| Error::Denied)?;
    if !valid_app(&claim.app) {
        return Err(Error::Denied);
    }
    Ok(claim.app)
}

fn enforce_secret_scan(update: &RefUpdate) -> Result<()> {
    let mut blobs = BTreeSet::new();
    let mut total = 0_usize;
    for commit in introduced_commits(update)? {
        let output = run_hook_git(
            &[
                "diff-tree".into(),
                "--root".into(),
                "-r".into(),
                "--no-commit-id".into(),
                "--raw".into(),
                "-z".into(),
                "--no-renames".into(),
                commit.into(),
            ],
            2 * 1024 * 1024,
        )?;
        let mut fields = output.split(|byte| *byte == 0);
        while let Some(header) = fields.next() {
            if header.is_empty() {
                break;
            }
            let _path = fields.next().ok_or(Error::Denied)?;
            let header = text(header)?;
            let parts = header.split_ascii_whitespace().collect::<Vec<_>>();
            if parts.len() != 5 || !parts[0].starts_with(':') || !valid_oid(parts[3]) {
                return Err(Error::Denied);
            }
            if parts[1] != "000000" {
                blobs.insert(parts[3].to_owned());
                if blobs.len() > 256 {
                    return Err(Error::Limit("Git secret scan blob count"));
                }
            }
        }
    }
    for object in blobs {
        let kind = run_hook_git(&["cat-file".into(), "-t".into(), object.clone().into()], 32)?;
        if kind != b"blob\n" {
            continue;
        }
        let size = run_hook_git(&["cat-file".into(), "-s".into(), object.clone().into()], 32)?;
        let size = text(&size)?
            .trim()
            .parse::<usize>()
            .map_err(|_| Error::Denied)?;
        if size > 4 * 1024 * 1024 || total.saturating_add(size) > 32 * 1024 * 1024 {
            return Err(Error::Limit("Git secret scan bytes"));
        }
        total += size;
        let bytes = run_hook_git(&["cat-file".into(), "blob".into(), object.into()], size)?;
        if contains_secret(&bytes) {
            return Err(Error::Denied);
        }
    }
    Ok(())
}

fn contains_secret(bytes: &[u8]) -> bool {
    const FIXED: [&[u8]; 7] = [
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"-----BEGIN EC PRIVATE KEY-----",
        b"-----BEGIN OPENSSH PRIVATE KEY-----",
        b"github_pat_",
        b"ghp_",
        b"sk_live_",
    ];
    if FIXED
        .iter()
        .any(|needle| bytes.windows(needle.len()).any(|window| window == *needle))
    {
        return true;
    }
    bytes.windows(20).any(|window| {
        window.starts_with(b"AKIA")
            && window[4..]
                .iter()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
    })
}

fn run_hook_git(arguments: &[std::ffi::OsString], limit: usize) -> Result<Vec<u8>> {
    let git_executable = PathBuf::from(required_environment("DIAMOND_GIT_EXECUTABLE")?);
    executable(&git_executable)?;
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    private_directory(&root)?;
    let object_environment = git_object_environment()?;
    let mut command = Command::new(git_executable);
    command
        .args([
            "-c",
            "protocol.file.allow=never",
            "-c",
            "core.askPass=",
            "-c",
            "credential.helper=",
        ])
        .args(arguments)
        .env_clear()
        .env("HOME", &root)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_OBJECT_DIRECTORY", &object_environment.object_directory)
        .env(
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            &object_environment.alternate_object_directories,
        )
        .env("GIT_QUARANTINE_PATH", &object_environment.quarantine_path)
        .env("LC_ALL", "C.UTF-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|_| git_failure())?;
    let stdout = child.stdout.take().ok_or(Error::Denied)?;
    let stderr = child.stderr.take().ok_or(Error::Denied)?;
    let stdout_reader = thread::spawn(move || read_bounded(stdout, limit));
    let stderr_reader = thread::spawn(move || read_bounded(stderr, 16_384));
    let deadline = Instant::now() + COMMAND_DEADLINE;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|_| git_failure())? {
            break status;
        }
        if Instant::now() >= deadline {
            let _killed = child.kill();
            let _waited = child.wait();
            return Err(Error::Limit("Git receive policy deadline"));
        }
        thread::sleep(Duration::from_millis(5));
    };
    let stdout = stdout_reader.join().map_err(|_| git_failure())??;
    let _stderr = stderr_reader.join().map_err(|_| git_failure())??;
    if !status.success() {
        return Err(git_failure());
    }
    Ok(stdout)
}

struct GitObjectEnvironment {
    object_directory: PathBuf,
    alternate_object_directories: String,
    quarantine_path: PathBuf,
}

fn git_object_environment() -> Result<GitObjectEnvironment> {
    let repository = std::env::current_dir()?.canonicalize()?;
    let objects = repository.join("objects").canonicalize()?;
    let object_directory =
        PathBuf::from(required_environment("GIT_OBJECT_DIRECTORY")?).canonicalize()?;
    let quarantine_path =
        PathBuf::from(required_environment("GIT_QUARANTINE_PATH")?).canonicalize()?;
    let alternate_object_directories = required_environment("GIT_ALTERNATE_OBJECT_DIRECTORIES")?;
    let alternates = alternate_object_directories
        .split(':')
        .map(PathBuf::from)
        .collect::<Vec<_>>();
    if repository.parent().is_none()
        || object_directory == objects
        || !object_directory.starts_with(&objects)
        || quarantine_path != object_directory
        || alternates.len() != 1
        || alternates[0].canonicalize()? != objects
    {
        return Err(Error::Denied);
    }
    Ok(GitObjectEnvironment {
        object_directory,
        alternate_object_directories,
        quarantine_path,
    })
}

fn is_ancestor(old: &str, new: &str) -> Result<bool> {
    let git_executable = PathBuf::from(required_environment("DIAMOND_GIT_EXECUTABLE")?);
    executable(&git_executable)?;
    let root = PathBuf::from(required_environment("DIAMOND_GIT_STORE_ROOT")?);
    private_directory(&root)?;
    let object_environment = git_object_environment()?;
    let mut command = Command::new(git_executable);
    command
        .args(["merge-base", "--is-ancestor", old, new])
        .env_clear()
        .env("HOME", root)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_OBJECT_DIRECTORY", &object_environment.object_directory)
        .env(
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            &object_environment.alternate_object_directories,
        )
        .env("GIT_QUARANTINE_PATH", &object_environment.quarantine_path)
        .env("LC_ALL", "C.UTF-8")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let mut child = command.spawn().map_err(|_| git_failure())?;
    let deadline = Instant::now() + COMMAND_DEADLINE;
    loop {
        if let Some(status) = child.try_wait().map_err(|_| git_failure())? {
            return match status.code() {
                Some(0) => Ok(true),
                Some(1) => Ok(false),
                _ => Err(git_failure()),
            };
        }
        if Instant::now() >= deadline {
            let _killed = child.kill();
            let _waited = child.wait();
            return Err(Error::Limit("Git protection ancestry deadline"));
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn valid_reference(value: &str) -> Result<()> {
    if value.len() > 255
        || (!value.starts_with("refs/heads/") && !value.starts_with("refs/tags/"))
        || value.ends_with(['/', '.'])
        || value.contains("..")
        || value.contains("@{")
        || value.contains(".lock/")
        || value
            .rsplit_once('.')
            .is_some_and(|(_, extension)| extension.eq_ignore_ascii_case("lock"))
        || value
            .split('/')
            .any(|part| part.is_empty() || part.starts_with('.'))
        || value.bytes().any(|byte| {
            byte.is_ascii_control()
                || byte == b' '
                || [b'~', b'^', b':', b'?', b'*', b'[', b'\\'].contains(&byte)
        })
    {
        return Err(Error::Denied);
    }
    Ok(())
}

fn valid_reference_pattern(value: &str) -> Result<()> {
    if let Some(prefix) = value.strip_suffix('*') {
        if !prefix.ends_with('/') || prefix.contains('*') || prefix.len() >= 254 {
            return Err(Error::Denied);
        }
        return valid_reference(&format!("{prefix}x"));
    }
    if value.contains('*') {
        return Err(Error::Denied);
    }
    valid_reference(value)
}

fn pattern_matches(pattern: &str, reference: &str) -> bool {
    pattern
        .strip_suffix('*')
        .map_or(pattern == reference, |prefix| reference.starts_with(prefix))
}

fn valid_certificate_principal(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 160
        && !value.starts_with('-')
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'@' | b'+')
        })
}

fn valid_actor(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn valid_app(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && !value.starts_with('.')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn required_environment(name: &str) -> Result<String> {
    let value = std::env::var(name).map_err(|_| Error::Denied)?;
    if value.is_empty() || value.len() > 16_384 || value.bytes().any(|byte| byte == 0) {
        return Err(Error::Denied);
    }
    Ok(value)
}

fn unix_time() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_| Error::Denied)
}

fn hmac_hex(key: &[u8], message: &[u8]) -> String {
    let mut normalized = [0_u8; 64];
    if key.len() > normalized.len() {
        normalized[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        normalized[..key.len()].copy_from_slice(key);
    }
    let mut inner_key = normalized;
    let mut outer_key = normalized;
    for byte in &mut inner_key {
        *byte ^= 0x36;
    }
    for byte in &mut outer_key {
        *byte ^= 0x5c;
    }
    let mut inner = Sha256::new();
    inner.update(inner_key);
    inner.update(message);
    let inner = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(outer_key);
    outer.update(inner);
    let digest = outer.finalize();
    let mut encoded = String::with_capacity(64);
    for byte in digest {
        let _written = write!(encoded, "{byte:02x}");
    }
    encoded
}

pub(super) fn validate(
    project: &CheckedProject,
    configuration: &str,
    grants: &crate::RuntimeGrants,
) -> Result<()> {
    let Some(git) = &project.manifest.git else {
        if grants.git.is_empty() {
            return Ok(());
        }
        return Err(Error::Denied);
    };
    if grants.git.keys().collect::<BTreeSet<_>>() != git.bindings.keys().collect::<BTreeSet<_>>() {
        return Err(Error::Denied);
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    for binding in git.bindings.keys() {
        Store::open(&mut database, grants, binding, false)?;
    }
    database.finish_request(Ok(()))
}

#[derive(Clone, Copy)]
pub(crate) struct Query<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) operation: &'a str,
    pub(crate) revision: &'a str,
    pub(crate) path: &'a str,
    pub(crate) limit: i64,
    pub(crate) write: bool,
}

pub(crate) fn call(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    query: Query<'_>,
) -> Result<serde_json::Value> {
    let config = database
        .git
        .as_ref()
        .and_then(|git| git.bindings.get(query.binding))
        .cloned()
        .ok_or(Error::Denied)?;
    let mut effects = BTreeSet::from([Effect::Read(config.resource.clone())]);
    if query.write {
        effects.insert(Effect::Write(config.resource.clone()));
    }
    let adapter_scope = Scope::new(scope.principal(), &effects);
    database.attachment(
        &adapter_scope,
        &config.resource,
        query.repository,
        query.write,
    )?;
    let store = Store::open(database, grants, query.binding, false)?;
    if query.write {
        if query.operation != "create" {
            return Err(Error::Denied);
        }
        let created = store.create(query.repository)?;
        database.connection.query(
            "INSERT INTO diamond_git.repositories(app,binding,repository) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
            &[&database.app, &query.binding, &query.repository],
        )?;
        Ok(created)
    } else {
        store.query(
            query.repository,
            query.operation,
            query.revision,
            query.path,
            query.limit,
        )
    }
}

/// Exact compare-and-swap fast-forward requested by the Diamond application.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Merge<'a> {
    pub(crate) binding: &'a str,
    pub(crate) repository: &'a str,
    pub(crate) source_reference: &'a str,
    pub(crate) target_reference: &'a str,
    pub(crate) expected_source: &'a str,
    pub(crate) expected_target: &'a str,
}

pub(crate) fn merge(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    grants: &crate::RuntimeGrants,
    request: Merge<'_>,
) -> Result<String> {
    validate_merge_request(request)?;
    let config = database
        .git
        .as_ref()
        .and_then(|git| git.bindings.get(request.binding))
        .cloned()
        .ok_or(Error::Denied)?;
    let effects = BTreeSet::from([
        Effect::Read(config.resource.clone()),
        Effect::Write(config.resource.clone()),
    ]);
    let adapter_scope = Scope::new(scope.principal(), &effects);
    database.attachment(&adapter_scope, &config.resource, request.repository, true)?;
    let lock = format!("diamond-git:{}:{}", database.app, request.binding);
    database.connection.query(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
        &[&lock],
    )?;
    let store = Store::open(database, grants, request.binding, false)?;
    let repository = store.repository_path(request.repository)?;
    if repository.canonicalize()? != repository {
        return Err(Error::Denied);
    }
    store.require_repository(&repository)?;
    let source = store.resolve(&repository, request.source_reference)?;
    let target = store.resolve(&repository, request.target_reference)?;
    if source != request.expected_source
        || target != request.expected_target
        || source == target
        || store
            .run_repo(
                &repository,
                &["merge-base", "--is-ancestor", &target, &source],
                32,
            )
            .is_err()
    {
        return Err(Error::Conflict);
    }
    enforce_web_merge_rules(database, scope, &request, &source)?;
    let actor = scope.principal().id().ok_or(Error::Unauthenticated)?;
    if !valid_actor(actor) {
        return Err(Error::Denied);
    }
    let claim = PolicyClaim {
        version: 2,
        app: database.app.clone(),
        binding: request.binding.to_owned(),
        repository: request.repository.to_owned(),
        actor: actor.to_owned(),
        actor_kind: "user".into(),
        store_generation: store.generation,
        nonce: crate::authority::random_hex()?,
        expires: unix_time()?.saturating_add(20 * 60),
    };
    let update = RefUpdate {
        old: target,
        new: source.clone(),
        reference: request.target_reference.to_owned(),
    };
    write_spool_event_at(&store.root, &claim, std::slice::from_ref(&update))?;
    let changed = store.run_repo(
        &repository,
        &[
            "update-ref",
            "--no-deref",
            request.target_reference,
            &source,
            request.expected_target,
        ],
        32,
    );
    if let Err(error) = changed {
        discard_prepared_event_at(&store.root, &claim.nonce)?;
        return Err(error);
    }
    commit_spool_event_at(&store.root, &claim, std::slice::from_ref(&update)).map_err(|_| {
        Error::OutcomeUnknown("Git merge committed but event spooling failed".into())
    })?;
    persist_application_merge(database, &claim, &update).map_err(|_| {
        Error::OutcomeUnknown("Git merge committed but metadata needs reconciliation".into())
    })?;
    Ok(source)
}

fn validate_merge_request(request: Merge<'_>) -> Result<()> {
    valid_reference(request.source_reference)?;
    valid_reference(request.target_reference)?;
    if request.source_reference == request.target_reference
        || !request.source_reference.starts_with("refs/heads/")
        || !request.target_reference.starts_with("refs/heads/")
        || !valid_oid(request.expected_source)
        || !valid_oid(request.expected_target)
        || request.expected_source.len() != request.expected_target.len()
    {
        return Err(input("invalid Git merge request"));
    }
    Ok(())
}

fn enforce_web_merge_rules(
    database: &mut PostgresStore,
    scope: &Scope<'_>,
    request: &Merge<'_>,
    candidate: &str,
) -> Result<()> {
    let rows = database.connection.query(
        "SELECT pattern,required_role,required_approvals,require_pipeline,require_discussions,require_signed_commits,scan_secrets FROM diamond_git.protections WHERE app=$1 AND binding=$2 AND repository=$3 ORDER BY pattern LIMIT 257",
        &[&database.app, &request.binding, &request.repository],
    )?;
    if rows.len() > 256 {
        return Err(Error::Limit("Git protection rules"));
    }
    let mut approvals = 0_i64;
    let mut pipeline = false;
    let mut discussions = false;
    for row in rows {
        let pattern: String = row.try_get(0).map_err(|_| Error::Denied)?;
        valid_reference_pattern(&pattern)?;
        if !pattern_matches(&pattern, request.target_reference) {
            continue;
        }
        let role: String = row.try_get(1).map_err(|_| Error::Denied)?;
        if role == "account_admin" && !scope.principal().has_role("account_admin") {
            return Err(Error::Denied);
        }
        if role != "authenticated" && role != "account_admin" {
            return Err(Error::Denied);
        }
        approvals = approvals.max(i64::from(
            row.try_get::<_, i32>(2).map_err(|_| Error::Denied)?,
        ));
        pipeline |= row.try_get::<_, bool>(3).map_err(|_| Error::Denied)?;
        discussions |= row.try_get::<_, bool>(4).map_err(|_| Error::Denied)?;
        let signed = row.try_get::<_, bool>(5).map_err(|_| Error::Denied)?;
        let secrets = row.try_get::<_, bool>(6).map_err(|_| Error::Denied)?;
        if signed || secrets {
            return Err(input(
                "protected content rules require the receive-pack path for this merge",
            ));
        }
    }
    if approvals == 0 && !pipeline && !discussions {
        return Ok(());
    }
    let rows = database.connection.query(
        "SELECT approvals,pipeline_succeeded,discussions_resolved FROM diamond_git.gates WHERE app=$1 AND binding=$2 AND repository=$3 AND reference=$4 AND object=$5 AND expires>pg_catalog.clock_timestamp()",
        &[&database.app, &request.binding, &request.repository, &request.target_reference, &candidate],
    )?;
    if rows.len() != 1
        || i64::from(rows[0].try_get::<_, i32>(0).map_err(|_| Error::Denied)?) < approvals
        || (pipeline && !rows[0].try_get::<_, bool>(1).map_err(|_| Error::Denied)?)
        || (discussions && !rows[0].try_get::<_, bool>(2).map_err(|_| Error::Denied)?)
    {
        return Err(Error::Denied);
    }
    Ok(())
}

fn discard_prepared_event_at(root: &Path, nonce: &str) -> Result<()> {
    let directory = event_directory_at(root)?;
    fs::remove_file(directory.join(format!("{nonce}.prepared")))?;
    File::open(directory)?.sync_all()?;
    Ok(())
}

fn persist_application_merge(
    database: &mut PostgresStore,
    claim: &PolicyClaim,
    update: &RefUpdate,
) -> Result<()> {
    let updates = serialize_updates(std::slice::from_ref(update))?;
    let rows = database.connection.query(
        "INSERT INTO diamond_git.push_events(app,nonce,binding,repository,actor_kind,actor,store_generation,updates,state,committed) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'committed',pg_catalog.clock_timestamp()) ON CONFLICT(app,nonce) DO UPDATE SET state='committed',committed=COALESCE(diamond_git.push_events.committed,EXCLUDED.committed) WHERE diamond_git.push_events.binding=EXCLUDED.binding AND diamond_git.push_events.repository=EXCLUDED.repository AND diamond_git.push_events.actor_kind=EXCLUDED.actor_kind AND diamond_git.push_events.actor=EXCLUDED.actor AND diamond_git.push_events.store_generation=EXCLUDED.store_generation AND diamond_git.push_events.updates=EXCLUDED.updates RETURNING nonce",
        &[&claim.app, &claim.nonce, &claim.binding, &claim.repository, &claim.actor_kind, &claim.actor, &claim.store_generation, &updates],
    )?;
    if rows.len() != 1 {
        return Err(Error::Conflict);
    }
    database.connection.query(
        "INSERT INTO diamond_git.ref_states(app,binding,repository,reference,object) VALUES($1,$2,$3,$4,$5) ON CONFLICT(app,binding,repository,reference) DO UPDATE SET object=EXCLUDED.object",
        &[&claim.app, &claim.binding, &claim.repository, &update.reference, &update.new],
    )?;
    Ok(())
}

fn validate_grant(grant: &GitGrant) -> Result<()> {
    private_directory(&grant.root)?;
    executable(&grant.executable)
}

fn private_directory(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() || path.canonicalize()? != path {
        return Err(input("Git store must be a canonical private directory"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o777 != 0o700 || metadata.uid() != fs::metadata("/proc/self")?.uid() {
            return Err(input(
                "Git store directory must be process-owned with mode 0700",
            ));
        }
    }
    Ok(())
}

fn executable(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || path.canonicalize()? != path {
        return Err(input("Git executable must be a canonical regular file"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o022 != 0 || metadata.mode() & 0o111 == 0 {
            return Err(input(
                "Git executable must be executable and not group/world writable",
            ));
        }
    }
    Ok(())
}

fn validate_hook(root: &Path, hook_name: &str) -> Result<()> {
    if !matches!(hook_name, "pre-receive" | "post-receive") {
        return Err(Error::Denied);
    }
    let directory = root.join(".diamond-hooks");
    private_directory(&directory)?;
    let hook = directory.join(hook_name);
    let metadata = fs::symlink_metadata(&hook)?;
    if !metadata.is_file() || hook.canonicalize()? != hook {
        return Err(input("Git policy hook must be a canonical regular file"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o777 != 0o500 || metadata.uid() != fs::metadata("/proc/self")?.uid() {
            return Err(input(
                "Git policy hook must be process-owned with mode 0500",
            ));
        }
    }
    let mut actual = String::new();
    options()
        .read(true)
        .open(&hook)?
        .take(4097)
        .read_to_string(&mut actual)?;
    if actual.len() > 4096 || actual != hook_source(hook_name)? {
        return Err(input(
            "Git policy hook does not match this Diamond executable; initialize Git again",
        ));
    }
    Ok(())
}

fn hook_source(mode: &str) -> Result<String> {
    if !matches!(mode, "pre-receive" | "post-receive") {
        return Err(Error::Denied);
    }
    let executable_path = std::env::current_exe()?.canonicalize()?;
    executable(&executable_path)?;
    let executable_path = executable_path.to_str().ok_or(Error::Denied)?;
    if executable_path.chars().any(char::is_control) {
        return Err(Error::Denied);
    }
    let quoted = executable_path.replace('\'', "'\\''");
    Ok(format!(
        "#!/bin/sh\nDIAMOND_GIT_HOOK_MODE='{mode}' exec '{quoted}' \"$@\"\n"
    ))
}

fn store_identity(marker: &Path) -> Result<String> {
    let mut file = options().read(true).open(marker)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() != 64 {
        return Err(Error::Denied);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o777 != 0o600 || metadata.uid() != fs::metadata("/proc/self")?.uid() {
            return Err(Error::Denied);
        }
    }
    let mut identity = String::new();
    Read::by_ref(&mut file)
        .take(65)
        .read_to_string(&mut identity)?;
    if !valid_identity(&identity) {
        return Err(Error::Denied);
    }
    Ok(identity)
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

fn valid_identity(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_revision(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || value.starts_with('-')
        || value
            .bytes()
            .any(|byte| byte == 0 || byte.is_ascii_control())
    {
        return Err(Error::Denied);
    }
    Ok(())
}

fn valid_subpath(value: &str) -> Result<()> {
    if value.len() > 4096
        || value.starts_with('/')
        || value.contains(['\\', '\0'])
        || value.chars().any(char::is_control)
        || value
            .split('/')
            .any(|segment| matches!(segment, "." | ".."))
    {
        return Err(Error::Denied);
    }
    Ok(())
}

fn valid_oid(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn read_bounded(mut reader: impl Read, limit: usize) -> Result<Vec<u8>> {
    let mut bytes = Vec::with_capacity(limit.min(65_536));
    let mut chunk = [0_u8; 8192];
    loop {
        let read = reader.read(&mut chunk)?;
        if read == 0 {
            return Ok(bytes);
        }
        if bytes.len().saturating_add(read) > limit {
            return Err(Error::Limit("Git command output"));
        }
        bytes.extend_from_slice(&chunk[..read]);
    }
}

fn text(bytes: &[u8]) -> Result<&str> {
    std::str::from_utf8(bytes).map_err(|_| input("Git output is not UTF-8"))
}

fn git_failure() -> Error {
    Error::Propagation("Git operation failed.".into())
}

pub(super) fn contracts() -> serde_json::Value {
    serde_json::json!({
        "target":"server",
        "native_grant":"git-v2 private repository root and fixed Git executable bound to PostgreSQL identity and fenced store generation",
        "operations":["create","head","tree","blob","history","diff","review-gate","fast-forward-merge","protect","unprotect","gate","deploy-key","ssh-certificate","push-events","smart-http","forced-command-ssh","archive-ssh","reconcile","promote"],
        "effects":"binding-specific GitRead/GitWrite/GitReview/GitAdmin plus current repository resource policy",
        "maximum_blob_preview_bytes":BLOB_LIMIT,
        "maximum_output_bytes":OUTPUT_LIMIT,
        "command_deadline_ms":COMMAND_DEADLINE.as_millis(),
        "process":"fixed Git executable and arguments, no client-controlled shell, cleared environment with validated Git quarantine paths only, disabled credentials and file protocol, exact runtime-owned pre/post-receive hooks",
        "transaction":"repository reads are external to PostgreSQL; create is idempotent; web merges use an exact fast-forward compare-and-swap, commit Git refs authoritatively, and spool durable events; review gates are exact database writes and reconciliation rebuilds the fenced ref inventory",
        "replay":"no automatic replay",
        "failure":"redacted policy, validation, resource, repository or Git failure"
    })
}
