//! Quiesced encrypted snapshots and isolated, paused restore.
use super::{CheckedProject, Error, Path, PostgresStore, Result, guard, input};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{DirBuilderExt as _, OpenOptionsExt as _, PermissionsExt as _},
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Entry {
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
    files: BTreeMap<String, Entry>,
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
fn regular(path: &Path, maximum: u64) -> Result<()> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.len() > maximum {
        return Err(input("invalid or oversized backup file"));
    }
    Ok(())
}
fn digest(path: &Path) -> Result<Entry> {
    regular(path, 1024 * 1024 * 1024)?;
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 8192];
    let mut bytes = 0;
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
        bytes += u64::try_from(n).map_err(|_| Error::Denied)?;
    }
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
    let deadline = Instant::now() + Duration::from_mins(1);
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
    // An isolated homedir prevents keyring/configuration reads or writes in the operator's home.
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
fn object_name(name: &str) -> bool {
    name == ".diamond-shared-store"
        || name.is_ascii()
            && name.len() == 134
            && name.as_bytes()[64] == b'-'
            && (name.get(129..) == Some(".blob") || name.get(129..) == Some(".part"))
            && name[..64]
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            && name[65..129]
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
fn object_root(grants: &crate::RuntimeGrants) -> Result<&Path> {
    let path = grants.objects.as_deref().ok_or(Error::Denied)?;
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir() || meta.permissions().mode() & 0o777 != 0o700 || path.canonicalize()? != path
    {
        return Err(input(
            "restore object root must be a canonical private 0700 directory",
        ));
    }
    Ok(path)
}
impl CheckedProject {
    /// Create a new private directory containing an AES256-encrypted quiesced snapshot.
    /// Requires one application per database, stopped servers/workers, `GnuPG` and `PostgreSQL` tools.
    /// The caller supplies a passphrase separately; no configuration or key is written to the manifest.
    ///
    /// # Errors
    /// Rejects active instances, missing grants, existing destinations, unsafe files or failed commands.
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
        let _objects = super::super::files::Store::open(self, configuration, runtime, false)?;
        let mut db = PostgresStore::open_control(self, configuration, None)?;
        let count = db
            .connection
            .query("SELECT COUNT(*) FROM diamond_w2.apps", &[])?[0]
            .try_get::<_, i64>(0)
            .map_err(|_| Error::Denied)?;
        if count != 1 {
            return Err(input(
                "snapshot profile requires one application per database",
            ));
        }
        db.finish_request(Ok(()))?;
        private_new(destination)?;
        let mut paused = false;
        if !self.manifest.jobs.is_empty() || !self.manifest.events.is_empty() {
            let mut db = PostgresStore::open_control(self, configuration, None)?;
            paused = db.connection.query(
                "SELECT paused FROM diamond_w7.apps WHERE app=$1",
                &[&db.app],
            )?[0]
                .try_get(0)
                .map_err(|_| Error::Denied)?;
            db.connection.query(
                "UPDATE diamond_w7.apps SET paused=true WHERE app=$1",
                &[&db.app],
            )?;
            db.finish_request(Ok(()))?;
        }
        let temporary = destination.join("plaintext-work");
        private_new(&temporary)?;
        let scratch = Scratch(temporary);
        let dump = scratch.0.join("database.dump");
        let mut command = pg("/usr/bin/pg_dump", configuration)?;
        command
            .args(["--format=custom", "--no-owner", "--no-acl", "--file"])
            .arg(&dump);
        run(command, None)?;
        if !self.manifest.jobs.is_empty() || !self.manifest.events.is_empty() {
            let mut db = PostgresStore::open_control(self, configuration, None)?;
            db.connection.query(
                "UPDATE diamond_w7.apps SET paused=$2 WHERE app=$1",
                &[&db.app, &paused],
            )?;
            db.finish_request(Ok(()))?;
        }
        let mut files = BTreeMap::new();
        files.insert("database.dump".into(), digest(&dump)?);
        crypt(&dump, &destination.join("database.dump.gpg"), key, false)?;
        if self.manifest.files.is_some() {
            for entry in fs::read_dir(object_root(runtime)?)?.take(10002) {
                let entry = entry?;
                let name = entry.file_name().into_string().map_err(|_| Error::Denied)?;
                if !object_name(&name) {
                    return Err(input("unexpected object store entry"));
                }
                if files.len() > 10001 {
                    return Err(Error::Limit("backup file count"));
                }
                regular(&entry.path(), super::super::files::MAX_BYTES)?;
                files.insert(name.clone(), digest(&entry.path())?);
                crypt(
                    &entry.path(),
                    &destination.join(format!("{name}.gpg")),
                    key,
                    false,
                )?;
            }
        }
        let manifest = Manifest {
            version: 1,
            app: self.manifest.name.clone(),
            contract: super::super::artifact::contract_id(self)?,
            snapshot_unix_seconds: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| Error::Denied)?
                .as_secs(),
            files,
        };
        let plain = scratch.0.join("manifest.json");
        fs::write(
            &plain,
            serde_json::to_vec_pretty(&manifest).map_err(|_| Error::Denied)?,
        )?;
        crypt(&plain, &destination.join("manifest.json.gpg"), key, false)?;
        drop(scratch);
        let _removed = fs::remove_dir_all(destination.join("gpg-home"));
        File::open(destination)?.sync_all()?;
        Ok(serde_json::json!({"snapshot":"encrypted","files":manifest.files.len(),"elapsed_ms":started.elapsed().as_millis(),"consistency":"quiesced","jobs_recovery":"paused until explicit resume or cancel","credentials_recovery":"sessions,API credentials,challenges revoked"}).to_string())
    }
    /// Restore into an empty isolated database and empty private object directory.
    /// Verifies decrypted hashes before database writes; revokes credentials and pauses jobs before returning.
    /// Retain the isolated target for inspection if a later step fails; do not route users to it.
    ///
    /// # Errors
    /// Rejects active instances, nonempty targets, wrong keys/contracts, tampering and command failures.
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
        let mut guard = guard(self, configuration, true)?;
        let count=guard.query("SELECT COUNT(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'", &[])?[0].try_get::<_,i64>(0).map_err(|_|Error::Denied)?;
        if count != 0 {
            return Err(input("restore requires a separate empty database"));
        }
        if self.manifest.files.is_some() && fs::read_dir(object_root(runtime)?)?.next().is_some() {
            return Err(input("restore requires an empty object directory"));
        }
        let scratch_path = std::env::temp_dir().join(format!(
            "diamond-restore-{}",
            crate::authority::random_hex()?
        ));
        private_new(&scratch_path)?;
        let scratch = Scratch(scratch_path);
        regular(&source.join("manifest.json.gpg"), 8 * 1024 * 1024)?;
        crypt(
            &source.join("manifest.json.gpg"),
            &scratch.0.join("manifest.json"),
            key,
            true,
        )?;
        regular(&scratch.0.join("manifest.json"), 4 * 1024 * 1024)?;
        let manifest: Manifest =
            serde_json::from_reader(File::open(scratch.0.join("manifest.json"))?)
                .map_err(|_| Error::Denied)?;
        if manifest.version != 1
            || manifest.app != self.manifest.name
            || manifest.contract != super::super::artifact::contract_id(self)?
            || manifest.files.len() > 10002
            || !manifest.files.contains_key("database.dump")
        {
            return Err(input("backup contract mismatch"));
        }
        for (name, entry) in &manifest.files {
            if name != "database.dump" && !object_name(name) {
                return Err(Error::Denied);
            }
            let encrypted = source.join(format!("{name}.gpg"));
            regular(&encrypted, 1024 * 1024 * 1024)?;
            let plain = scratch.0.join(name);
            crypt(&encrypted, &plain, key, true)?;
            let actual = digest(&plain)?;
            if actual.bytes != entry.bytes || actual.sha256 != entry.sha256 {
                return Err(input("backup hash mismatch"));
            }
        }
        let mut command = pg("/usr/bin/pg_restore", configuration)?;
        let config = configuration
            .parse::<tokio_postgres::Config>()
            .map_err(|_| Error::Denied)?;
        command
            .args([
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                "--dbname",
            ])
            .arg(config.get_dbname().ok_or(Error::Denied)?)
            .arg(scratch.0.join("database.dump"));
        run(command, None)?;
        let mut db = PostgresStore::open_control(self, configuration, None)?;
        db.connection.query(
            "UPDATE diamond_identity.users SET epoch=epoch+1 WHERE app=$1",
            &[&db.app],
        )?;
        for table in ["sessions", "credentials", "challenges"] {
            db.connection.query(
                &format!("DELETE FROM diamond_identity.{table} WHERE app=$1"),
                &[&db.app],
            )?;
        }
        if !self.manifest.jobs.is_empty() || !self.manifest.events.is_empty() {
            db.connection.query(
                "UPDATE diamond_w7.apps SET paused=true WHERE app=$1",
                &[&db.app],
            )?;
        }
        db.finish_request(Ok(()))?;
        if self.manifest.files.is_some() {
            for name in manifest
                .files
                .keys()
                .filter(|n| n.as_str() != "database.dump")
            {
                let to = object_root(runtime)?.join(name);
                let mut file = OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .mode(0o600)
                    .open(&to)?;
                std::io::copy(&mut File::open(scratch.0.join(name))?, &mut file)?;
                file.sync_all()?;
            }
            File::open(object_root(runtime)?)?.sync_all()?;
            let _objects = super::super::files::Store::open(self, configuration, runtime, false)?;
        }
        Ok(serde_json::json!({"restored":true,"elapsed_ms":started.elapsed().as_millis(),"snapshot_unix_seconds":manifest.snapshot_unix_seconds,"jobs_paused":true,"credentials_revoked":true,"files_verified":manifest.files.len(),"data_loss_window":"changes since quiesced snapshot"}).to_string())
    }
}
