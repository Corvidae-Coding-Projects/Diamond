//! Policy-bound access to installed Git without a shell or managed filesystem API.

use super::{CheckedProject, Manifest, artifact, input, storage::PostgresStore};
use crate::{
    Error,
    authority::{Effect, Scope},
    error::Result,
    hir::CheckedProgram,
    integrations::GitGrant,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

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
        if self.profile != "git-v1"
            || manifest.identity.is_none()
            || program.storage.is_none()
            || self.bindings.is_empty()
            || self.bindings.len() > 16
        {
            return Err(input(
                "Git requires git-v1, accounts and relational storage",
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
CREATE TABLE IF NOT EXISTS diamond_git.stores(app TEXT NOT NULL REFERENCES diamond_w2.apps(app), binding TEXT NOT NULL, shape TEXT NOT NULL, identity TEXT, PRIMARY KEY(app,binding));";

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
            "SELECT identity FROM diamond_git.stores WHERE app=$1 AND binding=$2",
            &[&database.app, &binding],
        )?;
        if rows.len() != 1
            || rows[0]
                .try_get::<_, Option<String>>(0)
                .map_err(|_| Error::Denied)?
                .as_deref()
                != Some(&identity)
        {
            return Err(input("Git store identity mismatch"));
        }
        Ok(Self {
            root: grant.root.clone(),
            executable: grant.executable.clone(),
            identity,
            app: database.app.clone(),
            binding: binding.into(),
        })
    }

    fn verify(&self) -> Result<()> {
        private_directory(&self.root)?;
        executable(&self.executable)?;
        if store_identity(&self.root.join(MARKER))? != self.identity {
            return Err(input("Git store identity changed"));
        }
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
            ("core.hooksPath", PathBuf::from("/dev/null")),
            ("receive.fsckObjects", PathBuf::from("true")),
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
        if settings.len() != 8
            || settings.get("core.repositoryformatversion") != Some(&"0")
            || !matches!(settings.get("core.filemode"), Some(&"true" | &"false"))
            || settings.get("core.bare") != Some(&"true")
            || settings.get("core.hookspath") != Some(&"/dev/null")
            || settings.get("receive.fsckobjects") != Some(&"true")
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
}

#[derive(Clone, Copy)]
pub(super) struct TransportRequest<'a> {
    pub(super) token: Option<&'a str>,
    pub(super) binding: &'a str,
    pub(super) repository: &'a str,
    pub(super) write: bool,
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
        Ok(Transport {
            executable: store.executable,
            root: store.root,
            repository,
        })
    })();
    database.finish_request(result)
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
        store.create(query.repository)
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
        "native_grant":"git-v1 private repository root and fixed Git executable bound to PostgreSQL identity",
        "operations":["create","head","tree","blob","history","diff"],
        "effects":"binding-specific GitRead/GitWrite plus current repository resource policy",
        "maximum_blob_preview_bytes":BLOB_LIMIT,
        "maximum_output_bytes":OUTPUT_LIMIT,
        "command_deadline_ms":COMMAND_DEADLINE.as_millis(),
        "process":"fixed Git executable and arguments, no shell, cleared environment, disabled credentials and file protocol",
        "transaction":"repository reads are external to PostgreSQL; create is idempotent and requires an existing authorized repository record",
        "replay":"no automatic replay",
        "failure":"redacted policy, validation, resource, repository or Git failure"
    })
}
