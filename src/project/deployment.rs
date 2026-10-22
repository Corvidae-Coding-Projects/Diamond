//! Explicit deployment validation, lifecycle exclusion, and redacted operational endpoints.
mod backup;
use super::{
    CheckedProject,
    connected::{self, Server},
    input,
    storage::PostgresStore,
};
use crate::{Error, error::Result, host::HostRuntime};
use axum::{extract::Request, http::StatusCode, response::Response};
use std::{
    path::Path,
    sync::{Arc, atomic::Ordering},
};

pub(super) fn guard(
    project: &CheckedProject,
    configuration: &str,
    exclusive: bool,
) -> Result<HostRuntime> {
    let mut c = HostRuntime::connect_worker(configuration)?;
    let app = super::artifact::hash(project.manifest.name.as_bytes());
    let sql = if exclusive {
        "SELECT pg_catalog.pg_try_advisory_lock(pg_catalog.hashtextextended($1,81))"
    } else {
        "SELECT pg_catalog.pg_try_advisory_lock_shared(pg_catalog.hashtextextended($1,81))"
    };
    let rows = c.query(sql, &[&app])?;
    if !rows[0].try_get::<_, bool>(0).map_err(|_| Error::Denied)? {
        return Err(input(
            "application is running or undergoing maintenance; stop all servers and workers first",
        ));
    }
    Ok(c)
}
pub(super) fn origin(origin: &str) -> Result<String> {
    if origin.starts_with("http://") {
        return connected::development_origin(origin);
    }
    let url = url::Url::parse(origin).map_err(|_| input("invalid public origin"))?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
        || origin != url.origin().ascii_serialization()
    {
        return Err(input(
            "deployment requires an exact HTTPS origin without credentials, path, query or fragment",
        ));
    }
    Ok(origin.trim_start_matches("https://").to_owned())
}
pub(super) async fn health(server: Arc<Server>, request: Request) -> Response {
    if request.method() != "GET" {
        return connected::failure(&Error::Denied);
    }
    match request.uri().path() {
        "/_diamond/health/live" => {
            connected::json_response(StatusCode::OK, &serde_json::json!({"live":true}))
        }
        "/_diamond/health/ready" => {
            if server.draining.load(Ordering::Relaxed) {
                return connected::failure(&Error::Limit("draining"));
            }
            let s = Arc::clone(&server);
            let result = tokio::task::spawn_blocking(move || {
                let _objects = super::files::Store::open(
                    &s.project,
                    &s.secrets.configuration,
                    &s.runtime,
                    false,
                )?;
                let mut db = PostgresStore::open_control(
                    &s.project,
                    &s.secrets.configuration,
                    s.pool.as_ref(),
                )?;
                db.finish_request(Ok(()))
            })
            .await;
            if matches!(result, Ok(Ok(()))) {
                connected::json_response(
                    StatusCode::OK,
                    &serde_json::json!({"ready":true,"contract":server.contract}),
                )
            } else {
                connected::failure(&Error::Denied)
            }
        }
        "/_diamond/health/metrics" => {
            // Operational counters contain no users, payloads, tokens, paths or configuration.
            connected::json_response(
                StatusCode::OK,
                &serde_json::json!({"requests":server.requests.load(Ordering::Relaxed),"active_requests":16-server.permits.available_permits(),"active_sockets":32-server.sockets.available_permits(),"draining":server.draining.load(Ordering::Relaxed)}),
            )
        }
        _ => connected::failure(&Error::Denied),
    }
}
impl CheckedProject {
    /// Validate deployment authority and shared state without opening a listener or deploying.
    /// HTTPS terminates at a trusted proxy forwarding the canonical Host unchanged.
    ///
    /// # Errors
    /// Rejects invalid origins, missing authority, unsafe private directories, and mismatched stores.
    ///
    /// ```no_run
    /// # fn demo(p:&diamond::CheckedProject)->Result<(),diamond::Error> {
    /// let report=p.validate_deployment("host=/tmp/pg dbname=app","https://app.example.invalid",&diamond::RuntimeGrants::default(),std::path::Path::new("/private/mail"))?;
    /// assert!(report.contains("contract"));
    /// # Ok(()) }
    /// ```
    pub fn validate_deployment(
        &self,
        configuration: &str,
        public_origin: &str,
        runtime: &crate::RuntimeGrants,
        mailbox: &Path,
    ) -> Result<String> {
        super::synchronous_context()?;
        let authority = origin(public_origin)?;
        if self.manifest.identity.is_none() {
            return Err(input("deployment requires accounts-v1"));
        }
        let _mail = super::accounts::Mailbox::open(mailbox)?;
        for e in &self.server.grants {
            if let crate::authority::Effect::Host(n) = e
                && let Some(n) = n.strip_prefix("Http<").and_then(|n| n.strip_suffix('>'))
            {
                runtime.require(n)?;
            }
        }
        let _objects = super::files::Store::open(self, configuration, runtime, false)?;
        let mut db = PostgresStore::open_control(self, configuration, None)?;
        db.finish_request(Ok(()))?;
        Ok(serde_json::json!({"valid":true,"origin":public_origin,"authority":authority,"contract":super::artifact::contract_id(self)?,"capabilities":self.manifest.provides,"servers":2,"workers":if self.jobs.is_empty(){0}else{2},"proxy":"loopback only; preserve Host; strip Forwarded and X-Forwarded-*; TLS at edge","migration":"stop all instances; current version only","backup":"quiesced encrypted snapshots; isolated restore; jobs paused"}).to_string())
    }
    /// Select explicit job recovery after an isolated restore. `resume` permits at-least-once redelivery.
    ///
    /// # Errors
    /// Rejects unknown policies, active instances, incompatible schemas, or database failure.
    ///
    /// ```no_run
    /// # fn demo(p:&diamond::CheckedProject)->Result<(),diamond::Error> {
    /// p.recover_jobs("host=/tmp/pg dbname=app","cancel")?;
    /// # Ok(()) }
    /// ```
    pub fn recover_jobs(&self, configuration: &str, policy: &str) -> Result<()> {
        super::synchronous_context()?;
        if !["resume", "cancel"].contains(&policy) {
            return Err(input("recovery requires resume or cancel"));
        }
        let _guard = guard(self, configuration, true)?;
        let mut db = PostgresStore::open_control(self, configuration, None)?;
        if policy == "cancel" {
            db.connection.query("UPDATE diamond_w7.jobs SET state='cancelled',fence=fence+1,outcome='restore_cancelled' WHERE app=$1 AND state IN ('pending','running')", &[&db.app])?;
        } else {
            db.connection.query("UPDATE diamond_w7.jobs SET state='pending',fence=fence+1,lease=NULL,due=clock_timestamp() WHERE app=$1 AND state='running'", &[&db.app])?;
        }
        db.connection.query(
            "UPDATE diamond_w7.apps SET paused=false WHERE app=$1",
            &[&db.app],
        )?;
        db.finish_request(Ok(()))
    }
}
