//! Bounded synchronous connection ownership and confirmed cleanup before reuse.
use crate::{
    error::{Error, Result},
    host::HostRuntime,
};
use std::{
    sync::{Arc, Condvar, Mutex},
    time::{Duration, Instant},
};
use zeroize::Zeroizing;
pub(crate) struct Pool {
    configuration: Zeroizing<String>,
    state: Mutex<State>,
    available: Condvar,
}
impl std::fmt::Debug for Pool {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Pool")
            .field("maximum_connections", &4)
            .finish_non_exhaustive()
    }
}
#[derive(Debug, Default)]
struct State {
    idle: Vec<HostRuntime>,
    total: usize,
}
impl Pool {
    pub(crate) fn new(configuration: &str) -> Arc<Self> {
        Arc::new(Self {
            configuration: Zeroizing::new(configuration.into()),
            state: Mutex::new(State::default()),
            available: Condvar::new(),
        })
    }
    pub(crate) fn acquire(self: &Arc<Self>) -> Result<Connection> {
        let until = Instant::now() + Duration::from_secs(5);
        let mut state = self.state.lock().map_err(|_| Error::Postgres)?;
        loop {
            if let Some(mut connection) = state.idle.pop() {
                connection.reset_deadline();
                return Ok(Connection::Pooled(Lease {
                    pool: Arc::clone(self),
                    connection: Some(connection),
                }));
            }
            if state.total < 4 {
                state.total += 1;
                drop(state);
                let result = HostRuntime::connect_worker(&self.configuration);
                if result.is_err() {
                    let mut state = self.state.lock().map_err(|_| Error::Postgres)?;
                    state.total -= 1;
                    self.available.notify_one();
                }
                return result.map(|connection| {
                    Connection::Pooled(Lease {
                        pool: Arc::clone(self),
                        connection: Some(connection),
                    })
                });
            }
            let Some(remaining) = until.checked_duration_since(Instant::now()) else {
                return Err(Error::Limit("connection pool admission"));
            };
            let (next, timeout) = self
                .available
                .wait_timeout(state, remaining)
                .map_err(|_| Error::Postgres)?;
            state = next;
            if timeout.timed_out() {
                return Err(Error::Limit("connection pool admission"));
            }
        }
    }
}
#[derive(Debug)]
pub(crate) enum Connection {
    Direct(HostRuntime),
    Pooled(Lease),
}
#[derive(Debug)]
pub(crate) struct Lease {
    pool: Arc<Pool>,
    connection: Option<HostRuntime>,
}
impl Connection {
    pub(crate) fn runtime(&mut self) -> Result<&mut HostRuntime> {
        match self {
            Self::Direct(c) => Ok(c),
            Self::Pooled(l) => l.connection.as_mut().ok_or(Error::Postgres),
        }
    }
    pub(crate) fn query(
        &mut self,
        sql: &str,
        params: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<Vec<tokio_postgres::Row>> {
        self.runtime()?.query(sql, params)
    }
    pub(crate) fn relational_batch(&mut self, sql: &str) -> Result<()> {
        self.runtime()?.relational_batch(sql)
    }
    pub(crate) fn batch(&mut self, sql: &str) -> Result<()> {
        self.runtime()?.batch(sql)
    }
    pub(crate) fn check_deadline(&self) -> Result<()> {
        match self {
            Self::Direct(c) => c.check_deadline(),
            Self::Pooled(l) => l
                .connection
                .as_ref()
                .ok_or(Error::Postgres)?
                .check_deadline(),
        }
    }
    pub(crate) fn close(&mut self) {
        if let Ok(c) = self.runtime() {
            c.close();
        }
    }
}
impl Drop for Lease {
    fn drop(&mut self) {
        if let Some(mut c) = self.connection.take() {
            c.reset_deadline();
            let clean = c.healthy() && c.batch("ROLLBACK").is_ok();
            if !clean {
                c.close();
            }
            if let Ok(mut state) = self.pool.state.lock() {
                if clean {
                    state.idle.push(c);
                } else {
                    state.total -= 1;
                }
                self.pool.available.notify_one();
            }
        }
    }
}
