//! Statically registered, typed host operations and explicit local `PostgreSQL` grants.

use crate::error::{Error, Result};
use crate::value::{Type, Value, ValueKind};
use serde::Serialize;
use std::fmt;
use std::time::{Duration, Instant};
use tokio::runtime::Runtime;
use tokio::task::JoinHandle;
use tokio_postgres::{Client, Config, NoTls, config::Host};

#[derive(Debug)]
pub(crate) struct Operation {
    pub arguments: Vec<Type>,
    pub result: Type,
    pub capability: &'static str,
}

pub(crate) fn operation(name: &str) -> Option<Operation> {
    let arguments = match name {
        "postgres_version" => vec![],
        "postgres_echo" => vec![Type::Text],
        _ => return None,
    };
    Some(Operation {
        arguments,
        result: Type::Text,
        capability: "PostgresRead",
    })
}

#[derive(Debug, Serialize)]
pub(crate) struct OperationContract {
    name: &'static str,
    arguments: Vec<String>,
    result: String,
    capability: &'static str,
    maximum_output_bytes: usize,
    deadline_milliseconds: u64,
    transaction: &'static str,
    replay: &'static str,
    cancellation: &'static str,
    failure: &'static str,
}

pub(crate) fn contracts() -> Vec<OperationContract> {
    ["postgres_version", "postgres_echo"].into_iter().filter_map(|name| {
        operation(name).map(|operation| OperationContract {
            name,
            arguments: operation.arguments.iter().map(ToString::to_string).collect(),
            result: operation.result.to_string(),
            capability: operation.capability,
            maximum_output_bytes: crate::MAX_SOURCE_BYTES,
            deadline_milliseconds: 5000,
            transaction: "outside application transactions; read-only statement",
            replay: "no automatic replay",
            cancellation: "timeout drops the read future, aborts and joins its connection driver; no rollback claim",
            failure: "denied grant, invalid shape, budget failure, or redacted PostgreSQL failure",
        })
    }).collect()
}

pub(crate) struct HostRuntime {
    postgres: Client,
    driver: JoinHandle<std::result::Result<(), tokio_postgres::Error>>,
    runtime: Runtime,
    closed: bool,
    relational: bool,
    deadline: Instant,
}

impl fmt::Debug for HostRuntime {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("HostRuntime")
            .field("closed", &self.closed)
            .finish_non_exhaustive()
    }
}

