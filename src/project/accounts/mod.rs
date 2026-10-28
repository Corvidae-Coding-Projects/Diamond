//! Explicit shared accounts, purpose-bound challenges, and attenuated credentials.
mod credentials;
mod delivery;
mod enrollment;
mod oauth;
mod rates;
mod routes;
mod schema;
mod sessions;
mod ssh;

use super::input;
pub(super) use crate::authority::Restrictions;
use crate::error::{Error, Result};
pub(super) use delivery::{Delivery, Mailbox};
pub(super) use oauth::{OAuthIdentity, OAuthSecrets};
pub(super) use rates::charge;
pub(super) use routes::{dispatch, route, signature, subject};
pub(super) use schema::{initialize, validate};
use serde::{Deserialize, Serialize};
pub(super) use ssh::parse_public_key;
use std::collections::BTreeSet;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    pub profile: String,
    pub enrollment: Enrollment,
    #[serde(default)]
    pub oauth: Option<OAuthConfig>,
    #[serde(default)]
    api_requests_per_minute: ApiRequestsPerMinute,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct ApiRequestsPerMinute {
    #[serde(default = "api_global_default")]
    global: i64,
    #[serde(default = "api_peer_default")]
    peer: i64,
    #[serde(default = "api_credential_default")]
    credential: i64,
}

const fn api_global_default() -> i64 {
    600
}

const fn api_peer_default() -> i64 {
    300
}

const fn api_credential_default() -> i64 {
    120
}

impl Default for ApiRequestsPerMinute {
    fn default() -> Self {
        Self {
            global: api_global_default(),
            peer: api_peer_default(),
            credential: api_credential_default(),
        }
    }
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
        let limits = self.api_requests_per_minute.values();
        if limits
            .iter()
            .any(|limit| !(10..=10_000_000).contains(limit))
            || limits[0] < limits[1]
            || limits[1] < limits[2]
        {
            return Err(input(
                "API rate limits require 10..10000000 requests per minute and global >= peer >= credential",
            ));
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

    pub(super) const fn api_rate_limits(&self) -> [i64; 3] {
        self.api_requests_per_minute.values()
    }
}

impl ApiRequestsPerMinute {
    const fn values(self) -> [i64; 3] {
        [self.global, self.peer, self.credential]
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

#[cfg(test)]
mod tests {
    use super::*;

    fn config(api_requests_per_minute: ApiRequestsPerMinute) -> Config {
        Config {
            profile: "accounts-v1".to_owned(),
            enrollment: Enrollment::Invite,
            oauth: None,
            api_requests_per_minute,
        }
    }

    #[test]
    fn api_rate_limits_keep_secure_defaults() {
        let limits = ApiRequestsPerMinute::default();
        assert_eq!(limits.values(), [600, 300, 120]);
        assert!(config(limits).checked().is_ok());
    }

    #[test]
    fn api_rate_limits_accept_bounded_ordered_capacity() {
        let limits = ApiRequestsPerMinute {
            global: 1_000_000,
            peer: 900_000,
            credential: 800_000,
        };
        assert!(config(limits).checked().is_ok());
    }

    #[test]
    fn api_rate_limits_reject_inversion_and_unbounded_values() {
        let inverted = ApiRequestsPerMinute {
            global: 100,
            peer: 101,
            credential: 10,
        };
        assert!(config(inverted).checked().is_err());
        let unbounded = ApiRequestsPerMinute {
            global: 10_000_001,
            peer: 10,
            credential: 10,
        };
        assert!(config(unbounded).checked().is_err());
    }
}
