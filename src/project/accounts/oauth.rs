//! One-use, session-bound OAuth transactions and stable provider identities.

use super::{digest, email, stored, valid_secret};
use crate::{
    Error,
    error::Result,
    project::{identity::Session, storage::PostgresStore, wire},
};
use base64::Engine as _;
use chacha20poly1305::{
    XChaCha20Poly1305, XNonce,
    aead::{Aead as _, KeyInit as _, Payload},
};
use serde::{Deserialize, Serialize};
use zeroize::{Zeroize, Zeroizing};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(in crate::project) struct OAuthSecrets {
    pub(in crate::project) verifier: String,
    pub(in crate::project) nonce: String,
    pub(in crate::project) invite: String,
}

impl Drop for OAuthSecrets {
    fn drop(&mut self) {
        self.verifier.zeroize();
        self.nonce.zeroize();
        self.invite.zeroize();
    }
}

pub(in crate::project) struct OAuthIdentity {
    pub(in crate::project) provider: String,
    pub(in crate::project) subject: String,
    pub(in crate::project) email: String,
}

impl PostgresStore {
    pub(in crate::project) fn begin_oauth(
        &mut self,
        token: &str,
        provider: &str,
        invite: &str,
        key: &[u8; 32],
    ) -> Result<(Zeroizing<String>, OAuthSecrets)> {
        if !matches!(provider, "google" | "github") || (!invite.is_empty() && !valid_secret(invite))
        {
            return Err(Error::Denied);
        }
        let session_digest = self.oauth_session_binding(token)?;
        self.connection.query(
            "DELETE FROM diamond_identity.oauth_transactions WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()",
            &[&self.app],
        )?;
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{}:oauth-transaction-capacity", self.app)],
        )?;
        let rows = self.connection.query(
            "SELECT count(*) FROM diamond_identity.oauth_transactions WHERE app=$1",
            &[&self.app],
        )?;
        if stored(rows[0].try_get::<_, i64>(0))? >= 4096 {
            return Err(Error::Limit("OAuth transactions"));
        }
        let state = Zeroizing::new(crate::authority::random_hex()?);
        let secrets = OAuthSecrets {
            verifier: crate::authority::random_hex()?,
            nonce: crate::authority::random_hex()?,
            invite: invite.to_owned(),
        };
        let envelope = seal(
            key,
            &aad(&self.app, provider, &session_digest),
            &serde_json::to_vec(&secrets).map_err(|_| Error::Denied)?,
        )?;
        self.connection.query(
            "INSERT INTO diamond_identity.oauth_transactions(app,digest,provider,session_digest,envelope,expires) VALUES($1,$2,$3,$4,$5,pg_catalog.clock_timestamp()+interval '10 minutes')",
            &[&self.app, &digest(&self.app, "oauth-state", &state), &provider, &session_digest, &envelope],
        )?;
        Ok((state, secrets))
    }

    pub(in crate::project) fn take_oauth(
        &mut self,
        token: &str,
        provider: &str,
        state: &str,
        key: &[u8; 32],
    ) -> Result<OAuthSecrets> {
        if !valid_secret(state) || !matches!(provider, "google" | "github") {
            return Err(Error::Denied);
        }
        let session_digest = self.oauth_session_binding(token)?;
        let state_digest = digest(&self.app, "oauth-state", state);
        let rows = self.connection.query(
            "SELECT session_digest,envelope FROM diamond_identity.oauth_transactions WHERE app=$1 AND digest=$2 AND provider=$3 AND expires>pg_catalog.clock_timestamp() FOR UPDATE",
            &[&self.app, &state_digest, &provider],
        )?;
        let row = rows.first().ok_or(Error::Denied)?;
        let stored_session: String = stored(row.try_get(0))?;
        let envelope: String = stored(row.try_get(1))?;
        if stored_session != session_digest {
            return Err(Error::Denied);
        }
        let deleted = self.connection.query(
            "DELETE FROM diamond_identity.oauth_transactions WHERE app=$1 AND digest=$2 RETURNING digest",
            &[&self.app, &state_digest],
        )?;
        if deleted.len() != 1 {
            return Err(Error::Denied);
        }
        let plaintext = Zeroizing::new(open(
            key,
            &aad(&self.app, provider, &session_digest),
            &envelope,
        )?);
        wire::strict_from_slice(&plaintext).map_err(|_| Error::Denied)
    }

    pub(in crate::project) fn finish_oauth(
        &mut self,
        token: &str,
        identity: &OAuthIdentity,
        admitted: bool,
        allow_existing_when_unadmitted: bool,
        require_invite: bool,
        invite: &str,
    ) -> Result<Session> {
        if !matches!(identity.provider.as_str(), "google" | "github")
            || identity.subject.is_empty()
            || identity.subject.len() > 255
            || identity.subject.chars().any(char::is_control)
        {
            return Err(Error::Denied);
        }
        let address = email(&identity.email)?;
        self.oauth_session_binding(token)?;
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!(
                "{}:oauth:{}:{}",
                self.app, identity.provider, identity.subject
            )],
        )?;
        let rows = self.connection.query(
            "SELECT username FROM diamond_identity.oauth_identities WHERE app=$1 AND provider=$2 AND subject=$3 FOR UPDATE",
            &[&self.app, &identity.provider, &identity.subject],
        )?;
        let username = if let Some(row) = rows.first() {
            if !admitted && !allow_existing_when_unadmitted {
                return Err(Error::Denied);
            }
            let username: String = stored(row.try_get(0))?;
            self.connection.query(
                "UPDATE diamond_identity.oauth_identities SET email=$4,last_login=pg_catalog.clock_timestamp() WHERE app=$1 AND provider=$2 AND subject=$3",
                &[&self.app, &identity.provider, &identity.subject, &address],
            )?;
            username
        } else {
            if !admitted {
                return Err(Error::Denied);
            }
            if require_invite {
                consume_invite(self, &address, invite)?;
            }
            self.account_capacity()?;
            let username = format!(
                "{}_{}",
                identity.provider,
                &super::super::artifact::hash(
                    format!("{}:{}", identity.provider, identity.subject).as_bytes()
                )[..24]
            );
            let inserted = self.connection.query(
                "INSERT INTO diamond_identity.users(app,username,email,password_hash,verified) VALUES($1,$2,$3,NULL,true) ON CONFLICT DO NOTHING RETURNING username",
                &[&self.app, &username, &address],
            )?;
            if inserted.len() != 1 {
                return Err(Error::Conflict);
            }
            self.connection.query(
                "INSERT INTO diamond_identity.oauth_identities(app,provider,subject,username,email) VALUES($1,$2,$3,$4,$5)",
                &[&self.app, &identity.provider, &identity.subject, &username, &address],
            )?;
            username
        };
        self.oauth_login_session(token, &username)
    }
}

