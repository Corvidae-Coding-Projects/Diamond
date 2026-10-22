//! Worker lifecycle: one bounded claim at a time, no captured request authority.
use super::{
    BTreeSet, CheckedProject, Effects, Error, PostgresStore, Result, Scope, Value, ValueKind,
    input, wire,
};
use crate::authority::{Principal, Restrictions};
use std::{sync::Arc, time::Duration};
use zeroize::Zeroizing;

#[derive(Debug)]
struct Claim {
    id: String,
    binding: String,
    parent: String,
    owner: String,
    payload: String,
    restrictions: String,
    unscoped: bool,
    fence: i64,
    attempts: i32,
}
fn claim(project: &CheckedProject, configuration: &str) -> Result<Option<Claim>> {
    let mut db = PostgresStore::open_control(project, configuration, None)?;
    let version = super::version(project)?;
    // Expired final attempts become visible dead letters even without another claim.
    for (binding, b) in &project.manifest.jobs {
        db.connection.query("UPDATE diamond_w7.jobs SET state='dead',outcome='attempts_exhausted',fence=fence+1 WHERE app=$1 AND binding=$2 AND state='running' AND lease<clock_timestamp() AND attempts >= $3", &[&db.app,&binding,&b.max_attempts])?;
    }
    let rows=db.connection.query("WITH candidate AS (SELECT j.id FROM diamond_w7.jobs j JOIN diamond_w7.apps a ON j.app=a.app WHERE j.app=$1 AND j.version=$2 AND NOT a.paused AND (j.state='pending' AND j.due<=clock_timestamp() OR j.state='running' AND j.lease<clock_timestamp()) ORDER BY j.due,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1) UPDATE diamond_w7.jobs j SET state='running',attempts=j.attempts+1,fence=j.fence+1,lease=clock_timestamp()+INTERVAL '30 seconds' FROM candidate c WHERE j.app=$1 AND j.id=c.id RETURNING j.id,j.binding,j.parent,j.owner,j.payload,j.restrictions,j.unscoped,j.fence,j.attempts", &[&db.app,&version])?;
    let value = rows
        .first()
        .map(|r| -> Result<Claim> {
            Ok(Claim {
                id: r.try_get(0).map_err(|_| Error::Denied)?,
                binding: r.try_get(1).map_err(|_| Error::Denied)?,
                parent: r.try_get(2).map_err(|_| Error::Denied)?,
                owner: r.try_get(3).map_err(|_| Error::Denied)?,
                payload: r.try_get(4).map_err(|_| Error::Denied)?,
                restrictions: r.try_get(5).map_err(|_| Error::Denied)?,
                unscoped: r.try_get(6).map_err(|_| Error::Denied)?,
                fence: r.try_get(7).map_err(|_| Error::Denied)?,
                attempts: r.try_get(8).map_err(|_| Error::Denied)?,
            })
        })
        .transpose()?;
    db.finish_request(Ok(value))
}
fn execute(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
    job: &Claim,
) -> Result<()> {
    let mut db = PostgresStore::open_with(project, configuration, false, None)?;
    // Do not lock the job row across handler execution. Cancellation and lease
    // recovery must be able to advance the fence while an attempt is in flight;
    // the conditional completion update below then makes this transaction lose
    // its serialization race and rolls back any database changes. External
    // bytes may already have been sent and retain the documented at-least-once
    // contract.
    let rows=db.connection.query("SELECT id FROM diamond_w7.jobs WHERE app=$1 AND id=$2 AND fence=$3 AND state='running' AND lease>clock_timestamp()", &[&db.app,&job.id,&job.fence])?;
    if rows.is_empty() {
        return Err(Error::Conflict);
    }
    let users=db.connection.query("SELECT username FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled FOR SHARE", &[&db.app,&job.owner])?;
    if users.is_empty() {
        return Err(Error::Unauthenticated);
    }
    let b = project
        .manifest
        .jobs
        .get(&job.binding)
        .ok_or(Error::Denied)?;
    let op = project.jobs.get(&job.binding).ok_or(Error::Denied)?;
    let grants = b
        .grants
        .iter()
        .map(|s| super::super::capability_effect(s))
        .collect::<Result<Effects>>()?;
    let principal = Principal::User {
        id: job.owner.clone(),
        roles: BTreeSet::new(),
    };
    let restrictions: Option<Restrictions> =
        serde_json::from_str(&job.restrictions).map_err(|_| Error::Denied)?;
    let scope = restrictions.as_ref().map_or_else(
        || Scope::new(&principal, &grants),
        |r| Scope::restricted(&principal, &grants, r, job.unscoped),
    );
    db.attachment(&scope, &b.resource, &job.parent, true)?;
    let payload = wire::decode(
        &op.arguments[1],
        &serde_json::from_str(&job.payload).map_err(|_| Error::Denied)?,
        &project.server,
    )?;
    let result = crate::eval::Interpreter::new(&project.server, &scope, None, None)
        .with_integrations(runtime)
        .with_job_key(&job.id)
        .with_postgres(&mut db)
        .function(
            &op.function,
            &[Value(ValueKind::Text(job.parent.clone())), payload],
        )
        .and_then(Value::into_result);
    let result=result.and_then(|value| {
        let output=value.display_text()?;
        if output.len()>16384 {return Err(Error::Limit("job result"));}
        let rows=db.connection.query("UPDATE diamond_w7.jobs SET state='done',outcome='completed',lease=NULL WHERE app=$1 AND id=$2 AND fence=$3 AND state='running' AND lease>clock_timestamp() RETURNING id", &[&db.app,&job.id,&job.fence])?;
        if rows.is_empty() {Err(Error::Conflict)}else{Ok(())}
    });
    db.finish_request(result)
}
fn step(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
) -> Result<()> {
    let Some(job) = claim(project, configuration).map_err(|_| input("worker claim failed"))? else {
        return Ok(());
    };
    if let Err(error) = execute(project, configuration, runtime, &job) {
        eprintln!(
            "{}",
            serde_json::json!({"event":"job_attempt_failed","class":match &error {Error::Denied=>"denied",Error::Unauthenticated=>"unauthenticated",Error::Conflict=>"conflict",Error::Input(_)=>"input_or_database",Error::OutcomeUnknown(_)=>"unknown",_=>"failure"}})
        );
        let mut db = PostgresStore::open_control(project, configuration, None)?;
        let terminal = matches!(error, Error::Denied | Error::Unauthenticated)
            || job.attempts >= project.manifest.jobs[&job.binding].max_attempts;
        let state = if terminal { "dead" } else { "pending" };
        let outcome = if terminal {
            "denied_or_exhausted"
        } else {
            "retry_after_failure_or_unknown"
        };
        let delay = 1_i32
            .checked_shl(u32::try_from(job.attempts.saturating_sub(1)).unwrap_or(8))
            .unwrap_or(60)
            .min(60);
        db.connection.query("UPDATE diamond_w7.jobs SET state=$4,outcome=$5,lease=NULL,due=clock_timestamp()+$6::integer*INTERVAL '1 second' WHERE app=$1 AND id=$2 AND fence=$3 AND state='running'", &[&db.app,&job.id,&job.fence,&state,&outcome,&delay])?;
        db.finish_request(Ok(()))?;
    }
    Ok(())
}
impl CheckedProject {
    /// Run a durable worker until SIGINT, draining the current bounded handler.
    /// External effects are at least once and require receiver-side deduplication.
    ///
    /// # Errors
    /// Rejects missing jobs, runtime grants, incompatible state, or unavailable stores.
    ///
    /// ```no_run
    /// # fn demo(project: &diamond::CheckedProject) -> Result<(), diamond::Error> {
    /// project.work_jobs("host=/tmp/pg dbname=app", diamond::RuntimeGrants::default())?;
    /// # Ok(()) }
    /// ```
    pub fn work_jobs(&self, configuration: &str, runtime: crate::RuntimeGrants) -> Result<()> {
        super::super::synchronous_context()?;
        let _deployment_guard = super::super::deployment::guard(self, configuration, false)?;
        if self.jobs.is_empty() {
            return Err(input("worker requires declared jobs"));
        }
        for b in self.manifest.jobs.values() {
            for grant in &b.grants {
                if let Some(n) = grant
                    .strip_prefix("Http<")
                    .and_then(|n| n.strip_suffix('>'))
                {
                    runtime.require(n)?;
                }
            }
        }
        let mut db = PostgresStore::open_control(self, configuration, None)?;
        db.finish_request(Ok(()))?;
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .max_blocking_threads(1)
            .enable_all()
            .build()?;
        let project = Arc::new(self.clone());
        let configuration = Arc::new(Zeroizing::new(configuration.to_owned()));
        let runtime = Arc::new(runtime);
        rt.block_on(async {
            let stop=tokio::signal::ctrl_c();tokio::pin!(stop);
            loop {
                let p=Arc::clone(&project);let c=Arc::clone(&configuration);let r=Arc::clone(&runtime);
                let work=tokio::task::spawn_blocking(move||step(&p,&c,&r));
                tokio::pin!(work);
                tokio::select! { result=&mut work=>{result.map_err(|_|Error::Denied)??;}, _=&mut stop=>{work.await.map_err(|_|Error::Denied)??;break;} }
                tokio::select! { ()=tokio::time::sleep(Duration::from_millis(250))=>{}, _=&mut stop=>break }
            }
            Ok(())
        })
    }
}
