//! Quiesced encrypted snapshots and isolated, paused restore.

use super::{CheckedProject, Error, Path, PostgresStore, Result, guard, input};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::{
    collections::BTreeMap,
    ffi::{OsStr, OsString},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::{
        ffi::{OsStrExt as _, OsStringExt as _},
        fs::{DirBuilderExt as _, MetadataExt as _, OpenOptionsExt as _, PermissionsExt as _},
    },
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

const MAX_DATABASE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_RUNTIME_FILE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_RUNTIME_BYTES: u64 = 128 * 1024 * 1024 * 1024;
const MAX_RUNTIME_ENTRIES: usize = 250_000;

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    bytes: u64,
    sha256: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RuntimeEntry {
    root: String,
    path: Vec<String>,
    directory: bool,
    mode: u32,
    bytes: u64,
    sha256: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    version: u32,
    app: String,
    contract: String,
    snapshot_unix_seconds: u64,
    database: Entry,
    roots: Vec<String>,
    runtime: Vec<RuntimeEntry>,
}

#[derive(Debug)]
struct SourceEntry {
    root: String,
    path: Vec<String>,
    source: PathBuf,
    directory: bool,
    mode: u32,
}

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _removed = fs::remove_dir_all(&self.0);
    }
}

fn private_new(path: &Path) -> Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.mode(0o700).create(path)?;
    Ok(())
}

fn private_root(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !path.is_absolute()
        || !metadata.is_dir()
        || metadata.mode() & 0o777 != 0o700
        || metadata.uid() != fs::metadata("/proc/self")?.uid()
        || path.canonicalize()? != path
    {
        return Err(input(
            "runtime backup roots must be canonical process-owned 0700 directories",
        ));
    }
    Ok(())
}

fn regular(path: &Path, maximum: u64) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() > maximum {
        return Err(input("invalid or oversized backup file"));
    }
    Ok(())
}

fn copy_hashed(
    input: &mut File,
    mut output: Option<&mut File>,
    maximum: u64,
    digest: &mut Sha256,
) -> Result<u64> {
    let mut buffer = vec![0_u8; 65_536].into_boxed_slice();
    let mut bytes = 0_u64;
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        bytes = bytes
            .checked_add(u64::try_from(read).map_err(|_| Error::Denied)?)
            .ok_or(Error::Denied)?;
        if bytes > maximum {
            return Err(Error::Limit("backup runtime bytes"));
        }
        digest.update(&buffer[..read]);
        if let Some(file) = output.as_deref_mut() {
            file.write_all(&buffer[..read])?;
        }
    }
    Ok(bytes)
}

fn digest(path: &Path, maximum: u64) -> Result<Entry> {
    regular(path, maximum)?;
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let bytes = copy_hashed(&mut file, None, maximum, &mut digest)?;
    Ok(Entry {
        bytes,
        sha256: super::super::files::hex(&digest.finalize()),
    })
}

