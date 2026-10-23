//! Explicit shared accounts, purpose-bound challenges, and attenuated credentials.
mod credentials;
mod delivery;
mod enrollment;
mod oauth;
mod rates;
mod routes;
mod schema;
mod sessions;

use super::input;
pub(super) use crate::authority::Restrictions;
use crate::error::{Error, Result};
pub(super) use delivery::{Delivery, Mailbox};
pub(super) use oauth::{OAuthIdentity, OAuthSecrets};
pub(super) use rates::charge;
pub(super) use routes::{dispatch, route, signature, subject};
pub(super) use schema::{initialize, validate};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    pub profile: String,
    pub enrollment: Enrollment,
    #[serde(default)]
    pub oauth: Option<OAuthConfig>,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(super) enum Enrollment {
    Invite,
    Open,
    Closed,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct OAuthConfig {
    pub providers: BTreeSet<OAuthProvider>,
    pub admission: OAuthAdmission,
    #[serde(default)]
    pub require_all: bool,
    #[serde(default)]
    pub email_domains: BTreeSet<String>,
    #[serde(default)]
    pub google_tenants: BTreeSet<String>,
    #[serde(default)]
    pub github_organizations: BTreeSet<String>,
    #[serde(default)]
    pub identities: BTreeSet<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "lowercase")]
pub(super) enum OAuthProvider {
    Google,
    Github,
}

impl OAuthProvider {
    pub(super) const fn name(self) -> &'static str {
        match self {
            Self::Google => "google",
            Self::Github => "github",
        }
    }

    pub(super) fn parse(value: &str) -> Option<Self> {
        match value {
            "google" => Some(Self::Google),
            "github" => Some(Self::Github),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(super) enum OAuthAdmission {
    Public,
    Restricted,
    Invite,
    Closed,
}

impl Config {
    pub fn checked(&self) -> Result<()> {
        if self.profile != "accounts-v1" {
            return Err(input("unsupported identity profile"));
        }
        if let Some(oauth) = &self.oauth {
            if oauth.providers.is_empty()
                || oauth.providers.len() > 2
                || (oauth.admission == OAuthAdmission::Restricted
                    && oauth.email_domains.is_empty()
                    && oauth.google_tenants.is_empty()
                    && oauth.github_organizations.is_empty()
                    && oauth.identities.is_empty())
                || oauth
                    .email_domains
                    .iter()
                    .chain(&oauth.google_tenants)
                    .any(|domain| !valid_domain(domain))
                || oauth
                    .github_organizations
                    .iter()
                    .any(|organization| !valid_github_name(organization))
                || oauth.identities.len() > 256
                || oauth.email_domains.len() > 64
                || oauth.google_tenants.len() > 64
                || oauth.github_organizations.len() > 64
                || oauth.identities.iter().any(|identity| {
                    identity.len() > 320
                        || (!identity.starts_with("google:") && !identity.starts_with("github:"))
                        || identity.chars().any(char::is_control)
                })
            {
                return Err(input("invalid OAuth identity policy"));
            }
            if oauth
                .email_domains
                .iter()
                .chain(&oauth.google_tenants)
                .any(|domain| domain != &domain.to_ascii_lowercase())
                || oauth
                    .github_organizations
                    .iter()
                    .any(|name| name != &name.to_ascii_lowercase())
            {
                return Err(input("OAuth policy names must be lowercase"));
            }
        }
        Ok(())
    }
}

fn valid_domain(value: &str) -> bool {
    value.len() <= 189
        && value.contains('.')
        && value.split('.').all(|part| {
            !part.is_empty()
                && !part.starts_with('-')
                && !part.ends_with('-')
                && part
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        })
}

fn valid_github_name(value: &str) -> bool {
    (1..=39).contains(&value.len())
        && !value.starts_with('-')
        && !value.ends_with('-')
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
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
