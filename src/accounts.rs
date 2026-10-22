//! Explicitly provisioned local accounts, separate from application data authority.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::path::Path;
use std::path::PathBuf;
use std::time::Duration;

use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::{Algorithm, Argon2, Params, Version};
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use zeroize::Zeroizing;

use crate::auth::valid_id;
use crate::authority::random_hex;
use crate::error::{Error, Result};

const SCHEMA: &str = "CREATE TABLE diamond_auth_users (username TEXT PRIMARY KEY NOT NULL COLLATE BINARY, password_hash TEXT NOT NULL)";
const MAX_ACCOUNTS: usize = 10_000;

pub(crate) struct LocalAccounts {
    connection: Connection,
    dummy_hash: Zeroizing<String>,
    _file: PrivateFile,
}

impl fmt::Debug for LocalAccounts {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("LocalAccounts([PRIVATE])")
    }
}

impl LocalAccounts {
    pub fn open(path: &Path) -> Result<Self> {
        let file = PrivateFile::open(path, false)?;
        let connection = file.connect(OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        connection.busy_timeout(Duration::from_secs(2))?;
        connection.execute_batch("PRAGMA trusted_schema=OFF;")?;
        validate_schema(&connection)?;
        validate_accounts(&connection)?;
        let dummy = Zeroizing::new(random_hex()?);
        Ok(Self {
            connection,
            dummy_hash: Zeroizing::new(hash_password(&dummy)?),
            _file: file,
        })
    }

    pub fn verify(&self, username: &str, password: &str) -> Result<bool> {
        if password.len() > 256 || !valid_id(username) {
            return Ok(false);
        }
        let stored: Option<String> = self
            .connection
            .query_row(
                "SELECT password_hash FROM diamond_auth_users WHERE username=?1",
                [username],
                |row| row.get(0),
            )
            .optional()?;
        let encoded = stored.as_deref().unwrap_or(&self.dummy_hash);
        let parsed = parse_password_hash(encoded)?;
        let valid = password_hasher()?
            .verify_password(password.as_bytes(), &parsed)
            .is_ok();
        Ok(stored.is_some() && valid)
    }
}

pub(crate) fn add_user(path: &Path, username: &str, password: &str) -> Result<()> {
    if !valid_id(username) {
        return Err(Error::Input(
            "username must be 1..128 ASCII letters, digits, underscores, or hyphens".into(),
        ));
    }
    if !(12..=256).contains(&password.len()) {
        return Err(Error::Input(
            "password must contain 12..256 UTF-8 bytes".into(),
        ));
    }
    let encoded = Zeroizing::new(hash_password(password)?);
    let (file, created) = PrivateFile::open_or_create(path)?;
    let connection = file.connect(OpenFlags::SQLITE_OPEN_READ_WRITE)?;
    connection.busy_timeout(Duration::from_secs(2))?;
    connection.execute_batch("PRAGMA trusted_schema=OFF;")?;
    if created {
        connection.execute(SCHEMA, [])?;
    }
    validate_schema(&connection)?;
    let account_count = validate_accounts(&connection)?;
    let exists: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM diamond_auth_users WHERE username=?1)",
        [username],
        |row| row.get(0),
    )?;
    if exists {
        return Err(Error::Input(
            "account already exists; password replacement is not supported by user-add".into(),
        ));
    }
    if account_count >= MAX_ACCOUNTS {
        return Err(Error::Limit("local account count"));
    }
    connection.execute(
        "INSERT INTO diamond_auth_users (username, password_hash) VALUES (?1, ?2)",
        (username, encoded.as_str()),
    )?;
    Ok(())
}

fn password_hasher() -> Result<Argon2<'static>> {
    let parameters = Params::new(19_456, 2, 1, None)
        .map_err(|_| Error::Input("invalid password-hash parameters".into()))?;
    Ok(Argon2::new(Algorithm::Argon2id, Version::V0x13, parameters))
}

pub(crate) fn hash_password(password: &str) -> Result<String> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes).map_err(|_| Error::Random)?;
    let salt = SaltString::encode_b64(&bytes).map_err(|_| Error::Random)?;
    password_hasher()?
        .hash_password(password.as_bytes(), &salt)
        .map(|hash| hash.to_string())
        .map_err(|_| Error::Input("password hashing failed".into()))
}

