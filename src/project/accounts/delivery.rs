use super::super::input;
use crate::{
    authority::random_hex,
    error::{Error, Result},
};
use std::{
    fmt,
    fs::{self, OpenOptions},
    io::Write,
    os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
};
use zeroize::Zeroizing;
pub(in crate::project) struct Delivery {
    pub email: String,
    pub purpose: &'static str,
    pub token: Zeroizing<String>,
}
impl fmt::Debug for Delivery {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Delivery")
            .field("secret", &"[PRIVATE]")
            .finish_non_exhaustive()
    }
}
#[derive(Debug)]
pub(in crate::project) struct Mailbox {
    directory: PathBuf,
}
impl Mailbox {
    pub fn open(directory: &Path) -> Result<Self> {
        let metadata = fs::symlink_metadata(directory)?;
        let owner = fs::metadata("/proc/self")?.uid();
        if !metadata.is_dir()
            || metadata.file_type().is_symlink()
            || metadata.uid() != owner
            || metadata.permissions().mode() & 0o777 != 0o700
        {
            return Err(input(
                "identity mail directory must exist, belong to the process user, and have mode 0700",
            ));
        }
        Ok(Self {
            directory: fs::canonicalize(directory)?,
        })
    }
    pub fn send(&self, message: &Delivery) -> Result<()> {
        if fs::read_dir(&self.directory)?.take(20000).count() >= 20000 {
            return Err(Error::Limit("identity mailbox messages"));
        }
        let path = self.directory.join(format!("{}.json", random_hex()?));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)?;
        let body=Zeroizing::new(serde_json::json!({"recipient":message.email,"purpose":message.purpose,"token":message.token.as_str(),"profile":"development-mailbox-v1"}).to_string());
        if file
            .write_all(body.as_bytes())
            .and_then(|()| file.sync_all())
            .and_then(|()| fs::File::open(&self.directory)?.sync_all())
            .is_err()
        {
            let _cleanup = fs::remove_file(&path);
            return Err(Error::OutcomeUnknown(random_hex()?));
        }
        Ok(())
    }
}
