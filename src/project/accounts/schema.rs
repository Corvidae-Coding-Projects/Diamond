use super::{Config, stored};
use crate::{error::Result, host::HostRuntime};
const SCHEMA:&str="CREATE SCHEMA IF NOT EXISTS diamond_identity;
CREATE TABLE IF NOT EXISTS diamond_identity.apps(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app), version INTEGER NOT NULL CHECK(version=2));
CREATE TABLE IF NOT EXISTS diamond_identity.users(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), username TEXT NOT NULL CHECK(username ~ '^[-_A-Za-z0-9]{1,128}$'), email TEXT NOT NULL CHECK(octet_length(email)<=254), password_hash TEXT, verified BOOLEAN NOT NULL, disabled BOOLEAN NOT NULL DEFAULT false, administrator BOOLEAN NOT NULL DEFAULT false, epoch BIGINT NOT NULL DEFAULT 1 CHECK(epoch>0), PRIMARY KEY(app,username));
CREATE UNIQUE INDEX IF NOT EXISTS identity_password_email ON diamond_identity.users(app,email) WHERE password_hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS diamond_identity.sessions(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), digest TEXT NOT NULL, username TEXT, epoch BIGINT NOT NULL, expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,digest), FOREIGN KEY(app,username) REFERENCES diamond_identity.users(app,username));
CREATE TABLE IF NOT EXISTS diamond_identity.challenges(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), digest TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('invite','verify','recover')), email TEXT NOT NULL, username TEXT, epoch BIGINT NOT NULL, consumed BOOLEAN NOT NULL DEFAULT false, expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,digest));
CREATE INDEX IF NOT EXISTS identity_challenge_subject ON diamond_identity.challenges(app,email,purpose);
CREATE TABLE IF NOT EXISTS diamond_identity.credentials(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), id TEXT NOT NULL, digest TEXT NOT NULL, username TEXT NOT NULL, epoch BIGINT NOT NULL, label TEXT NOT NULL CHECK(octet_length(label)<=160), operations TEXT[] NOT NULL, scopes TEXT NOT NULL CHECK(octet_length(scopes)<=16384), expires TIMESTAMPTZ NOT NULL, revoked BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY(app,id), UNIQUE(app,digest), FOREIGN KEY(app,username) REFERENCES diamond_identity.users(app,username));
CREATE INDEX IF NOT EXISTS identity_credential_owner ON diamond_identity.credentials(app,username);
CREATE TABLE IF NOT EXISTS diamond_identity.oauth_transactions(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), digest TEXT NOT NULL, provider TEXT NOT NULL CHECK(provider IN ('google','github')), session_digest TEXT NOT NULL, envelope TEXT NOT NULL CHECK(octet_length(envelope)<=8192), expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,digest));
CREATE INDEX IF NOT EXISTS identity_oauth_transaction_expiry ON diamond_identity.oauth_transactions(app,expires);
CREATE TABLE IF NOT EXISTS diamond_identity.oauth_identities(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), provider TEXT NOT NULL CHECK(provider IN ('google','github')), subject TEXT NOT NULL CHECK(octet_length(subject)<=255), username TEXT NOT NULL, email TEXT NOT NULL CHECK(octet_length(email)<=254), created TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), last_login TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(), PRIMARY KEY(app,provider,subject), FOREIGN KEY(app,username) REFERENCES diamond_identity.users(app,username));
CREATE TABLE IF NOT EXISTS diamond_identity.rates(app TEXT NOT NULL REFERENCES diamond_identity.apps(app), bucket TEXT NOT NULL, attempts BIGINT NOT NULL, expires TIMESTAMPTZ NOT NULL, PRIMARY KEY(app,bucket));";
pub(in crate::project) fn initialize(
    c: &mut HostRuntime,
    app: &str,
    config: &Config,
) -> Result<()> {
    config.checked()?;
    c.batch(SCHEMA)?;
    c.query(
        "INSERT INTO diamond_identity.apps(app,version) VALUES($1,2) ON CONFLICT(app) DO NOTHING",
        &[&app],
    )?;
    validate(c, app)
}
pub(in crate::project) fn validate(c: &mut HostRuntime, app: &str) -> Result<()> {
    let rows = c.query(
        "SELECT version FROM diamond_identity.apps WHERE app=$1",
        &[&app],
    )?;
    if rows
        .first()
        .map(|r| stored(r.try_get::<_, i32>(0)))
        .transpose()?
        != Some(2)
    {
        return Err(super::super::input(
            "missing/incompatible account schema; initialize explicitly",
        ));
    }
    Ok(())
}
