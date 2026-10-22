//! Explicit versioned relational storage; legacy adapters retain their own contracts.
pub(crate) mod config;
mod migration;
mod pool;
pub(crate) mod query;
mod runtime;
pub(crate) mod schema;
pub(crate) mod types;

pub(crate) use config::{Storage, scalar};
pub(crate) use pool::{Connection, Pool};
pub(crate) use runtime::Context;

use crate::error::Error;
pub(crate) use migration::{audit, backfill, migrate, status};
fn invalid(message: &str) -> Error {
    Error::Input(message.into())
}
