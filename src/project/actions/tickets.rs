//! One-attempt submission proofs. These retain no action results or form bodies.

use super::super::{artifact, input, storage::PostgresStore, wire};
use crate::error::{Error, Result};
use crate::value::Value;

const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_actions;
CREATE TABLE IF NOT EXISTS diamond_actions.tickets(app TEXT NOT NULL REFERENCES diamond_w2.apps(app), digest TEXT NOT NULL, subject TEXT NOT NULL, username TEXT NOT NULL, binding TEXT NOT NULL, expires TIMESTAMPTZ NOT NULL, consumed BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY(app,digest));
CREATE INDEX IF NOT EXISTS action_ticket_capacity ON diamond_actions.tickets(app,username,expires);";

pub(in crate::project) fn schema(store: &mut PostgresStore, initialize: bool) -> Result<()> {
    if initialize {
        store.connection.batch(SCHEMA)?;
    }
    store.connection.query("SELECT digest,subject,username,binding,expires,consumed FROM diamond_actions.tickets WHERE app=$1 LIMIT 0", &[&store.app])?;
    Ok(())
}

fn fingerprint(contract: &str, operation: &str, arguments: &[Value]) -> Result<String> {
    let arguments = arguments
        .iter()
        .map(wire::encode)
        .collect::<Result<Vec<_>>>()?;
    let bytes = serde_json::to_vec(&(contract, operation, arguments)).map_err(|_| Error::Denied)?;
    Ok(artifact::hash(&bytes))
}

pub(in crate::project) struct Submission<'a> {
    pub contract: &'a str,
    pub operation: &'a str,
    pub subject: &'a str,
    pub username: &'a str,
    pub arguments: &'a [Value],
}

impl std::fmt::Debug for Submission<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("Submission")
            .field("operation", &self.operation)
            .finish_non_exhaustive()
    }
}

impl Submission<'_> {
    pub(in crate::project) fn control(
        &self,
        store: &mut PostgresStore,
        ticket: Option<&str>,
        prepare: bool,
    ) -> Result<Option<serde_json::Value>> {
        if prepare {
            if ticket.is_some() {
                return Err(input(
                    "preparing a submission does not accept an existing proof",
                ));
            }
            let result = self.prepare(store);
            let ticket = store.finish_request(result)?;
            return Ok(Some(serde_json::json!({"ticket": ticket})));
        }
        let ticket = ticket
            .ok_or_else(|| input("action requires a runtime-issued single-use submission proof"))?;
        if ticket.len() != 64
            || !ticket
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(input("invalid action submission proof"));
        }
        let result = self.consume(store, ticket);
        // A racing/failed proof consumption cannot establish the outcome of
        // another attempt. Never report that attempt as confirmed aborted.
        if matches!(store.finish_request(result), Ok(true)) {
            return Ok(None);
        }
        Ok(Some(
            serde_json::json!({"value": wire::encode(&crate::action::value("ActionUnknown", vec![]))?}),
        ))
    }

    pub(in crate::project) fn prepare(&self, store: &mut PostgresStore) -> Result<String> {
        let binding = fingerprint(self.contract, self.operation, self.arguments)?;
        let subject = artifact::hash(self.subject.as_bytes());
        // Serialize capacity accounting across nodes; this is native control
        // state, separate from application policy and the mutation transaction.
        store.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{}:action-tickets", store.app)],
        )?;
        store.connection.query("DELETE FROM diamond_actions.tickets WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()", &[&store.app])?;
        let count = store.connection.query("SELECT count(*),count(*) FILTER(WHERE username=$2 AND NOT consumed) FROM diamond_actions.tickets WHERE app=$1", &[&store.app, &self.username])?;
        let total: i64 = count[0].try_get(0).map_err(|_| Error::Denied)?;
        let pending: i64 = count[0].try_get(1).map_err(|_| Error::Denied)?;
        if total >= 4096 || pending >= 16 {
            return Err(Error::Limit("action submission capacity"));
        }
        let ticket = crate::authority::random_hex()?;
        let digest = artifact::hash(ticket.as_bytes());
        store.connection.query("INSERT INTO diamond_actions.tickets(app,digest,subject,username,binding,expires) VALUES($1,$2,$3,$4,$5,pg_catalog.clock_timestamp()+interval '5 minutes')", &[&store.app, &digest, &subject, &self.username, &binding])?;
        Ok(ticket)
    }

    pub(in crate::project) fn consume(
        &self,
        store: &mut PostgresStore,
        ticket: &str,
    ) -> Result<bool> {
        if ticket.len() != 64
            || !ticket
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(input("invalid action submission proof"));
        }
        let digest = artifact::hash(ticket.as_bytes());
        let subject = artifact::hash(self.subject.as_bytes());
        let binding = fingerprint(self.contract, self.operation, self.arguments)?;
        let rows = store.connection.query("UPDATE diamond_actions.tickets SET consumed=true WHERE app=$1 AND digest=$2 AND subject=$3 AND username=$4 AND binding=$5 AND NOT consumed AND expires>pg_catalog.clock_timestamp() RETURNING digest", &[&store.app, &digest, &subject, &self.username, &binding])?;
        Ok(rows.len() == 1)
    }
}