impl HostRuntime {
    pub fn connect(configuration: &str) -> Result<Self> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(Error::Input(
                "call synchronous project evaluation outside an async runtime".into(),
            ));
        }
        Self::connect_inner(configuration, true)
    }

    // Called only from owned synchronous work, including a bounded spawn_blocking
    // task. That context may have a Tokio Handle but is not a runtime worker.
    pub(crate) fn connect_worker(configuration: &str) -> Result<Self> {
        Self::connect_inner(configuration, false)
    }

    fn connect_inner(configuration: &str, read_only: bool) -> Result<Self> {
        if configuration.len() > 16_384 {
            return Err(Error::Limit("PostgreSQL configuration"));
        }
        let mut config: Config = configuration
            .parse()
            .map_err(|_| Error::Input("invalid PostgreSQL configuration".into()))?;
        if config.get_hosts().len() != 1
            || config
                .get_hosts()
                .iter()
                .any(|host| !matches!(host, Host::Unix(_)))
            || !config.get_hostaddrs().is_empty()
        {
            return Err(Error::Input("W1 PostgreSQL grants require one explicit local Unix socket; remote/TLS connections are not implemented".into()));
        }
        config.connect_timeout(Duration::from_secs(5));
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let (postgres,driver)=runtime.block_on(async {
            let (postgres,connection)=tokio::time::timeout(Duration::from_secs(5),config.connect(NoTls)).await
                .map_err(|_|Error::Limit("PostgreSQL connection deadline"))?.map_err(|_|failure())?;
            let driver=tokio::spawn(connection);
            let settings=if read_only { "SET statement_timeout = '5s'; SET lock_timeout = '2s'; SET default_transaction_read_only = on;" }
                else { "SET statement_timeout = '5s'; SET lock_timeout = '2s'; SET idle_in_transaction_session_timeout = '10s'; SET search_path = pg_catalog;" };
            let setup=tokio::time::timeout(Duration::from_secs(5),postgres.batch_execute(settings)).await;
            if !matches!(setup,Ok(Ok(()))) {
                driver.abort();
                let _driver_result=driver.await;
                return Err(failure());
            }
            Ok((postgres,driver))
        })?;
        Ok(Self {
            postgres,
            driver,
            runtime,
            closed: false,
            relational: false,
            deadline: Instant::now() + Duration::from_secs(10),
        })
    }

    pub(crate) fn check_deadline(&self) -> Result<()> {
        if self.closed || Instant::now() >= self.deadline {
            return Err(Error::Limit("PostgreSQL request deadline"));
        }
        Ok(())
    }

    pub(crate) fn query(
        &mut self,
        sql: &str,
        parameters: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<Vec<tokio_postgres::Row>> {
        self.query_inner(sql, parameters, self.relational)
    }
    pub(crate) fn relational_query(
        &mut self,
        sql: &str,
        parameters: &[&(dyn tokio_postgres::types::ToSql + Sync)],
    ) -> Result<Vec<tokio_postgres::Row>> {
        self.query_inner(sql, parameters, true)
    }
    fn query_inner(
        &mut self,
        sql: &str,
        parameters: &[&(dyn tokio_postgres::types::ToSql + Sync)],
        classified: bool,
    ) -> Result<Vec<tokio_postgres::Row>> {
        self.check_deadline()?;
        let duration = (self.deadline - Instant::now()).min(Duration::from_secs(5));
        if let Ok(value) = self.runtime.block_on(async {
            tokio::time::timeout(duration, self.postgres.query(sql, parameters)).await
        }) {
            value.map_err(|e| {
                if classified {
                    relational_error(&e)
                } else {
                    failure()
                }
            })
        } else {
            self.close();
            Err(Error::Limit("PostgreSQL operation deadline"))
        }
    }

    pub(crate) fn batch(&mut self, sql: &str) -> Result<()> {
        self.batch_inner(sql, self.relational)
    }
    pub(crate) fn relational_batch(&mut self, sql: &str) -> Result<()> {
        self.batch_inner(sql, true)
    }
    fn batch_inner(&mut self, sql: &str, classified: bool) -> Result<()> {
        self.check_deadline()?;
        let duration = (self.deadline - Instant::now()).min(Duration::from_secs(5));
        if let Ok(value) = self.runtime.block_on(async {
            tokio::time::timeout(duration, self.postgres.batch_execute(sql)).await
        }) {
            value.map_err(|e| {
                if classified {
                    relational_error(&e)
                } else {
                    failure()
                }
            })
        } else {
            self.close();
            Err(Error::Limit("PostgreSQL operation deadline"))
        }
    }

    pub fn call(&mut self, name: &str, arguments: &[Value]) -> Result<Value> {
        let operation = operation(name).ok_or(Error::Denied)?;
        if self.closed {
            return Err(failure());
        }
        if operation.arguments.len() != arguments.len()
            || !arguments
                .iter()
                .zip(&operation.arguments)
                .all(|(value, ty)| value.fits(ty))
        {
            return Err(Error::Input(
                "host operation argument shape does not match its checked signature".into(),
            ));
        }
        if arguments.iter().any(
            |value| matches!(&value.0,ValueKind::Text(text) if text.len()>crate::MAX_SOURCE_BYTES),
        ) {
            return Err(Error::Limit("host operation input"));
        }
        let result = self.runtime.block_on(async {
            tokio::time::timeout(Duration::from_secs(5), async {
                match (name, arguments) {
                    ("postgres_version", []) => self
                        .postgres
                        .query_one("SELECT pg_catalog.current_setting('server_version')", &[])
                        .await
                        .map_err(|_| failure()),
                    ("postgres_echo", [Value(ValueKind::Text(text))]) => self
                        .postgres
                        .query_one("SELECT $1::text", &[text])
                        .await
                        .map_err(|_| failure()),
                    _ => Err(Error::Denied),
                }
            })
            .await
        });
        let row = if let Ok(result) = result {
            result?
        } else {
            self.close();
            return Err(Error::Limit("host operation deadline"));
        };
        let text: String = row.try_get(0).map_err(|_| failure())?;
        if text.len() > crate::MAX_SOURCE_BYTES {
            return Err(Error::Limit("host operation output"));
        }
        Ok(Value(ValueKind::Text(text)))
    }

    pub(crate) fn enable_relational(&mut self) {
        self.relational = true;
    }
    pub(crate) fn reset_deadline(&mut self) {
        self.deadline = Instant::now() + Duration::from_secs(10);
    }
    pub(crate) fn healthy(&self) -> bool {
        !self.closed && !self.postgres.is_closed()
    }
    pub(crate) fn close(&mut self) {
        if !self.closed {
            self.closed = true;
            self.driver.abort();
            let driver = &mut self.driver;
            self.runtime.block_on(async {
                let _driver_result = driver.await;
            });
        }
    }
}

impl Drop for HostRuntime {
    fn drop(&mut self) {
        self.close();
    }
}

fn failure() -> Error {
    Error::Postgres
}

fn relational_error(e: &tokio_postgres::Error) -> Error {
    match e.code().map(tokio_postgres::error::SqlState::code) {
        Some("40001" | "40P01") => Error::Conflict,
        Some("23502" | "23503" | "23505" | "23514") => Error::Denied,
        _ => Error::Postgres,
    }
}
