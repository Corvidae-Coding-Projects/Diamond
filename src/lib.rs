//! Diamond's checkpoint-six compiler, reference interpreter, and local web runtime.
//!
//! Executable programs are sealed behind checking. This is a restricted,
//! single-task development prototype, not a production security implementation.

mod accounts;
mod auth;
mod authority;
mod browser_auth;
mod checker;
mod data;
mod error;
mod eval;
mod forms;
mod hir;
mod host;
mod http;
mod integrations;
mod lexer;
mod parser;
mod policy;
mod project;
mod relational;
mod standard;
mod syntax;
mod template;
mod value;
mod web;

pub use auth::{SessionStore, SessionToken};
pub use error::{Error, Location};
pub use hir::CheckedProgram;
pub use integrations::RuntimeGrants;
pub use project::{CheckedProject, compile_project, load_project_build};
pub use value::Value;

use authority::{Effects, Principal, Scope};
use std::net::SocketAddr;
use std::path::Path;

/// Maximum accepted Diamond source size in UTF-8 bytes.
pub const MAX_SOURCE_BYTES: usize = 1_048_576;

/// Run the runtime-owned Git pre-receive entry point.
///
/// Git invokes this only through the private hook installed by
/// [`CheckedProject::initialize_git`]. Direct calls fail closed.
///
/// # Errors
/// Rejects an invalid invocation, claim, repository, account, protection rule,
/// ref update, database connection, or ancestry check.
#[doc(hidden)]
pub fn run_git_pre_receive_hook() -> Result<(), Error> {
    project::git::pre_receive_hook()
}

/// Persist a committed Git push in the runtime-owned durable event spool.
///
/// # Errors
/// Rejects invalid hook invocation, claims, ref input, fencing state, or storage
/// errors. A failure after Git commits refs is reported as an explicit unknown
/// event-delivery outcome for reconciliation.
#[doc(hidden)]
pub fn run_git_post_receive_hook() -> Result<(), Error> {
    project::git::post_receive_hook()
}

/// Parse and check a source program without executing it or opening a database.
///
/// # Errors
/// Returns source diagnostics, type/effect/policy errors, or explicit unsupported errors.
///
/// ```
/// assert!(diamond::compile("fn main() -> Int:\n  40 + 2\n").is_ok());
/// assert!(diamond::compile("fn main() -> Int:\n  \"wrong type\"\n").is_err());
/// ```
///
/// ```
/// let program = diamond::compile("enum State:\n  Ready\n  Waiting(reason: Text)\nfn main() -> Text:\n  match Waiting(\"review\"):\n    Ready => \"ready\"\n    Waiting(reason) => reason\n")?;
/// assert_eq!(program.evaluate()?.display_text()?, "review");
/// # Ok::<(), diamond::Error>(())
/// ```
pub fn compile(source: &str) -> Result<CheckedProgram, Error> {
    checker::check(&parser::parse(source)?)
}

/// Provision a local account in a private database, separate from application data.
/// Existing accounts are not overwritten; this function is a trusted-host operation.
///
/// # Errors
/// Rejects invalid identities/passwords, insecure files, incompatible schemas,
/// duplicate accounts, and hashing, database, or I/O failures.
///
/// ```
/// # fn main() -> Result<(), Box<dyn std::error::Error>> {
/// let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_nanos();
/// let path = std::env::temp_dir().join(format!("diamond-account-doc-{}-{nonce}.sqlite", std::process::id()));
/// diamond::add_local_user(&path, "example", "documentation-only-password")?;
/// assert!(diamond::add_local_user(&path, "example", "documentation-only-password").is_err());
/// std::fs::remove_file(path)?;
/// # Ok(())
/// # }
/// ```
pub fn add_local_user(path: &Path, username: &str, password: &str) -> Result<(), Error> {
    accounts::add_user(path, username, password)
}

impl CheckedProgram {
    /// Execute a zero-argument `main` without database or other host authority.
    ///
    /// # Errors
    /// Returns missing-main, capability, runtime, or resource-budget failures.
    ///
    /// ```
    /// let program = diamond::compile("fn main() -> Int:\n  21 * 2\n")?;
    /// assert_eq!(program.evaluate()?.display_text()?, "42");
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn evaluate(&self) -> Result<Value, Error> {
        let principal = Principal::Anonymous;
        let grants = Effects::new();
        let scope = Scope::new(&principal, &grants);
        eval::Interpreter::new(self, &scope, None, None).main()
    }

    /// Execute `main` with explicit host authority for this database path.
    /// Policies still run under an anonymous principal; this grants no user identity.
    ///
    /// # Errors
    /// Returns schema, I/O, policy, capability, runtime, or uncertain-transaction errors.
    pub fn evaluate_with_database(&self, path: &Path) -> Result<Value, Error> {
        if !self.app.as_ref().is_some_and(|app| app.database) {
            return Err(Error::Input(
                "program does not declare a SQLite database".into(),
            ));
        }
        let mut database = data::Database::open(path, self)?;
        let principal = Principal::Anonymous;
        let scope = Scope::new(&principal, &self.grants);
        eval::Interpreter::new(self, &scope, Some(&mut database), None).main()
    }

    /// Initialize the exact checked resource schema at an explicitly supplied path.
    /// Existing incompatible schemas are rejected rather than migrated.
    ///
    /// # Errors
    /// Returns unsupported field, incompatible schema, database, or I/O failures.
    pub fn initialize_database(&self, path: &Path) -> Result<(), Error> {
        if !self.app.as_ref().is_some_and(|app| app.database) {
            return Err(Error::Input(
                "program does not declare a SQLite database".into(),
            ));
        }
        data::Database::open(path, self)?;
        Ok(())
    }

    /// Run the bounded development HTTP server on a loopback address.
    /// The optional request limit supports reproducible local verification.
    ///
    /// # Errors
    /// Rejects non-loopback serving and returns listener or schema failures.
    /// Without an account provider, private routes continue to deny access.
    pub fn serve(
        &self,
        address: SocketAddr,
        database: Option<&Path>,
        maximum_requests: Option<usize>,
    ) -> Result<(), Error> {
        web::serve(self, address, database, None, maximum_requests)
    }

    /// Serve with explicitly supplied local-account and application database paths.
    /// Runtime login/logout and form CSRF handling are automatic for this provider.
    /// Use `http://localhost:PORT` in a browser for loopback secure-cookie support.
    ///
    /// # Errors
    /// Rejects missing session declarations, overlapping database paths, insecure
    /// account stores, non-loopback listeners, and schema or transport failures.
    ///
    /// ```no_run
    /// # fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// let source = std::fs::read_to_string("examples/message_board.dia")?;
    /// let program = diamond::compile(&source)?;
    /// program.serve_with_auth("127.0.0.1:3000".parse()?, Some(std::path::Path::new(".diamond/board.sqlite")), std::path::Path::new(".diamond/accounts.sqlite"), None)?;
    /// # Ok(())
    /// # }
    /// ```
    pub fn serve_with_auth(
        &self,
        address: SocketAddr,
        database: Option<&Path>,
        accounts: &Path,
        maximum_requests: Option<usize>,
    ) -> Result<(), Error> {
        web::serve(self, address, database, Some(accounts), maximum_requests)
    }
}
