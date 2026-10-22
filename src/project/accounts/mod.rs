//! Explicit shared accounts, purpose-bound challenges, and attenuated credentials.
mod credentials;
mod delivery;
mod enrollment;
mod rates;
mod routes;
mod schema;
mod sessions;

use super::input;
pub(super) use crate::authority::Restrictions;
use crate::error::{Error, Result};
pub(super) use delivery::{Delivery, Mailbox};
pub(super) use rates::charge;
pub(super) use routes::{dispatch, route, signature, subject};
pub(super) use schema::{initialize, validate};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    pub profile: String,
    pub enrollment: Enrollment,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(super) enum Enrollment {
    Invite,
    Open,
    Closed,
}
impl Config {
    pub fn checked(&self) -> Result<()> {
        if self.profile != "accounts-v1" {
            return Err(input("unsupported identity profile"));
        }
        Ok(())
    }
}
fn digest(app: &str, purpose: &str, secret: &str) -> String {
    super::artifact::hash(format!("diamond-identity-v1:{app}:{purpose}:{secret}").as_bytes())
}
fn valid_secret(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn email(s: &str) -> Result<String> {
    if s.len() > 254
        || !s.is_ascii()
        || s.bytes()
            .any(|b| b.is_ascii_control() || b.is_ascii_whitespace())
    {
        return Err(input("invalid email address"));
    }
    let (local, domain) = s
        .split_once('@')
        .ok_or_else(|| input("invalid email address"))?;
    if local.is_empty()
        || local.len() > 64
        || domain.len() > 189
        || !domain.contains('.')
        || domain.starts_with('.')
        || domain.ends_with('.')
        || !local
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".!#$%&'*+-/=?^_`{|}~".contains(&b))
        || !domain.split('.').all(|part| {
            !part.is_empty()
                && !part.starts_with('-')
                && !part.ends_with('-')
                && part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
        })
    {
        return Err(input("invalid email address"));
    }
    Ok(s.to_ascii_lowercase())
}
fn password(s: &str) -> Result<()> {
    if !(12..=256).contains(&s.len()) {
        return Err(input("password requires 12..256 UTF-8 bytes"));
    }
    Ok(())
}
fn stored<T>(v: std::result::Result<T, tokio_postgres::Error>) -> Result<T> {
    v.map_err(|_| Error::Postgres)
}