fn consume_invite(store: &mut PostgresStore, address: &str, invite: &str) -> Result<()> {
    if !valid_secret(invite) {
        return Err(Error::Denied);
    }
    let invite_digest = digest(&store.app, "invite", invite);
    let rows = store.connection.query(
        "UPDATE diamond_identity.challenges SET consumed=true WHERE app=$1 AND digest=$2 AND purpose='invite' AND email=$3 AND NOT consumed AND expires>pg_catalog.clock_timestamp() RETURNING digest",
        &[&store.app, &invite_digest, &address],
    )?;
    if rows.len() != 1 {
        return Err(Error::Denied);
    }
    Ok(())
}

fn aad(app: &str, provider: &str, session: &str) -> Vec<u8> {
    format!("diamond-oauth-v1:{app}:{provider}:{session}").into_bytes()
}

fn seal(key: &[u8; 32], aad: &[u8], plaintext: &[u8]) -> Result<String> {
    let cipher = XChaCha20Poly1305::new_from_slice(key).map_err(|_| Error::Denied)?;
    let mut nonce = [0_u8; 24];
    getrandom::fill(&mut nonce).map_err(|_| Error::Denied)?;
    let ciphertext = cipher
        .encrypt(
            XNonce::from_slice(&nonce),
            Payload {
                msg: plaintext,
                aad,
            },
        )
        .map_err(|_| Error::Denied)?;
    let mut envelope = Vec::with_capacity(nonce.len() + ciphertext.len());
    envelope.extend_from_slice(&nonce);
    envelope.extend_from_slice(&ciphertext);
    Ok(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(envelope))
}

fn open(key: &[u8; 32], aad: &[u8], envelope: &str) -> Result<Vec<u8>> {
    if envelope.len() > 8192 {
        return Err(Error::Denied);
    }
    let envelope = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(envelope)
        .map_err(|_| Error::Denied)?;
    let (nonce, ciphertext) = envelope.split_at_checked(24).ok_or(Error::Denied)?;
    XChaCha20Poly1305::new_from_slice(key)
        .map_err(|_| Error::Denied)?
        .decrypt(
            XNonce::from_slice(nonce),
            Payload {
                msg: ciphertext,
                aad,
            },
        )
        .map_err(|_| Error::Denied)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn oauth_envelope_binds_key_and_context_and_detects_tampering() {
        let key = [7_u8; 32];
        let context = b"diamond-oauth-v1:test:google:session";
        let plaintext = br#"{"verifier":"secret","nonce":"nonce","invite":""}"#;
        let Ok(envelope) = seal(&key, context, plaintext) else {
            panic!("synthetic envelope must be sealable");
        };
        let Ok(opened) = open(&key, context, &envelope) else {
            panic!("matching context must open envelope");
        };
        assert_eq!(opened, plaintext);
        assert!(open(&[8_u8; 32], context, &envelope).is_err());
        assert!(open(&key, b"different-context", &envelope).is_err());

        let mut bytes = envelope.into_bytes();
        assert!(!bytes.is_empty());
        let index = bytes.len() - 1;
        bytes[index] = if bytes[index] == b'A' { b'B' } else { b'A' };
        let Ok(tampered) = String::from_utf8(bytes) else {
            panic!("envelope must remain ASCII");
        };
        assert!(open(&key, context, &tampered).is_err());
    }
}
