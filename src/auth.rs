//! Opaque, expiring server-side sessions for a trusted embedding host.

use crate::authority::{Principal, random_hex};
use crate::error::{Error, Result};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::time::{Duration, Instant};
use subtle::ConstantTimeEq;

const MAX_SESSIONS: usize = 4096;

/// An opaque session token. Debug formatting intentionally redacts its contents.
pub struct SessionToken(String);

impl fmt::Debug for SessionToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SessionToken([REDACTED])")
    }
}

impl SessionToken {
    /// Access the token for transport in a secure cookie by the trusted host.
    #[must_use]
    pub fn cookie_value(&self) -> &str {
        &self.0
    }
}

#[derive(Debug)]
struct Session {
    principal: Principal,
    expiration: Instant,
    csrf_hash: [u8; 32],
    csrf: SessionToken,
}

/// A bounded in-memory session registry. The embedding host authenticates users.
///
/// The development runtime can supply a local browser login provider. Sessions
/// remain process-local and are invalidated on restart. Lifetimes use elapsed
/// monotonic time, so wall-clock adjustments cannot revive an expired session.
///
/// ```
/// use std::time::Duration;
/// let mut sessions = diamond::SessionStore::default();
/// let (token, csrf) = sessions.issue("alice", &["editor"], Duration::from_secs(60))?;
/// assert!(sessions.is_valid(token.cookie_value()));
/// assert!(sessions.check_csrf(token.cookie_value(), &csrf));
/// sessions.revoke(token.cookie_value());
/// assert!(!sessions.is_valid(token.cookie_value()));
/// # Ok::<(), diamond::Error>(())
/// ```
#[derive(Debug, Default)]
pub struct SessionStore {
    sessions: BTreeMap<[u8; 32], Session>,
}

impl SessionStore {
    /// Issue a session for a user already authenticated by the trusted host.
    ///
    /// User-controlled request fields MUST NOT be passed here as verified identity.
    ///
    /// # Errors
    /// Rejects invalid identifiers, zero/overlong lifetimes, exhausted capacity,
    /// clock overflow, or unavailable secure randomness.
    pub fn issue(
        &mut self,
        user: &str,
        roles: &[&str],
        lifetime: Duration,
    ) -> Result<(SessionToken, String)> {
        if lifetime.is_zero() || lifetime > Duration::from_hours(24) {
            return Err(Error::Input(
                "session lifetime must be within one day".into(),
            ));
        }
        self.issue_principal(user_principal(user, roles)?, lifetime, None, false)
    }

    pub(crate) fn issue_anonymous(&mut self) -> Result<(SessionToken, String)> {
        self.issue_principal(Principal::Anonymous, Duration::from_mins(5), None, true)
    }

    pub(crate) fn rotate(
        &mut self,
        previous: &str,
        user: &str,
        roles: &[&str],
        lifetime: Duration,
    ) -> Result<(SessionToken, String)> {
        if lifetime.is_zero() || lifetime > Duration::from_hours(24) {
            return Err(Error::Input(
                "session lifetime must be within one day".into(),
            ));
        }
        self.issue_principal(
            user_principal(user, roles)?,
            lifetime,
            Some(previous),
            false,
        )
    }

    fn issue_principal(
        &mut self,
        principal: Principal,
        lifetime: Duration,
        previous: Option<&str>,
        evict_anonymous: bool,
    ) -> Result<(SessionToken, String)> {
        let now = Instant::now();
        self.sessions.retain(|_, session| session.expiration > now);
        let replacement_required = previous.is_some();
        let previous = previous
            .filter(|token| valid_token(token))
            .map(hash)
            .filter(|key| self.sessions.contains_key(key));
        if replacement_required && previous.is_none() {
            return Err(Error::Denied);
        }
        let eviction = if self.sessions.len() >= MAX_SESSIONS && previous.is_none() {
            if !evict_anonymous {
                return Err(Error::Limit("session count"));
            }
            Some(
                self.sessions
                    .iter()
                    .filter(|(_, session)| matches!(&session.principal, Principal::Anonymous))
                    .min_by_key(|(_, session)| session.expiration)
                    .map(|(key, _)| *key)
                    .ok_or(Error::Limit("session count"))?,
            )
        } else {
            previous
        };
        let token = random_hex()?;
        let csrf = random_hex()?;
        let expiration = now
            .checked_add(lifetime)
            .ok_or(Error::Limit("session expiration"))?;
        if let Some(key) = eviction {
            self.sessions.remove(&key);
        }
        self.sessions.insert(
            hash(&token),
            Session {
                principal,
                expiration,
                csrf_hash: hash(&csrf),
                csrf: SessionToken(csrf.clone()),
            },
        );
        Ok((SessionToken(token), csrf))
    }

    /// Return whether the supplied opaque token names an unexpired session.
    #[must_use]
    pub fn is_valid(&self, token: &str) -> bool {
        self.principal(token).is_some()
    }

    /// Check a session-bound CSRF token without exposing the stored token.
    #[must_use]
    pub fn check_csrf(&self, token: &str, csrf: &str) -> bool {
        if !valid_token(token) || !valid_token(csrf) {
            return false;
        }
        self.sessions.get(&hash(token)).is_some_and(|session| {
            session.expiration > Instant::now() && bool::from(session.csrf_hash.ct_eq(&hash(csrf)))
        })
    }

    /// Revoke the exact token immediately.
    pub fn revoke(&mut self, token: &str) {
        self.sessions.remove(&hash(token));
    }

    pub(crate) fn principal(&self, token: &str) -> Option<&Principal> {
        if !valid_token(token) {
            return None;
        }
        self.sessions
            .get(&hash(token))
            .filter(|session| session.expiration > Instant::now())
            .map(|session| &session.principal)
    }

    pub(crate) fn csrf(&self, token: &str) -> Option<&str> {
        if !valid_token(token) {
            return None;
        }
        self.sessions
            .get(&hash(token))
            .filter(|session| session.expiration > Instant::now())
            .map(|session| session.csrf.cookie_value())
    }
}

fn user_principal(user: &str, roles: &[&str]) -> Result<Principal> {
    if !valid_id(user) || roles.iter().any(|role| !valid_id(role)) {
        return Err(Error::Input("invalid session identity".into()));
    }
    Ok(Principal::User {
        id: user.into(),
        roles: roles
            .iter()
            .map(|role| (*role).into())
            .collect::<BTreeSet<_>>(),
    })
}

fn hash(value: &str) -> [u8; 32] {
    Sha256::digest(value.as_bytes()).into()
}
fn valid_token(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|c| c.is_ascii_hexdigit())
}
pub(crate) fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}
