//! Bounded server-owned invocation records with authenticated binding and outcome.
use super::{PostgresStore, footprint::Access, input, wire};
use crate::{
    authority::random_hex,
    error::{Error, Result},
};
use base64::Engine as _;
use chacha20poly1305::{
    XChaCha20Poly1305, XNonce,
    aead::{Aead as _, KeyInit as _, Payload},
};
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

pub(super) const MAX_RECEIPT: usize = 262_144;
const SCHEMA:&str="CREATE SCHEMA IF NOT EXISTS diamond_actions;
CREATE TABLE IF NOT EXISTS diamond_actions.keys(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app),secret BYTEA NOT NULL CHECK(octet_length(secret)=32));
CREATE TABLE IF NOT EXISTS diamond_actions.invocations(app TEXT NOT NULL REFERENCES diamond_w2.apps(app),id TEXT NOT NULL,username TEXT NOT NULL,endpoint TEXT NOT NULL,subject TEXT NOT NULL,authority TEXT NOT NULL,version TEXT NOT NULL,resource TEXT NOT NULL,parent TEXT NOT NULL,fingerprint TEXT NOT NULL,expires BIGINT NOT NULL,ack BOOLEAN NOT NULL DEFAULT false,envelope TEXT NOT NULL CHECK(octet_length(envelope)<=352000),PRIMARY KEY(app,id));
CREATE INDEX IF NOT EXISTS invocation_owner ON diamond_actions.invocations(app,username,ack,expires);";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub app: String,
    pub username: String,
    pub endpoint: String,
    pub subject: String,
    pub authority: String,
    pub version: String,
    pub resource: String,
    pub parent: String,
    pub fingerprint: String,
    pub expires: i64,
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Receipt {
    pub binding: Binding,
    pub outcome: Option<serde_json::Value>,
    pub accesses: Vec<Access>,
}
#[derive(Debug)]
pub(super) struct Stored {
    pub binding: Binding,
    pub envelope: String,
    pub expired: bool,
}
fn now(store: &mut PostgresStore) -> Result<i64> {
    store
        .connection
        .query(
            "SELECT floor(extract(epoch FROM pg_catalog.clock_timestamp()))::bigint",
            &[],
        )?
        .first()
        .ok_or(Error::Denied)?
        .try_get(0)
        .map_err(|_| Error::Denied)
}
pub(super) fn schema(store: &mut PostgresStore, initialize: bool) -> Result<()> {
    if initialize {
        store.connection.batch(SCHEMA)?;
        let mut key = Zeroizing::new(vec![0u8; 32]);
        getrandom::fill(&mut key).map_err(|_| Error::Random)?;
        store.connection.query(
            "INSERT INTO diamond_actions.keys(app,secret) VALUES($1,$2) ON CONFLICT DO NOTHING",
            &[&store.app, &&key[..]],
        )?;
    }
    store.connection.query("SELECT id,username,endpoint,subject,authority,version,resource,parent,fingerprint,expires,ack,envelope FROM diamond_actions.invocations WHERE app=$1 LIMIT 0",&[&store.app])?;
    key(store).map(|_| ())
}
fn key(store: &mut PostgresStore) -> Result<Zeroizing<Vec<u8>>> {
    let rows = store.connection.query(
        "SELECT secret FROM diamond_actions.keys WHERE app=$1",
        &[&store.app],
    )?;
    let key: Vec<u8> = rows
        .first()
        .ok_or(Error::Denied)?
        .try_get(0)
        .map_err(|_| Error::Denied)?;
    if key.len() != 32 {
        return Err(Error::Denied);
    }
    Ok(Zeroizing::new(key))
}
fn seal(store: &mut PostgresStore, id: &str, receipt: &Receipt) -> Result<String> {
    let plaintext = Zeroizing::new(serde_json::to_vec(receipt).map_err(|_| Error::Denied)?);
    if plaintext.len() > MAX_RECEIPT {
        return Err(Error::Limit("action receipt size"));
    }
    let mut nonce = [0u8; 24];
    getrandom::fill(&mut nonce).map_err(|_| Error::Random)?;
    let aad = format!("diamond-action-v1:{}:{id}", store.app);
    let ciphertext = XChaCha20Poly1305::new_from_slice(&key(store)?)
        .map_err(|_| Error::Denied)?
        .encrypt(
            XNonce::from_slice(&nonce),
            Payload {
                msg: &plaintext,
                aad: aad.as_bytes(),
            },
        )
        .map_err(|_| Error::Denied)?;
    let mut envelope = Vec::with_capacity(24 + ciphertext.len());
    envelope.extend_from_slice(&nonce);
    envelope.extend_from_slice(&ciphertext);
    Ok(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(envelope))
}
pub(super) fn open(store: &mut PostgresStore, id: &str, stored: &Stored) -> Result<Receipt> {
    if stored.envelope.len() > 352_000 {
        return Err(Error::Denied);
    }
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(&stored.envelope)
        .map_err(|_| Error::Denied)?;
    let (nonce, ciphertext) = bytes.split_at_checked(24).ok_or(Error::Denied)?;
    let aad = format!("diamond-action-v1:{}:{id}", store.app);
    let plaintext = Zeroizing::new(
        XChaCha20Poly1305::new_from_slice(&key(store)?)
            .map_err(|_| Error::Denied)?
            .decrypt(
                XNonce::from_slice(nonce),
                Payload {
                    msg: ciphertext,
                    aad: aad.as_bytes(),
                },
            )
            .map_err(|_| Error::Denied)?,
    );
    if plaintext.len() > MAX_RECEIPT {
        return Err(Error::Denied);
    }
    let receipt: Receipt = wire::strict_from_slice(&plaintext).map_err(|_| Error::Denied)?;
    if receipt.binding != stored.binding
        || receipt.accesses.len() > 64
        || receipt.outcome.is_none() && !receipt.accesses.is_empty()
    {
        return Err(Error::Denied);
    }
    Ok(receipt)
}
pub(super) fn load(store: &mut PostgresStore, id: &str) -> Result<Option<Stored>> {
    let rows=store.connection.query("SELECT username,endpoint,subject,authority,version,resource,parent,fingerprint,expires,envelope FROM diamond_actions.invocations WHERE app=$1 AND id=$2 FOR UPDATE",&[&store.app,&id])?;
    let Some(row) = rows.first() else {
        return Ok(None);
    };
    let binding = Binding {
        app: store.app.clone(),
        username: row.try_get(0).map_err(|_| Error::Denied)?,
        endpoint: row.try_get(1).map_err(|_| Error::Denied)?,
        subject: row.try_get(2).map_err(|_| Error::Denied)?,
        authority: row.try_get(3).map_err(|_| Error::Denied)?,
        version: row.try_get(4).map_err(|_| Error::Denied)?,
        resource: row.try_get(5).map_err(|_| Error::Denied)?,
        parent: row.try_get(6).map_err(|_| Error::Denied)?,
        fingerprint: row.try_get(7).map_err(|_| Error::Denied)?,
        expires: row.try_get(8).map_err(|_| Error::Denied)?,
    };
    let expired = binding.expires <= now(store)?;
    Ok(Some(Stored {
        binding,
        envelope: row.try_get(9).map_err(|_| Error::Denied)?,
        expired,
    }))
}
pub(super) fn prepare(
    store: &mut PostgresStore,
    mut binding: Binding,
) -> Result<serde_json::Value> {
    let time = now(store)?;
    store.connection.query(
        "DELETE FROM diamond_actions.invocations WHERE app=$1 AND expires<=$2",
        &[&store.app, &time],
    )?;
    let rows=store.connection.query("SELECT id,fingerprint,authority,version FROM diamond_actions.invocations WHERE app=$1 AND username=$2 AND endpoint=$3 AND subject=$4 AND resource=$5 AND parent=$6 AND NOT ack ORDER BY id LIMIT 1",&[&store.app,&binding.username,&binding.endpoint,&binding.subject,&binding.resource,&binding.parent])?;
    if let Some(row) = rows.first() {
        if row.try_get::<_, String>(1).map_err(|_| Error::Denied)? != binding.fingerprint
            || row.try_get::<_, String>(2).map_err(|_| Error::Denied)? != binding.authority
            || row.try_get::<_, String>(3).map_err(|_| Error::Denied)? != binding.version
        {
            return outcome("ActionConflict");
        }
        let id: String = row.try_get(0).map_err(|_| Error::Denied)?;
        let stored = load(store, &id)?.ok_or(Error::Denied)?;
        match open(store, &id, &stored) {
            Ok(_) => {}
            Err(Error::Denied) => return outcome("ActionCorrupt"),
            Err(error) => return Err(error),
        }
        return Ok(serde_json::json!({"invocation":id}));
    }
    let rows=store.connection.query("SELECT count(*),count(*) FILTER(WHERE username=$2),count(*) FILTER(WHERE username=$2 AND NOT ack) FROM diamond_actions.invocations WHERE app=$1",&[&store.app,&binding.username])?;
    let row = rows.first().ok_or(Error::Denied)?;
    if row.try_get::<_, i64>(0).map_err(|_| Error::Denied)? >= 4096
        || row.try_get::<_, i64>(1).map_err(|_| Error::Denied)? >= 128
        || row.try_get::<_, i64>(2).map_err(|_| Error::Denied)? >= 16
    {
        return Err(Error::Limit("action invocation capacity"));
    }
    binding.expires = time + 86400;
    let id = random_hex()?;
    let envelope = seal(
        store,
        &id,
        &Receipt {
            binding: binding.clone(),
            outcome: None,
            accesses: vec![],
        },
    )?;
    store.connection.query("INSERT INTO diamond_actions.invocations(app,id,username,endpoint,subject,authority,version,resource,parent,fingerprint,expires,envelope) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",&[&store.app,&id,&binding.username,&binding.endpoint,&binding.subject,&binding.authority,&binding.version,&binding.resource,&binding.parent,&binding.fingerprint,&binding.expires,&envelope])?;
    Ok(serde_json::json!({"invocation":id}))
}
pub(super) fn save(store: &mut PostgresStore, id: &str, receipt: &Receipt) -> Result<()> {
    let envelope = seal(store, id, receipt)?;
    let changed = store.connection.query(
        "UPDATE diamond_actions.invocations SET envelope=$3 WHERE app=$1 AND id=$2 RETURNING id",
        &[&store.app, &id, &envelope],
    )?;
    if changed.len() != 1 {
        return Err(Error::Denied);
    }
    Ok(())
}
pub(super) fn outcome(tag: &str) -> Result<serde_json::Value> {
    Ok(serde_json::json!({"value":wire::encode(&crate::action::value(tag,vec![]))?}))
}
pub(super) fn valid_id(id: &str) -> Result<()> {
    if id.len() != 64
        || !id
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err(input("invalid action invocation identity"));
    }
    Ok(())
}
