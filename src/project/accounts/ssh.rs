//! Bounded OpenSSH Ed25519 key enrollment and runtime lookup.

use super::{stored, valid_secret};
use crate::{
    authority::random_hex,
    error::{Error, Result},
    project::{input, storage::PostgresStore},
};
use base64::Engine as _;
use sha2::{Digest, Sha256};

const KEY_TYPE: &str = "ssh-ed25519";
const MAX_KEYS: i64 = 32;

pub(in crate::project) struct AuthorizedSshKey {
    pub id: String,
    pub public_key: String,
}

impl PostgresStore {
    pub(in crate::project) fn add_ssh_key(
        &mut self,
        user: &str,
        label: &str,
        public_key: &str,
    ) -> Result<String> {
        if label.is_empty() || label.len() > 160 || label.chars().any(char::is_control) {
            return Err(input("invalid SSH key label"));
        }
        let account = self.account(user, true)?;
        let (canonical, fingerprint) = parse_public_key(public_key)?;
        self.connection.query(
            "DELETE FROM diamond_identity.ssh_keys WHERE app=$1 AND username=$2 AND (revoked OR epoch<>$3)",
            &[&self.app, &user, &account.epoch],
        )?;
        let rows = self.connection.query(
            "SELECT count(*) FROM diamond_identity.ssh_keys WHERE app=$1 AND username=$2",
            &[&self.app, &user],
        )?;
        if stored(rows[0].try_get::<_, i64>(0))? >= MAX_KEYS {
            return Err(Error::Limit("SSH key count"));
        }
        let id = random_hex()?;
        self.connection.query(
            "INSERT INTO diamond_identity.ssh_keys(app,id,fingerprint,public_key,username,epoch,label) VALUES($1,$2,$3,$4,$5,$6,$7)",
            &[&self.app, &id, &fingerprint, &canonical, &user, &account.epoch, &label],
        )?;
        Ok(id)
    }

    pub(in crate::project) fn list_ssh_keys(&mut self, user: &str) -> Result<String> {
        self.account(user, false)?;
        let rows = self.connection.query(
            "SELECT k.id,k.fingerprint,k.label,k.created::text,(k.revoked OR k.epoch<>u.epoch) FROM diamond_identity.ssh_keys k JOIN diamond_identity.users u ON u.app=k.app AND u.username=k.username WHERE k.app=$1 AND k.username=$2 ORDER BY k.created,k.id LIMIT 32",
            &[&self.app, &user],
        )?;
        let mut values = Vec::with_capacity(rows.len());
        for row in rows {
            values.push(serde_json::json!({
                "id": stored(row.try_get::<_, String>(0))?,
                "fingerprint": stored(row.try_get::<_, String>(1))?,
                "label": stored(row.try_get::<_, String>(2))?,
                "created": stored(row.try_get::<_, String>(3))?,
                "revoked": stored(row.try_get::<_, bool>(4))?,
            }));
        }
        Ok(serde_json::Value::Array(values).to_string())
    }

    pub(in crate::project) fn revoke_ssh_key(&mut self, user: &str, id: &str) -> Result<()> {
        if !valid_secret(id) {
            return Err(input("invalid SSH key id"));
        }
        self.account(user, true)?;
        let rows = self.connection.query(
            "UPDATE diamond_identity.ssh_keys SET revoked=true WHERE app=$1 AND username=$2 AND id=$3 AND NOT revoked RETURNING id",
            &[&self.app, &user, &id],
        )?;
        if rows.len() != 1 {
            return Err(Error::Denied);
        }
        Ok(())
    }

    pub(in crate::project) fn ssh_key_by_fingerprint(
        &mut self,
        fingerprint: &str,
    ) -> Result<AuthorizedSshKey> {
        valid_fingerprint(fingerprint)?;
        let rows = self.connection.query(
            "SELECT k.id,k.public_key FROM diamond_identity.ssh_keys k JOIN diamond_identity.users u ON u.app=k.app AND u.username=k.username WHERE k.app=$1 AND k.fingerprint=$2 AND NOT k.revoked AND k.epoch=u.epoch AND NOT u.disabled AND u.verified",
            &[&self.app, &fingerprint],
        )?;
        if rows.len() != 1 {
            return Err(Error::Unauthenticated);
        }
        Ok(AuthorizedSshKey {
            id: stored(rows[0].try_get(0))?,
            public_key: stored(rows[0].try_get(1))?,
        })
    }
}

pub(in crate::project) fn parse_public_key(value: &str) -> Result<(String, String)> {
    if value.len() > 512 || value.chars().any(char::is_control) {
        return Err(input("invalid SSH public key"));
    }
    let mut fields = value.split_ascii_whitespace();
    if fields.next() != Some(KEY_TYPE) {
        return Err(input("only OpenSSH Ed25519 public keys are supported"));
    }
    let encoded = fields
        .next()
        .ok_or_else(|| input("invalid SSH public key"))?;
    if fields.collect::<Vec<_>>().join(" ").len() > 160 {
        return Err(input("invalid SSH public key"));
    }
    let blob = base64::engine::general_purpose::STANDARD_NO_PAD
        .decode(encoded)
        .or_else(|_| base64::engine::general_purpose::STANDARD.decode(encoded))
        .map_err(|_| input("invalid SSH public key"))?;
    let mut cursor = 0_usize;
    let algorithm = ssh_field(&blob, &mut cursor)?;
    let key = ssh_field(&blob, &mut cursor)?;
    if cursor != blob.len() || algorithm != KEY_TYPE.as_bytes() || key.len() != 32 {
        return Err(input("invalid SSH Ed25519 key encoding"));
    }
    let canonical_blob = base64::engine::general_purpose::STANDARD_NO_PAD.encode(&blob);
    let digest = Sha256::digest(&blob);
    let fingerprint = format!(
        "SHA256:{}",
        base64::engine::general_purpose::STANDARD_NO_PAD.encode(digest)
    );
    Ok((format!("{KEY_TYPE} {canonical_blob}"), fingerprint))
}

fn ssh_field<'a>(bytes: &'a [u8], cursor: &mut usize) -> Result<&'a [u8]> {
    let length = bytes
        .get(*cursor..cursor.saturating_add(4))
        .and_then(|value| <[u8; 4]>::try_from(value).ok())
        .map(u32::from_be_bytes)
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| input("invalid SSH public key"))?;
    *cursor = cursor.saturating_add(4);
    let value = bytes
        .get(*cursor..cursor.saturating_add(length))
        .ok_or_else(|| input("invalid SSH public key"))?;
    *cursor = cursor.saturating_add(length);
    Ok(value)
}

fn valid_fingerprint(value: &str) -> Result<()> {
    let encoded = value
        .strip_prefix("SHA256:")
        .ok_or(Error::Unauthenticated)?;
    let digest = base64::engine::general_purpose::STANDARD_NO_PAD
        .decode(encoded)
        .map_err(|_| Error::Unauthenticated)?;
    if digest.len() != 32 {
        return Err(Error::Unauthenticated);
    }
    Ok(())
}