fn parse_password_hash(encoded: &str) -> Result<PasswordHash<'_>> {
    let parsed = PasswordHash::new(encoded)
        .map_err(|_| Error::Input("invalid account hash configuration".into()))?;
    // Never honor arbitrary cost parameters from a corrupted hash record.
    if parsed.algorithm.as_str() != "argon2id"
        || parsed.version != Some(19)
        || parsed.params.iter().count() != 3
        || parsed.params.get_decimal("m") != Some(19_456)
        || parsed.params.get_decimal("t") != Some(2)
        || parsed.params.get_decimal("p") != Some(1)
        || parsed.salt.is_none_or(|salt| salt.len() != 22)
        || parsed.hash.is_none_or(|hash| hash.len() != 32)
    {
        return Err(Error::Input(
            "unsupported account hash configuration".into(),
        ));
    }
    Ok(parsed)
}

pub(crate) fn verify_password(encoded: &str, password: &str) -> Result<bool> {
    let parsed = parse_password_hash(encoded)?;
    Ok(password_hasher()?
        .verify_password(password.as_bytes(), &parsed)
        .is_ok())
}

fn validate_schema(connection: &Connection) -> Result<()> {
    let schema: Option<String> = connection
        .query_row(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='diamond_auth_users'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let triggers: i64 = connection.query_row(
        "SELECT count(*) FROM sqlite_master WHERE type='trigger'",
        [],
        |row| row.get(0),
    )?;
    if schema.as_deref() != Some(SCHEMA) || triggers != 0 {
        return Err(Error::Input(
            "not a supported Diamond local-account database".into(),
        ));
    }
    Ok(())
}

fn validate_accounts(connection: &Connection) -> Result<usize> {
    let mut statement = connection.prepare(
        "SELECT username, password_hash FROM diamond_auth_users ORDER BY username LIMIT 10001",
    )?;
    let mut rows = statement.query([])?;
    let mut count = 0;
    while let Some(row) = rows.next()? {
        count += 1;
        if count > MAX_ACCOUNTS {
            return Err(Error::Limit("local account count"));
        }
        let username: String = row.get(0)?;
        let password_hash: String = row.get(1)?;
        if !valid_id(&username) {
            return Err(Error::Input("invalid stored account identity".into()));
        }
        parse_password_hash(&password_hash)?;
    }
    Ok(count)
}

struct PrivateFile {
    file: File,
    path: PathBuf,
}

impl fmt::Debug for PrivateFile {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PrivateFile([PRIVATE])")
    }
}

#[cfg(unix)]
impl PrivateFile {
    fn options(write: bool) -> OpenOptions {
        use std::os::unix::fs::OpenOptionsExt;
        let mut options = OpenOptions::new();
        options
            .read(true)
            .write(write)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        options
    }

    fn open(path: &Path, write: bool) -> Result<Self> {
        let file = Self::options(write).open(path)?;
        check_permissions(&file)?;
        Ok(Self {
            file,
            path: path.into(),
        })
    }

    fn open_or_create(path: &Path) -> Result<(Self, bool)> {
        match Self::options(true).create_new(true).open(path) {
            Ok(file) => {
                check_permissions(&file)?;
                Ok((
                    Self {
                        file,
                        path: path.into(),
                    },
                    true,
                ))
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                Ok((Self::open(path, true)?, false))
            }
            Err(error) => Err(error.into()),
        }
    }

    fn connect(&self, flags: OpenFlags) -> Result<Connection> {
        let connection =
            Connection::open_with_flags(&self.path, flags | OpenFlags::SQLITE_OPEN_NOFOLLOW)?;
        self.check_path_identity()?;
        Ok(connection)
    }

    fn check_path_identity(&self) -> Result<()> {
        use std::os::unix::fs::MetadataExt;
        let anchored = self.file.metadata()?;
        let path = fs::symlink_metadata(&self.path)?;
        if anchored.dev() != path.dev() || anchored.ino() != path.ino() {
            return Err(Error::Input(
                "the account database changed while it was being opened".into(),
            ));
        }
        Ok(())
    }
}

#[cfg(unix)]
fn check_permissions(file: &File) -> Result<()> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.permissions().mode() & 0o077 != 0 || metadata.nlink() != 1 {
        return Err(Error::Input(
            "the account database must be a regular, unlinked private file (mode 0600)".into(),
        ));
    }
    Ok(())
}

#[cfg(not(unix))]
impl PrivateFile {
    fn unsupported<T>() -> Result<T> {
        Err(Error::unsupported(
            crate::Location::default(),
            "local account file permissions on this platform",
        ))
    }

    fn open(_path: &Path, _write: bool) -> Result<Self> {
        Self::unsupported()
    }

    fn open_or_create(_path: &Path) -> Result<(Self, bool)> {
        Self::unsupported()
    }

    fn connect(&self, _flags: OpenFlags) -> Result<Connection> {
        Self::unsupported()
    }
}