fn run(mut command: Command, key: Option<&str>) -> Result<()> {
    command.stderr(Stdio::null()).stdout(Stdio::null());
    if key.is_some() {
        command.stdin(Stdio::piped());
    } else {
        command.stdin(Stdio::null());
    }
    let mut child = command.spawn().map_err(|_| {
        input("backup dependency could not start; install PostgreSQL client tools and GnuPG")
    })?;
    if let Some(key) = key {
        let mut stdin = child.stdin.take().ok_or(Error::Denied)?;
        if stdin
            .write_all(key.as_bytes())
            .and_then(|()| stdin.write_all(b"\n"))
            .is_err()
        {
            let _killed = child.kill();
            let _joined = child.wait();
            return Err(Error::Denied);
        }
    }
    let deadline = Instant::now() + Duration::from_hours(1);
    loop {
        if let Some(status) = child.try_wait()? {
            return if status.success() {
                Ok(())
            } else {
                Err(input(
                    "backup command failed; details suppressed to protect secrets",
                ))
            };
        }
        if Instant::now() >= deadline {
            let _killed = child.kill();
            let _joined = child.wait();
            return Err(Error::Limit("backup command deadline"));
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn pg(program: &str, configuration: &str) -> Result<Command> {
    let config = configuration
        .parse::<tokio_postgres::Config>()
        .map_err(|_| Error::Denied)?;
    let [tokio_postgres::config::Host::Unix(host)] = config.get_hosts() else {
        return Err(Error::Denied);
    };
    let mut command = Command::new(program);
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("LC_ALL", "C")
        .env("PGHOST", host)
        .env(
            "PGPORT",
            config
                .get_ports()
                .first()
                .copied()
                .unwrap_or(5432)
                .to_string(),
        )
        .env("PGUSER", config.get_user().ok_or(Error::Denied)?)
        .env("PGDATABASE", config.get_dbname().ok_or(Error::Denied)?)
        .env("PGCONNECT_TIMEOUT", "5");
    if let Some(password) = config.get_password() {
        command.env(
            "PGPASSWORD",
            std::str::from_utf8(password).map_err(|_| Error::Denied)?,
        );
    }
    Ok(command)
}

fn crypt(input_path: &Path, output_path: &Path, key: &str, decrypt: bool) -> Result<()> {
    if key.len() < 24 || key.len() > 1024 || key.contains(['\r', '\n']) {
        return Err(input(
            "backup passphrase must contain 24..1024 bytes without newlines",
        ));
    }
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(output_path)?;
    drop(file);
    let mut command = Command::new("/usr/bin/gpg");
    command
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .args([
            "--no-options",
            "--batch",
            "--yes",
            "--no-symkey-cache",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--output",
        ])
        .arg(output_path);
    let home = output_path.parent().ok_or(Error::Denied)?.join("gpg-home");
    if !home.exists() {
        private_new(&home)?;
    }
    command.arg("--homedir").arg(&home);
    if decrypt {
        command.arg("--decrypt");
    } else {
        command.args(["--symmetric", "--cipher-algo", "AES256"]);
    }
    command.arg(input_path);
    run(command, Some(key))
}

fn runtime_roots(runtime: &crate::RuntimeGrants) -> Result<BTreeMap<String, PathBuf>> {
    let mut roots = BTreeMap::new();
    if let Some(root) = &runtime.objects {
        roots.insert("objects".to_owned(), root.clone());
    }
    for (binding, grant) in &runtime.git {
        roots.insert(format!("git:{binding}"), grant.root.clone());
    }
    for (binding, grant) in &runtime.delivery {
        roots.insert(format!("delivery:{binding}"), grant.root.clone());
    }
    for (binding, grant) in &runtime.operations {
        roots.insert(format!("operations:{binding}"), grant.root.clone());
    }
    if roots.is_empty() {
        return Err(input(
            "backup requires at least one runtime filesystem root",
        ));
    }
    for root in roots.values() {
        private_root(root)?;
    }
    let paths = roots.values().collect::<Vec<_>>();
    for (index, left) in paths.iter().enumerate() {
        for right in paths.iter().skip(index + 1) {
            if left.starts_with(right) || right.starts_with(left) {
                return Err(input("runtime backup roots must not overlap"));
            }
        }
    }
    Ok(roots)
}

fn encode_component(component: &OsStr) -> String {
    let bytes = component.as_bytes();
    let mut encoded = String::with_capacity(bytes.len().saturating_mul(2));
    for byte in bytes {
        use std::fmt::Write as _;
        let _written = write!(encoded, "{byte:02x}");
    }
    encoded
}

fn decode_component(encoded: &str) -> Result<OsString> {
    if encoded.is_empty()
        || encoded.len() > 510
        || !encoded.len().is_multiple_of(2)
        || !encoded.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(Error::Denied);
    }
    let mut bytes = Vec::with_capacity(encoded.len() / 2);
    for pair in encoded.as_bytes().chunks_exact(2) {
        let text = std::str::from_utf8(pair).map_err(|_| Error::Denied)?;
        bytes.push(u8::from_str_radix(text, 16).map_err(|_| Error::Denied)?);
    }
    if bytes.contains(&0) || bytes.contains(&b'/') || matches!(bytes.as_slice(), b"." | b"..") {
        return Err(Error::Denied);
    }
    Ok(OsString::from_vec(bytes))
}

fn collect_runtime(roots: &BTreeMap<String, PathBuf>) -> Result<Vec<SourceEntry>> {
    let owner = fs::metadata("/proc/self")?.uid();
    let mut entries = Vec::new();
    for (label, root) in roots {
        let mut pending = vec![root.clone()];
        while let Some(directory) = pending.pop() {
            let mut children = fs::read_dir(&directory)?.collect::<std::io::Result<Vec<_>>>()?;
            children.sort_by_key(fs::DirEntry::file_name);
            for child in children.into_iter().rev() {
                if entries.len() >= MAX_RUNTIME_ENTRIES {
                    return Err(Error::Limit("backup runtime entry count"));
                }
                let metadata = fs::symlink_metadata(child.path())?;
                if metadata.uid() != owner || metadata.mode() & 0o7000 != 0 {
                    return Err(input("unsafe runtime backup entry"));
                }
                let relative = child
                    .path()
                    .strip_prefix(root)
                    .map_err(|_| Error::Denied)?
                    .components()
                    .map(|component| encode_component(component.as_os_str()))
                    .collect::<Vec<_>>();
                let directory_entry = metadata.is_dir();
                if !directory_entry && !metadata.is_file() {
                    return Err(input("runtime backup rejects links and special files"));
                }
                if metadata.is_file() && metadata.len() > MAX_RUNTIME_FILE_BYTES {
                    return Err(Error::Limit("backup runtime file"));
                }
                entries.push(SourceEntry {
                    root: label.clone(),
                    path: relative,
                    source: child.path(),
                    directory: directory_entry,
                    mode: metadata.mode() & 0o777,
                });
                if directory_entry {
                    pending.push(child.path());
                }
            }
        }
    }
    entries.sort_by(|left, right| (&left.root, &left.path).cmp(&(&right.root, &right.path)));
    Ok(entries)
}

fn write_runtime_bundle(entries: &[SourceEntry], path: &Path) -> Result<Vec<RuntimeEntry>> {
    let mut bundle = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    let mut total = 0_u64;
    let mut manifest = Vec::with_capacity(entries.len());
    for entry in entries {
        if entry.directory {
            manifest.push(RuntimeEntry {
                root: entry.root.clone(),
                path: entry.path.clone(),
                directory: true,
                mode: entry.mode,
                bytes: 0,
                sha256: String::new(),
            });
            continue;
        }
        let before = fs::symlink_metadata(&entry.source)?;
        let mut options = OpenOptions::new();
        options
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        let mut source = options.open(&entry.source)?;
        let mut hasher = Sha256::new();
        let remaining = MAX_RUNTIME_BYTES.checked_sub(total).ok_or(Error::Denied)?;
        let bytes = copy_hashed(
            &mut source,
            Some(&mut bundle),
            remaining.min(MAX_RUNTIME_FILE_BYTES),
            &mut hasher,
        )?;
        let after = source.metadata()?;
        if before.len() != bytes
            || before.len() != after.len()
            || before.mode() != after.mode()
            || before.modified()? != after.modified()?
        {
            return Err(input("runtime file changed during quiesced backup"));
        }
        total = total.checked_add(bytes).ok_or(Error::Denied)?;
        manifest.push(RuntimeEntry {
            root: entry.root.clone(),
            path: entry.path.clone(),
            directory: false,
            mode: entry.mode,
            bytes,
            sha256: super::super::files::hex(&hasher.finalize()),
        });
    }
    bundle.sync_all()?;
    Ok(manifest)
}

fn validate_manifest(
    manifest: &Manifest,
    project: &CheckedProject,
    roots: &BTreeMap<String, PathBuf>,
) -> Result<()> {
    let expected_roots = roots.keys().cloned().collect::<Vec<_>>();
    if manifest.version != 2
        || manifest.app != project.manifest.name
        || manifest.contract != super::super::artifact::contract_id(project)?
        || manifest.roots != expected_roots
        || manifest.runtime.len() > MAX_RUNTIME_ENTRIES
        || manifest.database.bytes > MAX_DATABASE_BYTES
        || !valid_sha256(&manifest.database.sha256)
    {
        return Err(input("backup contract mismatch"));
    }
    let mut prior: Option<(&str, &[String])> = None;
    let mut total = 0_u64;
    for entry in &manifest.runtime {
        if !roots.contains_key(&entry.root)
            || entry.path.is_empty()
            || entry.path.len() > 128
            || entry.mode & !0o777 != 0
            || entry
                .path
                .iter()
                .any(|part| decode_component(part).is_err())
            || entry.directory && (entry.bytes != 0 || !entry.sha256.is_empty())
            || !entry.directory
                && (entry.bytes > MAX_RUNTIME_FILE_BYTES || !valid_sha256(&entry.sha256))
        {
            return Err(Error::Denied);
        }
        if prior.is_some_and(|value| value >= (entry.root.as_str(), entry.path.as_slice())) {
            return Err(Error::Denied);
        }
        prior = Some((&entry.root, &entry.path));
        total = total.checked_add(entry.bytes).ok_or(Error::Denied)?;
        if total > MAX_RUNTIME_BYTES {
            return Err(Error::Limit("backup runtime bytes"));
        }
    }
    Ok(())
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn verify_runtime_bundle(path: &Path, entries: &[RuntimeEntry]) -> Result<()> {
    regular(path, MAX_RUNTIME_BYTES)?;
    let mut bundle = File::open(path)?;
    let mut buffer = vec![0_u8; 65_536].into_boxed_slice();
    for entry in entries.iter().filter(|entry| !entry.directory) {
        let mut remaining = entry.bytes;
        let mut hasher = Sha256::new();
        while remaining > 0 {
            let requested = usize::try_from(
                remaining.min(u64::try_from(buffer.len()).map_err(|_| Error::Denied)?),
            )
            .map_err(|_| Error::Denied)?;
            let read = bundle.read(&mut buffer[..requested])?;
            if read == 0 {
                return Err(input("truncated runtime backup bundle"));
            }
            hasher.update(&buffer[..read]);
            remaining -= u64::try_from(read).map_err(|_| Error::Denied)?;
        }
        if super::super::files::hex(&hasher.finalize()) != entry.sha256 {
            return Err(input("backup hash mismatch"));
        }
    }
    if bundle.read(&mut buffer[..1])? != 0 {
        return Err(input("runtime backup bundle has trailing data"));
    }
    Ok(())
}

fn destination_path(root: &Path, encoded: &[String]) -> Result<PathBuf> {
    let mut path = root.to_path_buf();
    for component in encoded {
        path.push(decode_component(component)?);
    }
    Ok(path)
}

fn restore_runtime_bundle(
    path: &Path,
    entries: &[RuntimeEntry],
    roots: &BTreeMap<String, PathBuf>,
) -> Result<()> {
    let mut bundle = File::open(path)?;
    let mut directories = Vec::new();
    for entry in entries {
        let root = roots.get(&entry.root).ok_or(Error::Denied)?;
        let target = destination_path(root, &entry.path)?;
        if entry.directory {
            let mut builder = fs::DirBuilder::new();
            builder.mode(0o700).create(&target)?;
            directories.push((target, entry.mode));
            continue;
        }
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&target)?;
        let mut remaining = entry.bytes;
        let mut buffer = vec![0_u8; 65_536].into_boxed_slice();
        while remaining > 0 {
            let requested = usize::try_from(
                remaining.min(u64::try_from(buffer.len()).map_err(|_| Error::Denied)?),
            )
            .map_err(|_| Error::Denied)?;
            bundle.read_exact(&mut buffer[..requested])?;
            output.write_all(&buffer[..requested])?;
            remaining -= u64::try_from(requested).map_err(|_| Error::Denied)?;
        }
        output.sync_all()?;
        fs::set_permissions(&target, fs::Permissions::from_mode(entry.mode))?;
    }
    for (directory, mode) in directories.into_iter().rev() {
        fs::set_permissions(directory, fs::Permissions::from_mode(mode))?;
    }
    for root in roots.values() {
        File::open(root)?.sync_all()?;
    }
    Ok(())
}

fn validate_adapters(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
) -> Result<()> {
    if project.manifest.files.is_some() {
        let _objects = super::super::files::Store::open(project, configuration, runtime, false)?;
    }
    super::super::git::validate(project, configuration, runtime)?;
    super::super::delivery::validate(project, configuration, runtime)?;
    super::super::operations::validate(project, configuration, runtime)
}

fn set_jobs_paused(project: &CheckedProject, configuration: &str, paused: bool) -> Result<()> {
    if project.manifest.jobs.is_empty() && project.manifest.events.is_empty() {
        return Ok(());
    }
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    database.connection.query(
        "UPDATE diamond_w7.apps SET paused=$2 WHERE app=$1",
        &[&database.app, &paused],
    )?;
    database.finish_request(Ok(()))
}

fn restore_database(configuration: &str, dump: &Path) -> Result<()> {
    let config = configuration
        .parse::<tokio_postgres::Config>()
        .map_err(|_| Error::Denied)?;
    let mut command = pg("/usr/bin/pg_restore", configuration)?;
    command
        .args([
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "--dbname",
        ])
        .arg(config.get_dbname().ok_or(Error::Denied)?)
        .arg(dump);
    run(command, None)
}

fn revoke_restored_authority(project: &CheckedProject, configuration: &str) -> Result<()> {
    let mut database = PostgresStore::open_control(project, configuration, None)?;
    database.connection.query(
        "UPDATE diamond_identity.users SET epoch=epoch+1 WHERE app=$1",
        &[&database.app],
    )?;
    for table in ["sessions", "credentials", "challenges"] {
        database.connection.query(
            &format!("DELETE FROM diamond_identity.{table} WHERE app=$1"),
            &[&database.app],
        )?;
    }
    if !project.manifest.jobs.is_empty() || !project.manifest.events.is_empty() {
        database.connection.query(
            "UPDATE diamond_w7.apps SET paused=true WHERE app=$1",
            &[&database.app],
        )?;
    }
    database.finish_request(Ok(()))
}

impl CheckedProject {
    /// Create a private directory containing an AES256-encrypted quiesced snapshot.
    /// Includes `PostgreSQL` plus every declared shared-object, Git, delivery, and operations root.
    /// The passphrase is supplied separately and is never written to the snapshot.
    ///
    /// # Errors
    /// Rejects active instances, missing grants, overlapping roots, unsafe entries, or failed commands.
    ///
    /// ```no_run
    /// # fn demo(p:&diamond::CheckedProject)->Result<(),diamond::Error> {
    /// let grants=diamond::RuntimeGrants::from_json(r#"{"objects":"/private/objects"}"#)?;
    /// p.backup_project("host=/tmp/pg user=app dbname=app",&grants,std::path::Path::new("/private/new-backup"),"synthetic-example-passphrase-123")?;
    /// # Ok(()) }
    /// ```
    pub fn backup_project(
        &self,
        configuration: &str,
        runtime: &crate::RuntimeGrants,
        destination: &Path,
        key: &str,
    ) -> Result<String> {
        super::super::synchronous_context()?;
        let started = Instant::now();
        let _guard = guard(self, configuration, true)?;
        validate_adapters(self, configuration, runtime)?;
        let roots = runtime_roots(runtime)?;
        let mut database = PostgresStore::open_control(self, configuration, None)?;
        let count = database
            .connection
            .query("SELECT COUNT(*) FROM diamond_w2.apps", &[])?[0]
            .try_get::<_, i64>(0)
            .map_err(|_| Error::Denied)?;
        if count != 1 {
            return Err(input(
                "snapshot profile requires one application per database",
            ));
        }
        let was_paused = if self.manifest.jobs.is_empty() && self.manifest.events.is_empty() {
            false
        } else {
            database.connection.query(
                "SELECT paused FROM diamond_w7.apps WHERE app=$1",
                &[&database.app],
            )?[0]
                .try_get(0)
                .map_err(|_| Error::Denied)?
        };
        database.finish_request(Ok(()))?;
        private_new(destination)?;
        let temporary = destination.join("plaintext-work");
        private_new(&temporary)?;
        let scratch = Scratch(temporary);
        let dump = scratch.0.join("database.dump");
        let mut command = pg("/usr/bin/pg_dump", configuration)?;
        command
            .args(["--format=custom", "--no-owner", "--no-acl", "--file"])
            .arg(&dump);
        set_jobs_paused(self, configuration, true)?;
        let dump_result = run(command, None);
        let resume_result = set_jobs_paused(self, configuration, was_paused);
        dump_result?;
        resume_result?;
        let database_entry = digest(&dump, MAX_DATABASE_BYTES)?;
        let source_entries = collect_runtime(&roots)?;
        let runtime_plain = scratch.0.join("runtime.data");
        let runtime_entries = write_runtime_bundle(&source_entries, &runtime_plain)?;
        crypt(&dump, &destination.join("database.dump.gpg"), key, false)?;
        crypt(
            &runtime_plain,
            &destination.join("runtime.data.gpg"),
            key,
            false,
        )?;
        let manifest = Manifest {
            version: 2,
            app: self.manifest.name.clone(),
            contract: super::super::artifact::contract_id(self)?,
            snapshot_unix_seconds: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| Error::Denied)?
                .as_secs(),
            database: database_entry,
            roots: roots.keys().cloned().collect(),
            runtime: runtime_entries,
        };
        let plain_manifest = scratch.0.join("manifest.json");
        fs::write(
            &plain_manifest,
            serde_json::to_vec_pretty(&manifest).map_err(|_| Error::Denied)?,
        )?;
        crypt(
            &plain_manifest,
            &destination.join("manifest.json.gpg"),
            key,
            false,
        )?;
        drop(scratch);
        let _removed = fs::remove_dir_all(destination.join("gpg-home"));
        File::open(destination)?.sync_all()?;
        Ok(serde_json::json!({
            "snapshot":"encrypted",
            "database_files":1,
            "runtime_entries":manifest.runtime.len(),
            "runtime_roots":manifest.roots.len(),
            "elapsed_ms":started.elapsed().as_millis(),
            "consistency":"quiesced",
            "jobs_recovery":"paused until explicit resume or cancel",
            "credentials_recovery":"sessions,API credentials,challenges revoked"
        })
        .to_string())
    }

    /// Restore into an empty isolated database and empty private runtime roots.
    /// Verifies every decrypted hash before database writes and revokes credentials before returning.
    /// Retain the isolated target for inspection if a later step fails; do not route users to it.
    ///
    /// # Errors
    /// Rejects active instances, nonempty targets, wrong keys/contracts, tampering, or failed commands.
    ///
    /// ```no_run
    /// # fn demo(p:&diamond::CheckedProject)->Result<(),diamond::Error> {
    /// let grants=diamond::RuntimeGrants::from_json(r#"{"objects":"/private/restored-objects"}"#)?;
    /// p.restore_project("host=/tmp/pg user=app dbname=isolated",&grants,std::path::Path::new("/private/backup"),"synthetic-example-passphrase-123")?;
    /// # Ok(()) }
    /// ```
    pub fn restore_project(
        &self,
        configuration: &str,
        runtime: &crate::RuntimeGrants,
        source: &Path,
        key: &str,
    ) -> Result<String> {
        super::super::synchronous_context()?;
        let started = Instant::now();
        let roots = runtime_roots(runtime)?;
        if roots
            .values()
            .any(|root| fs::read_dir(root).is_ok_and(|mut entries| entries.next().is_some()))
        {
            return Err(input("restore requires empty runtime filesystem roots"));
        }
        let mut guard = guard(self, configuration, true)?;
        let count=guard.query("SELECT COUNT(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'", &[])?[0].try_get::<_,i64>(0).map_err(|_|Error::Denied)?;
        if count != 0 {
            return Err(input("restore requires a separate empty database"));
        }
        let scratch_path = std::env::temp_dir().join(format!(
            "diamond-restore-{}",
            crate::authority::random_hex()?
        ));
        private_new(&scratch_path)?;
        let scratch = Scratch(scratch_path);
        regular(&source.join("manifest.json.gpg"), 16 * 1024 * 1024)?;
        crypt(
            &source.join("manifest.json.gpg"),
            &scratch.0.join("manifest.json"),
            key,
            true,
        )?;
        regular(&scratch.0.join("manifest.json"), 8 * 1024 * 1024)?;
        let mut manifest_bytes = Vec::new();
        File::open(scratch.0.join("manifest.json"))?
            .take(8 * 1024 * 1024 + 1)
            .read_to_end(&mut manifest_bytes)?;
        if manifest_bytes.len() > 8 * 1024 * 1024 {
            return Err(Error::Limit("backup manifest"));
        }
        let manifest: Manifest =
            super::super::wire::strict_from_slice(&manifest_bytes).map_err(|_| Error::Denied)?;
        validate_manifest(&manifest, self, &roots)?;
        regular(
            &source.join("database.dump.gpg"),
            MAX_DATABASE_BYTES.saturating_add(1024 * 1024 * 1024),
        )?;
        crypt(
            &source.join("database.dump.gpg"),
            &scratch.0.join("database.dump"),
            key,
            true,
        )?;
        let actual_database = digest(&scratch.0.join("database.dump"), MAX_DATABASE_BYTES)?;
        if actual_database.bytes != manifest.database.bytes
            || actual_database.sha256 != manifest.database.sha256
        {
            return Err(input("backup hash mismatch"));
        }
        regular(
            &source.join("runtime.data.gpg"),
            MAX_RUNTIME_BYTES.saturating_add(1024 * 1024 * 1024),
        )?;
        crypt(
            &source.join("runtime.data.gpg"),
            &scratch.0.join("runtime.data"),
            key,
            true,
        )?;
        verify_runtime_bundle(&scratch.0.join("runtime.data"), &manifest.runtime)?;
        restore_database(configuration, &scratch.0.join("database.dump"))?;
        restore_runtime_bundle(&scratch.0.join("runtime.data"), &manifest.runtime, &roots)?;
        revoke_restored_authority(self, configuration)?;
        super::super::git::repair_after_restore(self, configuration, runtime)?;
        validate_adapters(self, configuration, runtime)?;
        Ok(serde_json::json!({
            "restored":true,
            "elapsed_ms":started.elapsed().as_millis(),
            "snapshot_unix_seconds":manifest.snapshot_unix_seconds,
            "jobs_paused":true,
            "credentials_revoked":true,
            "database_files_verified":1,
            "runtime_entries_verified":manifest.runtime.len(),
            "runtime_roots_verified":manifest.roots.len(),
            "data_loss_window":"changes since quiesced snapshot"
        })
        .to_string())
    }
}
