//! Checked admission, workload isolation, verified-identity quotas and owned execution.
use super::{input, retry};
use crate::error::{Error, Result};
use futures_util::StreamExt as _;
use serde::{Deserialize, Serialize};
use std::{
    cell::RefCell,
    collections::{BTreeMap, BTreeSet},
    future::Future,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Class {
    Interactive,
    Control,
    Transfer,
    Background,
}
impl Class {
    fn index(self) -> usize {
        match self {
            Self::Interactive => 0,
            Self::Control => 1,
            Self::Transfer => 2,
            Self::Background => 3,
        }
    }
}
pub(super) fn classify(path: &str) -> Class {
    if path.starts_with("/_diamond/git/")
        || path.starts_with("/_diamond/files/")
        || path.starts_with("/_diamond/delivery/")
        || path.starts_with("/v2/")
    {
        Class::Transfer
    } else if path.starts_with("/_diamond/health/")
        || path.starts_with("/_diamond/oauth/")
        || path.starts_with("/_diamond/accounts/")
        || ["/_diamond/session", "/_diamond/login", "/_diamond/logout"].contains(&path)
    {
        Class::Control
    } else {
        Class::Interactive
    }
}
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(super) struct Workload {
    pub request_limit: usize,
    pub execution_limit: usize,
    pub timeout_ms: u64,
    pub reserved_requests: Option<usize>,
    pub reserved_execution: Option<usize>,
}
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(super) struct Workloads {
    pub interactive: Workload,
    pub control: Workload,
    pub transfer: Workload,
    pub background: Workload,
}
impl Workloads {
    fn get(self, class: Class) -> Workload {
        match class {
            Class::Interactive => self.interactive,
            Class::Control => self.control,
            Class::Transfer => self.transfer,
            Class::Background => self.background,
        }
    }
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(super) struct Fairness {
    pub peer_limit: usize,
    pub principal_limit: usize,
    pub max_peers: usize,
    pub max_principals: usize,
    pub tenants: BTreeMap<String, String>,
}
impl Default for Fairness {
    fn default() -> Self {
        Self {
            peer_limit: 0,
            principal_limit: 0,
            max_peers: 1024,
            max_principals: 1024,
            tenants: BTreeMap::new(),
        }
    }
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(super) struct Config {
    pub request_limit: usize,
    pub queue_limit: usize,
    pub queue_timeout_ms: u64,
    pub worker_threads: usize,
    pub blocking_threads: usize,
    pub maintenance_threads: usize,
    pub database_connections: usize,
    pub request_timeout_ms: u64,
    pub stream_timeout_ms: u64,
    pub client_event_timeout_ms: u64,
    pub cleanup_timeout_ms: u64,
    pub workloads: Workloads,
    pub fairness: Fairness,
    pub retry: retry::Config,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            request_limit: 16,
            queue_limit: 128,
            queue_timeout_ms: 1000,
            worker_threads: 2,
            blocking_threads: 4,
            maintenance_threads: 2,
            database_connections: 4,
            request_timeout_ms: 10_000,
            stream_timeout_ms: 60_000,
            client_event_timeout_ms: 0,
            cleanup_timeout_ms: 200,
            workloads: Workloads::default(),
            fairness: Fairness::default(),
            retry: retry::Config::default(),
        }
    }
}
impl Config {
    fn workload(&self, class: Class) -> Workload {
        let value = self.workloads.get(class);
        Workload {
            request_limit: if class == Class::Background {
                0
            } else if value.request_limit != 0 {
                value.request_limit
            } else if class == Class::Transfer {
                (self.request_limit / 2).max(1)
            } else {
                self.request_limit
            },
            execution_limit: if value.execution_limit != 0 {
                value.execution_limit
            } else if class == Class::Background {
                1
            } else if class == Class::Transfer {
                (self.blocking_threads / 2).max(1)
            } else {
                self.blocking_threads
            },
            timeout_ms: if value.timeout_ms != 0 {
                value.timeout_ms
            } else if class == Class::Background {
                self.request_timeout_ms.min(3000)
            } else if class == Class::Transfer {
                self.stream_timeout_ms
            } else {
                self.request_timeout_ms
            },
            reserved_requests: Some(value.reserved_requests.unwrap_or_else(|| {
                if matches!(class, Class::Interactive | Class::Control) && self.request_limit >= 4 {
                    (self.request_limit / 8).max(1)
                } else {
                    0
                }
            })),
            reserved_execution: Some(value.reserved_execution.unwrap_or_else(|| {
                usize::from(
                    matches!(class, Class::Interactive | Class::Control)
                        && self.blocking_threads >= 3,
                )
            })),
        }
    }
    pub(super) fn client_timeout(&self) -> u64 {
        if self.client_event_timeout_ms != 0 {
            return self.client_event_timeout_ms;
        }
        [Class::Interactive, Class::Control, Class::Transfer]
            .into_iter()
            .map(|class| self.workload(class).timeout_ms)
            .max()
            .unwrap_or(self.request_timeout_ms)
            .saturating_add(5000)
    }
    pub(super) fn client(&self) -> serde_json::Value {
        serde_json::json!({"event_timeout_ms":self.client_timeout(),"retries":self.retry.client_retries,
            "max_backoff_ms":self.retry.client_max_backoff_ms,"jitter_ms":self.retry.client_jitter_ms})
    }
    pub(super) fn checked(&self) -> Result<()> {
        if !(1..=1024).contains(&self.request_limit)
            || self.queue_limit > 4096
            || !(1..=5000).contains(&self.queue_timeout_ms)
            || !(1..=16).contains(&self.worker_threads)
            || !(1..=64).contains(&self.blocking_threads)
            || !(1..=8).contains(&self.maintenance_threads)
            || !(1..=64).contains(&self.database_connections)
            || self.database_connections > self.blocking_threads
            || self.blocking_threads > self.request_limit
            || !(10..=120_000).contains(&self.request_timeout_ms)
            || !(10..=600_000).contains(&self.stream_timeout_ms)
            || self.client_timeout() > 610_000
            || !(10..=5000).contains(&self.cleanup_timeout_ms)
            || !(1..=65_536).contains(&self.fairness.max_peers)
            || !(1..=65_536).contains(&self.fairness.max_principals)
            || self.fairness.peer_limit > self.request_limit
            || self.fairness.principal_limit > self.request_limit
            || self.fairness.tenants.len() > 10_000
        {
            return Err(input(
                "invalid runtime capacity, deadline or fairness bounds",
            ));
        }
        let mut reserved_requests = 0;
        let mut reserved_execution = 0;
        for class in [
            Class::Interactive,
            Class::Control,
            Class::Transfer,
            Class::Background,
        ] {
            let value = self.workload(class);
            if class == Class::Background
                && (self.workloads.background.request_limit != 0
                    || self
                        .workloads
                        .background
                        .reserved_requests
                        .unwrap_or_default()
                        != 0)
            {
                return Err(input(
                    "background polls use execution limits, not HTTP request slots",
                ));
            }
            if value.request_limit > self.request_limit
                || value.execution_limit > self.blocking_threads
                || value.reserved_requests.unwrap_or_default() > value.request_limit
                || value.reserved_execution.unwrap_or_default() > value.execution_limit
                || !(10..=600_000).contains(&value.timeout_ms)
                || (class != Class::Background && self.client_timeout() < value.timeout_ms)
            {
                return Err(input(
                    "workload limits exceed runtime capacity or client deadline",
                ));
            }
            reserved_requests += value.reserved_requests.unwrap_or_default();
            reserved_execution += value.reserved_execution.unwrap_or_default();
        }
        if reserved_requests >= self.request_limit || reserved_execution >= self.blocking_threads {
            return Err(input(
                "runtime reservations must leave at least one shared request and execution slot",
            ));
        }
        if self.fairness.tenants.iter().any(|(principal, group)| {
            !crate::auth::valid_id(principal) || !crate::auth::valid_id(group)
        }) {
            return Err(input(
                "tenant fairness groups need valid principal and group identifiers",
            ));
        }
        self.retry.checked()
    }
}
#[derive(Debug)]
struct Bucket {
    semaphore: Arc<Semaphore>,
    users: usize,
}
#[derive(Debug)]
struct Registry {
    entries: Mutex<BTreeMap<String, Bucket>>,
    maximum: usize,
    limit: usize,
}
impl Registry {
    fn new(maximum: usize, limit: usize) -> Arc<Self> {
        Arc::new(Self {
            entries: Mutex::new(BTreeMap::new()),
            maximum,
            limit,
        })
    }
    fn lease(self: &Arc<Self>, key: String) -> Result<KeyPermit> {
        let mut entries = self
            .entries
            .lock()
            .map_err(|_| Error::Limit("fairness state"))?;
        if !entries.contains_key(&key) && entries.len() >= self.maximum {
            return Err(Error::Limit("request capacity"));
        }
        let bucket = entries.entry(key.clone()).or_insert_with(|| Bucket {
            semaphore: Arc::new(Semaphore::new(self.limit)),
            users: 0,
        });
        bucket.users += 1;
        Ok(KeyPermit {
            registry: Arc::clone(self),
            key,
            semaphore: Arc::clone(&bucket.semaphore),
            permit: None,
        })
    }
}
#[derive(Debug)]
struct KeyPermit {
    registry: Arc<Registry>,
    key: String,
    semaphore: Arc<Semaphore>,
    permit: Option<OwnedSemaphorePermit>,
}
impl Drop for KeyPermit {
    fn drop(&mut self) {
        drop(self.permit.take());
        if let Ok(mut entries) = self.registry.entries.lock()
            && let Some(bucket) = entries.get_mut(&self.key)
        {
            bucket.users -= 1;
            if bucket.users == 0 {
                entries.remove(&self.key);
            }
        }
    }
}
#[derive(Debug)]
pub(super) struct Permit {
    _global: OwnedSemaphorePermit,
    _slot: OwnedSemaphorePermit,
    _class: OwnedSemaphorePermit,
    _peer: KeyPermit,
}
#[derive(Debug)]
struct Execution {
    _global: OwnedSemaphorePermit,
    _slot: OwnedSemaphorePermit,
    _class: OwnedSemaphorePermit,
}
#[derive(Debug)]
struct Slots {
    shared: Arc<Semaphore>,
    reserved: [Arc<Semaphore>; 4],
}
impl Slots {
    fn new(total: usize, reserved: [usize; 4]) -> Self {
        Self {
            shared: Arc::new(Semaphore::new(total - reserved.iter().sum::<usize>())),
            reserved: reserved.map(|count| Arc::new(Semaphore::new(count))),
        }
    }
    fn try_acquire(&self, class: Class) -> Option<OwnedSemaphorePermit> {
        Arc::clone(&self.reserved[class.index()])
            .try_acquire_owned()
            .ok()
            .or_else(|| Arc::clone(&self.shared).try_acquire_owned().ok())
    }
    async fn acquire(&self, class: Class) -> Result<OwnedSemaphorePermit> {
        tokio::select! { biased;
            permit=Arc::clone(&self.reserved[class.index()]).acquire_owned()=>permit.map_err(|_|Error::Denied),
            permit=Arc::clone(&self.shared).acquire_owned()=>permit.map_err(|_|Error::Denied),
        }
    }
}
#[derive(Debug)]
pub(super) struct Admission {
    active: Arc<Semaphore>,
    waiting: Arc<Semaphore>,
    execution: Arc<Semaphore>,
    request_slots: Slots,
    execution_slots: Slots,
    classes: [Arc<Semaphore>; 4],
    executors: [Arc<Semaphore>; 4],
    peers: Arc<Registry>,
    principals: Arc<Registry>,
    pub config: Config,
    pub retries: retry::Budget,
    rejected: AtomicU64,
    reported: AtomicU64,
    expired: AtomicU64,
}
impl Admission {
    pub(super) fn new(config: Config) -> Arc<Self> {
        let peer_limit = if config.fairness.peer_limit == 0 {
            (config.request_limit / 2).max(1)
        } else {
            config.fairness.peer_limit
        };
        let principal_limit = if config.fairness.principal_limit == 0 {
            (config.blocking_threads / 2).max(1)
        } else {
            config.fairness.principal_limit
        };
        Arc::new(Self {
            request_slots: Slots::new(
                config.request_limit,
                [
                    Class::Interactive,
                    Class::Control,
                    Class::Transfer,
                    Class::Background,
                ]
                .map(|class| config.workload(class).reserved_requests.unwrap_or_default()),
            ),
            execution_slots: Slots::new(
                config.blocking_threads,
                [
                    Class::Interactive,
                    Class::Control,
                    Class::Transfer,
                    Class::Background,
                ]
                .map(|class| {
                    config
                        .workload(class)
                        .reserved_execution
                        .unwrap_or_default()
                }),
            ),
            active: Arc::new(Semaphore::new(config.request_limit)),
            waiting: Arc::new(Semaphore::new(config.queue_limit)),
            execution: Arc::new(Semaphore::new(config.blocking_threads)),
            classes: [
                Class::Interactive,
                Class::Control,
                Class::Transfer,
                Class::Background,
            ]
            .map(|class| Arc::new(Semaphore::new(config.workload(class).request_limit))),
            executors: [
                Class::Interactive,
                Class::Control,
                Class::Transfer,
                Class::Background,
            ]
            .map(|class| Arc::new(Semaphore::new(config.workload(class).execution_limit))),
            peers: Registry::new(config.fairness.max_peers, peer_limit),
            principals: Registry::new(config.fairness.max_principals, principal_limit),
            retries: retry::Budget::new(config.retry),
            config,
            rejected: AtomicU64::new(0),
            reported: AtomicU64::new(0),
            expired: AtomicU64::new(0),
        })
    }
    pub(super) async fn acquire(&self) -> Result<Permit> {
        let (class, peer) = TASK
            .try_with(|request| (request.class, request.peer.clone()))
            .unwrap_or((Class::Interactive, "local".into()));
        let mut peer = match self.peers.lease(super::artifact::hash(
            format!("{}:{peer}", class.index()).as_bytes(),
        )) {
            Ok(peer) => peer,
            Err(Error::Limit("request capacity")) => return self.reject(),
            Err(error) => return Err(error),
        };
        let class_slots = &self.classes[class.index()];
        if let Ok(peer_permit) = Arc::clone(&peer.semaphore).try_acquire_owned()
            && let Ok(class_permit) = Arc::clone(class_slots).try_acquire_owned()
            && let Some(slot) = self.request_slots.try_acquire(class)
            && let Ok(global) = Arc::clone(&self.active).try_acquire_owned()
        {
            peer.permit = Some(peer_permit);
            return Ok(Permit {
                _global: global,
                _slot: slot,
                _class: class_permit,
                _peer: peer,
            });
        }
        let Ok(_waiting) = Arc::clone(&self.waiting).try_acquire_owned() else {
            return self.reject();
        };
        let outcome = tokio::time::timeout_at(
            crate::budget::until(Duration::from_millis(self.config.queue_timeout_ms)),
            async {
                peer.permit = Some(
                    Arc::clone(&peer.semaphore)
                        .acquire_owned()
                        .await
                        .map_err(|_| Error::Denied)?,
                );
                let class = Arc::clone(class_slots)
                    .acquire_owned()
                    .await
                    .map_err(|_| Error::Denied)?;
                let slot = self
                    .request_slots
                    .acquire(
                        TASK.try_with(|request| request.class)
                            .unwrap_or(Class::Interactive),
                    )
                    .await?;
                let global = Arc::clone(&self.active)
                    .acquire_owned()
                    .await
                    .map_err(|_| Error::Denied)?;
                Ok(Permit {
                    _global: global,
                    _slot: slot,
                    _class: class,
                    _peer: peer,
                })
            },
        )
        .await;
        match outcome {
            Ok(result) => result,
            _ => self.reject(),
        }
    }
    fn reject<T>(&self) -> Result<T> {
        self.rejected.fetch_add(1, Ordering::Relaxed);
        Err(Error::Limit("request capacity"))
    }
    async fn actor(&self, class: Class, principal: Option<&str>, peer: &str) -> Result<KeyPermit> {
        let key = principal.map_or_else(
            || format!("anonymous:{peer}"),
            |principal| {
                self.config.fairness.tenants.get(principal).map_or_else(
                    || format!("principal:{principal}"),
                    |tenant| format!("tenant:{tenant}"),
                )
            },
        );
        let mut actor = match self.principals.lease(super::artifact::hash(
            format!("{}:{key}", class.index()).as_bytes(),
        )) {
            Ok(actor) => actor,
            Err(Error::Limit("request capacity")) => return self.reject(),
            Err(error) => return Err(error),
        };
        if let Ok(permit) = Arc::clone(&actor.semaphore).try_acquire_owned() {
            actor.permit = Some(permit);
            return Ok(actor);
        }
        let Ok(_waiting) = Arc::clone(&self.waiting).try_acquire_owned() else {
            return self.reject();
        };
        match tokio::time::timeout_at(
            crate::budget::until(Duration::from_millis(self.config.queue_timeout_ms)),
            Arc::clone(&actor.semaphore).acquire_owned(),
        )
        .await
        {
            Ok(Ok(permit)) => {
                actor.permit = Some(permit);
                Ok(actor)
            }
            _ => self.reject(),
        }
    }
    async fn execute(&self, class: Class) -> Result<Execution> {
        let class_permit = tokio::time::timeout_at(
            crate::budget::until(Duration::from_mins(10)),
            Arc::clone(&self.executors[class.index()]).acquire_owned(),
        )
        .await
        .map_err(|_| Error::Limit("request deadline"))?
        .map_err(|_| Error::Denied)?;
        let slot = tokio::time::timeout_at(
            crate::budget::until(Duration::from_mins(10)),
            self.execution_slots.acquire(class),
        )
        .await
        .map_err(|_| Error::Limit("request deadline"))??;
        let global = tokio::time::timeout_at(
            crate::budget::until(Duration::from_mins(10)),
            Arc::clone(&self.execution).acquire_owned(),
        )
        .await
        .map_err(|_| Error::Limit("request deadline"))?
        .map_err(|_| Error::Denied)?;
        Ok(Execution {
            _global: global,
            _slot: slot,
            _class: class_permit,
        })
    }
    pub(super) fn metrics(&self) -> serde_json::Value {
        let workloads=[("interactive",Class::Interactive),("control",Class::Control),("transfer",Class::Transfer),("background",Class::Background)]
            .into_iter().map(|(name,class)| {
                let resolved=self.config.workload(class);
                (name,serde_json::json!({"limits":resolved,
                    "occupied_requests":resolved.request_limit-self.classes[class.index()].available_permits(),
                    "occupied_execution":resolved.execution_limit-self.executors[class.index()].available_permits()}))
            }).collect::<BTreeMap<_,_>>();
        serde_json::json!({"active_requests":self.config.request_limit-self.active.available_permits(),
            "queued_requests":self.config.queue_limit-self.waiting.available_permits(),
            "executing_requests":self.config.blocking_threads-self.execution.available_permits(),
            "overload_rejections":self.rejected.load(Ordering::Relaxed),"request_deadlines":self.expired.load(Ordering::Relaxed),
            "retry_budget_denied":self.retries.denied.load(Ordering::Relaxed),
            "request_limit":self.config.request_limit,"queue_limit":self.config.queue_limit,"queue_timeout_ms":self.config.queue_timeout_ms,
            "workloads":workloads,"request_timeout_ms":self.config.request_timeout_ms,"stream_timeout_ms":self.config.stream_timeout_ms,
            "retry_budget_capacity":self.config.retry.budget_capacity,"retry_budget_refill_per_second":self.config.retry.budget_refill_per_second})
    }
    pub(super) fn report(&self) {
        let total = self.rejected.load(Ordering::Relaxed);
        let previous = self.reported.swap(total, Ordering::Relaxed);
        if total != previous {
            eprintln!(
                "{}",
                serde_json::json!({"event":"overload_summary","rejected":total-previous,"total":total})
            );
        }
    }
}
#[derive(Debug)]
struct Request {
    admission: Arc<Admission>,
    class: Class,
    peer: String,
    started: AtomicBool,
    actor: Mutex<Option<KeyPermit>>,
    charges: Mutex<BTreeSet<[u8; 32]>>,
}
#[derive(Clone, Copy, Debug)]
struct DeadlineResponse;
tokio::task_local! { static TASK: Arc<Request>; }
thread_local! { static SYNC_REQUEST: RefCell<Option<Arc<Request>>> = const { RefCell::new(None) }; }

pub(super) async fn scope(
    admission: Arc<Admission>,
    class: Class,
    peer: String,
    work: impl Future<Output = axum::response::Response>,
) -> axum::response::Response {
    let until = Instant::now() + Duration::from_millis(admission.config.workload(class).timeout_ms);
    let context = Arc::new(Request {
        admission,
        class,
        peer,
        started: AtomicBool::new(false),
        actor: Mutex::new(None),
        charges: Mutex::new(BTreeSet::new()),
    });
    let result = tokio::time::timeout_at(
        tokio::time::Instant::from_std(until),
        TASK.scope(Arc::clone(&context), crate::budget::scope(until, work)),
    )
    .await;
    if let Ok(response) = result
        && response.extensions().get::<DeadlineResponse>().is_none()
        && Instant::now() < until
    {
        let response = crate::budget::response(response, until);
        // Keep identity quotas through stream completion without resetting its deadline.
        let (parts, body) = response.into_parts();
        let stream = futures_util::stream::unfold(
            (body.into_data_stream(), context),
            |(mut body, context)| async move { body.next().await.map(|chunk| (chunk, (body, context))) },
        );
        axum::response::Response::from_parts(parts, axum::body::Body::from_stream(stream))
    } else {
        context.admission.expired.fetch_add(1, Ordering::Relaxed);
        let safe = !context.started.load(Ordering::Acquire);
        deadline_response(safe)
    }
}
pub(super) async fn identity(principal: Option<&str>) -> Result<()> {
    let request = TASK.try_with(Arc::clone).map_err(|_| Error::Denied)?;
    let actor = request
        .admission
        .actor(request.class, principal, &request.peer)
        .await?;
    *request.actor.lock().map_err(|_| Error::Denied)? = Some(actor);
    Ok(())
}
pub(super) async fn background<T>(admission: Arc<Admission>, work: impl Future<Output = T>) -> T {
    let class = Class::Background;
    let until = Instant::now() + Duration::from_millis(admission.config.workload(class).timeout_ms);
    let request = Arc::new(Request {
        admission,
        class,
        peer: "internal".into(),
        started: AtomicBool::new(false),
        actor: Mutex::new(None),
        charges: Mutex::new(BTreeSet::new()),
    });
    TASK.scope(request, crate::budget::scope(until, work)).await
}
pub(super) async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<Result<T>> {
    let request = TASK.try_with(Arc::clone).ok();
    let cleanup_ms = request
        .as_ref()
        .map_or(200, |request| request.admission.config.cleanup_timeout_ms);
    let deadline = crate::budget::current();
    let execution = if let Some(request) = &request {
        Some(request.admission.execute(request.class).await?)
    } else {
        None
    };
    tokio::task::spawn_blocking(move || {
        struct Restore(Option<Arc<Request>>);
        impl Drop for Restore {
            fn drop(&mut self) {
                SYNC_REQUEST.with(|current| {
                    current.replace(self.0.take());
                });
            }
        }
        let _execution = execution;
        crate::budget::sync(deadline, cleanup_ms, || {
            crate::budget::check()?;
            if let Some(request) = &request {
                request.started.store(true, Ordering::Release);
            }
            let _restore = Restore(SYNC_REQUEST.with(|current| current.replace(request)));
            work()
        })
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))
}
fn charge_key(app: &str, class: &str, peer: &str, subject: &str) -> [u8; 32] {
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    for part in [app, class, peer, subject] {
        hash.update(part.len().to_le_bytes());
        hash.update(part.as_bytes());
    }
    let mut key = [0; 32];
    key.copy_from_slice(&hash.finalize());
    key
}
pub(super) fn was_charged(app: &str, class: &str, peer: &str, subject: &str) -> Result<bool> {
    SYNC_REQUEST.with(|current| {
        let current = current.borrow();
        let Some(request) = current.as_ref() else {
            return Ok(false);
        };
        Ok(request
            .charges
            .lock()
            .map_err(|_| Error::Denied)?
            .contains(&charge_key(app, class, peer, subject)))
    })
}
pub(super) fn record_charge(app: &str, class: &str, peer: &str, subject: &str) -> Result<()> {
    SYNC_REQUEST.with(|current| {
        let current = current.borrow();
        let Some(request) = current.as_ref() else {
            return Ok(());
        };
        let mut charges = request.charges.lock().map_err(|_| Error::Denied)?;
        if charges.len() >= 64 {
            return Err(Error::Limit("rate accounting budget"));
        }
        charges.insert(charge_key(app, class, peer, subject));
        Ok(())
    })
}
pub(super) fn retryable(error: &Error) -> bool {
    matches!(
        error,
        Error::Limit(
            "request capacity" | "Git transport capacity" | "socket capacity" | "server draining"
        )
    )
}
pub(super) fn deadline_response(safe: bool) -> axum::response::Response {
    let mut response = super::connected::json_response(
        axum::http::StatusCode::SERVICE_UNAVAILABLE,
        &serde_json::json!({"error":"deadline_exceeded","retry_safe":safe}),
    );
    response
        .headers_mut()
        .insert("connection", axum::http::HeaderValue::from_static("close"));
    // The small final error report must remain deliverable after useful work
    // expires. Its marker is native-only; it cannot grant replay authority.
    response.extensions_mut().insert(DeadlineResponse);
    response
}
pub(super) fn expired_response() -> axum::response::Response {
    let safe = TASK
        .try_with(|request| !request.started.load(Ordering::Acquire))
        .unwrap_or(false);
    deadline_response(safe)
}
pub(super) fn overload() -> axum::response::Response {
    let config = TASK
        .try_with(|request| request.admission.config.retry)
        .unwrap_or_default();
    let mut response = super::connected::json_response(
        axum::http::StatusCode::SERVICE_UNAVAILABLE,
        &serde_json::json!({"error":"overloaded","retry_safe":true}),
    );
    if let Ok(value) = config.retry_after_seconds.to_string().parse() {
        response.headers_mut().insert("retry-after", value);
    }
    response.headers_mut().insert(
        "x-diamond-retry",
        axum::http::HeaderValue::from_static("safe"),
    );
    response
        .headers_mut()
        .insert("connection", axum::http::HeaderValue::from_static("close"));
    response
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn final_deadline_report_is_deliverable_after_work_expires()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let admission = Admission::new(Config {
            request_timeout_ms: 10,
            ..Config::default()
        });
        let response = scope(
            Arc::clone(&admission),
            Class::Interactive,
            "local".into(),
            async {
                std::thread::sleep(Duration::from_millis(20));
                expired_response()
            },
        )
        .await;
        assert_eq!(
            response.status(),
            axum::http::StatusCode::SERVICE_UNAVAILABLE
        );
        assert!(response.headers().get("x-diamond-retry").is_none());
        let body = axum::body::to_bytes(response.into_body(), 1024).await?;
        let value: serde_json::Value = serde_json::from_slice(&body)?;
        assert_eq!(value["error"], "deadline_exceeded");
        assert_eq!(admission.expired.load(Ordering::Relaxed), 1);
        Ok(())
    }

    #[test]
    fn defaults_reserve_interactive_and_control_capacity() {
        let config = Config::default();
        assert!(config.checked().is_ok());
        assert_eq!(
            config.workload(Class::Interactive).reserved_requests,
            Some(2)
        );
        assert_eq!(config.workload(Class::Control).reserved_execution, Some(1));
        assert_eq!(config.workload(Class::Transfer).execution_limit, 2);
        let slots = Slots::new(4, [1, 1, 0, 0]);
        let first = slots.try_acquire(Class::Transfer);
        let second = slots.try_acquire(Class::Transfer);
        assert!(first.is_some() && second.is_some());
        assert!(slots.try_acquire(Class::Transfer).is_none());
        assert!(slots.try_acquire(Class::Interactive).is_some());
        assert!(slots.try_acquire(Class::Control).is_some());
    }

    #[test]
    fn fairness_keys_are_evicted_only_after_the_last_owner_drops() -> Result<()> {
        let registry = Registry::new(2, 1);
        let first = registry.lease("first".into())?;
        let duplicate = registry.lease("first".into())?;
        let second = registry.lease("second".into())?;
        assert!(registry.lease("third".into()).is_err());
        drop(first);
        assert!(registry.lease("third".into()).is_err());
        drop(duplicate);
        assert!(registry.lease("third".into()).is_ok());
        drop(second);
        Ok(())
    }

    #[test]
    fn expired_work_waiting_for_an_executor_never_runs() -> Result<()> {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(2)
            .enable_all()
            .build()?;
        runtime.block_on(async {
            let mut config = Config {
                request_limit: 2,
                blocking_threads: 1,
                database_connections: 1,
                ..Config::default()
            };
            config.workloads.interactive.timeout_ms = 40;
            config.workloads.control.timeout_ms = 500;
            config.checked()?;
            let admission = Admission::new(config);
            let started = Arc::new(AtomicBool::new(false));
            let flag = Arc::clone(&started);
            let controller = Arc::clone(&admission);
            let held = tokio::spawn(async move {
                scope(controller, Class::Control, "test".into(), async move {
                    let result = blocking(move || {
                        flag.store(true, Ordering::Release);
                        std::thread::sleep(Duration::from_millis(180));
                        Ok(())
                    })
                    .await;
                    match result {
                        Ok(Ok(())) => super::super::connected::json_response(
                            axum::http::StatusCode::OK,
                            &serde_json::json!({"ready":true}),
                        ),
                        Ok(Err(error)) | Err(error) => super::super::connected::failure(&error),
                    }
                })
                .await
            });
            let until = Instant::now() + Duration::from_secs(1);
            while !started.load(Ordering::Acquire) && Instant::now() < until {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
            assert!(started.load(Ordering::Acquire));
            let called = Arc::new(AtomicBool::new(false));
            let flag = Arc::clone(&called);
            let response = scope(
                Arc::clone(&admission),
                Class::Interactive,
                "test".into(),
                async move {
                    let result = blocking(move || {
                        flag.store(true, Ordering::Release);
                        Ok(())
                    })
                    .await;
                    match result {
                        Ok(Ok(())) => super::super::connected::json_response(
                            axum::http::StatusCode::OK,
                            &serde_json::json!({"ready":true}),
                        ),
                        Ok(Err(error)) | Err(error) => super::super::connected::failure(&error),
                    }
                },
            )
            .await;
            assert_eq!(
                response.status(),
                axum::http::StatusCode::SERVICE_UNAVAILABLE
            );
            let _completed = held
                .await
                .map_err(|_| Error::OutcomeUnknown(String::new()))?;
            assert!(!called.load(Ordering::Acquire));
            Ok(())
        })
    }
}
