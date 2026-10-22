//! Independently committed shared admission decisions survive later rollbacks.
use super::super::{CheckedProject, storage::PostgresStore};
use crate::{
    error::{Error, Result},
    relational::Pool,
};
use std::sync::Arc;
pub(in crate::project) fn charge(
    project: &CheckedProject,
    configuration: &str,
    pool: Option<&Arc<Pool>>,
    class: &str,
    peer: &str,
    subject: &str,
) -> Result<()> {
    let mut store = PostgresStore::open_control(project, configuration, pool)?;
    let app = store.app.clone();
    let result = (|| {
        store.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{app}:identity-rate")],
        )?;
        store.connection.query("DELETE FROM diamond_identity.rates WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()",&[&app])?;
        let count = store.connection.query(
            "SELECT count(*) FROM diamond_identity.rates WHERE app=$1",
            &[&app],
        )?;
        if super::stored(count[0].try_get::<_, i64>(0))? > 4090 {
            return Err(Error::Limit("identity rate"));
        }
        for (bucket, limit) in [
            (
                format!("{class}:global"),
                if class == "api" { 600 } else { 120 },
            ),
            (
                format!("{class}:peer:{}", super::digest(&app, "peer", peer)),
                if class == "api" { 300 } else { 100 },
            ),
            (
                format!(
                    "{class}:subject:{}",
                    super::digest(&app, "subject", subject)
                ),
                if class == "api" { 120 } else { 12 },
            ),
        ] {
            if subject.is_empty() && bucket.contains(":subject:") {
                continue;
            }
            let rows=store.connection.query("INSERT INTO diamond_identity.rates(app,bucket,attempts,expires) VALUES($1,$2,1,pg_catalog.clock_timestamp()+interval '1 minute') ON CONFLICT(app,bucket) DO UPDATE SET attempts=diamond_identity.rates.attempts+1 RETURNING attempts",&[&app,&bucket])?;
            if super::stored(rows[0].try_get::<_, i64>(0))? > limit {
                return Ok(false);
            }
        }
        Ok(true)
    })();
    match result {
        Ok(allowed) => {
            store.finish_request(Ok(()))?;
            if allowed {
                Ok(())
            } else {
                Err(Error::Limit("identity rate"))
            }
        }
        Err(e) => {
            let _rollback = store.finish_request::<()>(Err(Error::Aborted));
            Err(e)
        }
    }
}
