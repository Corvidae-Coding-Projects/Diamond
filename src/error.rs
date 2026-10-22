//! Diagnostics and runtime failures shared by the compiler and adapters.

use std::fmt;

/// A source location, measured in one-based Unicode character columns.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Location {
    pub(crate) line: usize,
    pub(crate) column: usize,
}

impl Location {
    /// Return the one-based source line.
    #[must_use]
    pub const fn line(self) -> usize {
        self.line
    }

    /// Return the one-based Unicode scalar column.
    #[must_use]
    pub const fn column(self) -> usize {
        self.column
    }
}

impl fmt::Display for Location {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.line, self.column)
    }
}

/// A compiler or execution error. HTTP adapters sanitize internal failures.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A project-module failure with its originating source path.
    #[error("{path}: {source}")]
    Source {
        /// Project-relative source path.
        path: String,
        /// Underlying source diagnostic or failure.
        #[source]
        source: Box<Self>,
    },
    /// Invalid source, a type mismatch, or an undeclared effect.
    #[error("{code} at {location}: {message}")]
    Diagnostic {
        /// Stable diagnostic category.
        code: &'static str,
        /// Location of the offending source.
        location: Location,
        /// Explanation and, when possible, a remedy.
        message: String,
    },
    /// A feature intentionally absent from this prototype.
    #[error("D900 unsupported at {location}: {feature}")]
    Unsupported {
        /// Source of the unsupported operation.
        location: Location,
        /// Feature that would be needed to execute it.
        feature: String,
    },
    /// A missing or expired capability, policy denial, or protected record absence.
    #[error("access denied or resource not found")]
    Denied,
    /// Input failed its declared shape or bounds.
    #[error("invalid input: {0}")]
    Input(String),
    /// A Diamond Result error propagated with `?`.
    #[error("Diamond operation failed: {0}")]
    Propagation(String),
    /// A configured execution, input, or output budget was exceeded.
    #[error("resource limit exceeded: {0}")]
    Limit(&'static str),
    /// A database operation failed; this is an internal diagnostic, not an HTTP body.
    #[error("database operation failed")]
    Database(#[from] rusqlite::Error),
    /// A `PostgreSQL` adapter failed; connection details are never included.
    ///
    /// ```
    /// assert!(diamond::Error::Postgres.to_string().contains("redacted"));
    /// ```
    #[error("PostgreSQL operation failed; connection details are redacted")]
    Postgres,
    /// An operating-system operation failed.
    #[error("I/O operation failed: {0}")]
    Io(#[from] std::io::Error),
    /// Secure random generation failed; there is no insecure fallback.
    #[error("secure random generation failed")]
    Random,
    /// The runtime established that the transaction did not commit.
    #[error("transaction aborted")]
    Aborted,
    /// Commit status cannot be established; callers must not blindly replay.
    #[error("transaction outcome unknown; operation {0}; do not automatically retry")]
    OutcomeUnknown(String),
}

impl Error {
    pub(crate) fn diagnostic(
        code: &'static str,
        location: Location,
        message: impl Into<String>,
    ) -> Self {
        Self::Diagnostic {
            code,
            location,
            message: message.into(),
        }
    }

    pub(crate) fn unsupported(location: Location, feature: impl Into<String>) -> Self {
        Self::Unsupported {
            location,
            feature: feature.into(),
        }
    }
}

pub(crate) type Result<T> = std::result::Result<T, Error>;
