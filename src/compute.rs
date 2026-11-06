//! Bounded, pure compute kernels registered by the embedding host.
//!
//! Compute operations are trusted embedder extensions in the sense of
//! `docs/compute-extensions.md`: they receive wire-encoded values, return
//! wire-encoded values, and perform no I/O. The runtime treats their limits as
//! cooperative and advisory; only the sandboxed WebAssembly tier (future work)
//! carries enforced isolation.

use crate::error::{Error, Result};
use crate::value::Type;
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

/// Maximum number of exports a project may register. This matches the manifest
/// bound of 16 modules with at most 32 exports each.
pub(crate) const MAX_EXPORTS: usize = 16 * 32;

/// A classified failure returned by a compute kernel.
///
/// Every variant travels to Diamond as the matching `DataError` class, so
/// application code can handle kernel failures like any other fallible call.
/// [`ComputeError::Message`] text is transported verbatim; it must never
/// contain secrets, payloads, or runtime configuration.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ComputeError {
    /// The caller is not allowed to run this computation.
    Denied,
    /// The arguments do not satisfy the kernel's contract.
    InvalidInput,
    /// The call was cancelled or exceeded the request deadline.
    Aborted,
    /// A retryable conflict with concurrent work.
    Conflict,
    /// An application-defined message transported to Diamond verbatim.
    Message(String),
}

impl ComputeError {
    pub(crate) fn into_error(self) -> Error {
        match self {
            Self::Denied => Error::Denied,
            Self::InvalidInput => Error::Input("compute kernel rejected its arguments".into()),
            Self::Aborted => Error::Aborted,
            Self::Conflict => Error::Conflict,
            Self::Message(message) => Error::Propagation(message),
        }
    }
}

impl std::fmt::Display for ComputeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Denied => f.write_str("compute call denied"),
            Self::InvalidInput => f.write_str("compute input is invalid"),
            Self::Aborted => f.write_str("compute call aborted"),
            Self::Conflict => f.write_str("compute call conflicts with concurrent work"),
            Self::Message(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for ComputeError {}

/// Read-only execution context for one kernel call.
///
/// Kernels receive no principal, scope, secret, or database handle: the context
/// exposes only the declared names and the request deadline.
#[derive(Clone, Copy, Debug)]
pub struct ComputeContext<'a> {
    module: &'a str,
    export: &'a str,
    deadline: Option<Instant>,
}

impl<'a> ComputeContext<'a> {
    pub(crate) fn new(module: &'a str, export: &'a str, deadline: Option<Instant>) -> Self {
        Self {
            module,
            export,
            deadline,
        }
    }

    /// The declared module name, for example `"sim"` in `Compute<sim.tick>`.
    #[must_use]
    pub fn module(&self) -> &'a str {
        self.module
    }

    /// The declared export name, for example `"tick"` in `Compute<sim.tick>`.
    #[must_use]
    pub fn export(&self) -> &'a str {
        self.export
    }

    /// Time left before the enclosing request deadline, or `None` when the call
    /// runs outside a request.
    #[must_use]
    pub fn remaining(&self) -> Option<Duration> {
        self.deadline
            .map(|deadline| deadline.saturating_duration_since(Instant::now()))
    }

    /// Cooperative checkpoint for long-running loops.
    ///
    /// Returns [`ComputeError::Aborted`] once the request deadline has passed.
    /// The runtime cannot interrupt a kernel, so kernels that ignore this call
    /// stay bound only by the post-call `max_duration_ms` check.
    ///
    /// # Errors
    /// Returns [`ComputeError::Aborted`] after the request deadline.
    pub fn checkpoint(&self) -> std::result::Result<(), ComputeError> {
        match self.remaining() {
            Some(left) if left.is_zero() => Err(ComputeError::Aborted),
            _ => Ok(()),
        }
    }
}

/// A pure compute kernel registered by the embedding host.
///
/// # Wire encoding
///
/// Arguments and replies use Diamond's canonical wire encoding for the declared
/// types:
///
/// | Diamond type | JSON |
/// | --- | --- |
/// | `Int` | string of canonical decimal digits, for example `"-7"` |
/// | `Bool` | boolean |
/// | `Text` | string |
/// | `Decimal` | `{"tag":"Decimal","value":"<fixed-scale units>"}` |
/// | `List<T>` | array of encoded `T` |
/// | `Option<T>` | `{"tag":"None"}` or `{"tag":"Some","value":<T>}` |
///
/// `Decimal` values carry fixed-scale units as a canonical integer string, so
/// `1.5` is `{"tag":"Decimal","value":"1500000"}`. Anything else, including a
/// JSON number, is rejected as an invalid reply.
///
/// # Contract
///
/// Kernels are trusted embedder code. They must be deterministic — the same
/// arguments must always produce the same reply — must not perform I/O or
/// retain state between calls, and should call [`ComputeContext::checkpoint`]
/// inside long loops. Diamond treats those rules as cooperative: only the
/// sandboxed WebAssembly tier enforces them.
pub trait Compute: Send + Sync {
    /// Run one bounded, pure computation over wire-encoded arguments.
    ///
    /// # Errors
    /// Return a classified [`ComputeError`]. `Message` text is transported to
    /// Diamond, so it must not contain secrets or payload data.
    fn call(
        &self,
        context: &ComputeContext<'_>,
        arguments: &[serde_json::Value],
    ) -> std::result::Result<serde_json::Value, ComputeError>;
}

pub(crate) struct ComputeEntry {
    pub(crate) arguments: Vec<Type>,
    pub(crate) result: Type,
    pub(crate) function: Box<dyn Compute>,
}

/// Named compute kernels registered outside managed Diamond code. The registry
/// is inert unless the embedding host populates it, and it never survives into
/// build artifacts.
#[derive(Default)]
pub(crate) struct ComputeRegistry {
    entries: BTreeMap<String, BTreeMap<String, ComputeEntry>>,
}

impl ComputeRegistry {
    pub(crate) fn register(
        &mut self,
        module: &str,
        export: &str,
        arguments: Vec<Type>,
        result: Type,
        function: Box<dyn Compute>,
    ) -> Result<()> {
        if self
            .entries
            .get(module)
            .is_some_and(|exports| exports.contains_key(export))
        {
            return Err(Error::Input(format!(
                "compute operation {module}.{export} is already registered"
            )));
        }
        if self.entries.values().map(BTreeMap::len).sum::<usize>() >= MAX_EXPORTS {
            return Err(Error::Limit("registered compute operations"));
        }
        self.entries.entry(module.to_string()).or_default().insert(
            export.to_string(),
            ComputeEntry {
                arguments,
                result,
                function,
            },
        );
        Ok(())
    }

    pub(crate) fn get(&self, module: &str, export: &str) -> Option<&ComputeEntry> {
        self.entries.get(module)?.get(export)
    }
}

/// Parse one bounded wire-safe compute type name.
///
/// Supported names are `Int`, `Bool`, `Text`, `Decimal`, and nested
/// `List<T>` / `Option<T>` up to four levels. Records, identifiers, secrets,
/// and HTML values are deliberately excluded from this tier.
///
/// # Errors
/// Rejects unknown, over-nested, or non-wire-safe type names.
pub(crate) fn parse_compute_type(name: &str) -> Result<Type> {
    parse_type_inner(name, 0)
}

fn parse_type_inner(text: &str, depth: usize) -> Result<Type> {
    let text = text.trim();
    if depth > 4 {
        return Err(Error::Limit("compute type nesting"));
    }
    match text {
        "Int" => Ok(Type::Int),
        "Bool" => Ok(Type::Bool),
        "Text" => Ok(Type::Text),
        "Decimal" => Ok(Type::Decimal),
        _ => {
            if let Some(inner) = text.strip_prefix("List<").and_then(|v| v.strip_suffix('>')) {
                return Ok(Type::List(Box::new(parse_type_inner(inner, depth + 1)?)));
            }
            if let Some(inner) = text
                .strip_prefix("Option<")
                .and_then(|v| v.strip_suffix('>'))
            {
                return Ok(Type::Option(Box::new(parse_type_inner(inner, depth + 1)?)));
            }
            Err(Error::Input(format!(
                "unsupported compute type {text:?}; supported types are Int, Bool, Text, Decimal, List<T>, and Option<T>"
            )))
        }
    }
}

/// Measure one wire payload without materializing it.
pub(crate) fn encoded_size(value: &serde_json::Value) -> Result<usize> {
    struct Counter(usize);
    impl std::io::Write for Counter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0 = self.0.saturating_add(bytes.len());
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut counter = Counter(0);
    serde_json::to_writer(&mut counter, value)
        .map_err(|_| Error::Input("compute payload is not representable".into()))?;
    Ok(counter.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value as Json, json};

    #[test]
    fn parse_supported_compute_types() -> Result<()> {
        assert_eq!(parse_compute_type("Text")?, Type::Text);
        assert_eq!(
            parse_compute_type(" List<Int> ")?,
            Type::List(Box::new(Type::Int))
        );
        assert_eq!(
            parse_compute_type("List< Int >")?,
            Type::List(Box::new(Type::Int))
        );
        assert_eq!(
            parse_compute_type("Option<List<Text>>")?,
            Type::Option(Box::new(Type::List(Box::new(Type::Text))))
        );
        Ok(())
    }

    #[test]
    fn parse_rejects_unknown_and_over_nested_types() {
        assert!(parse_compute_type("Record").is_err());
        assert!(parse_compute_type("List<Int").is_err());
        assert!(parse_compute_type("List<Int>>").is_err());
        assert!(parse_compute_type("List<Option<List<Option<List<Text>>>>>").is_err());
        assert!(parse_compute_type("Id").is_err());
        assert!(parse_compute_type("Secret").is_err());
    }

    #[test]
    fn registry_rejects_duplicates_without_replacing_the_kernel() -> Result<()> {
        let mut registry = ComputeRegistry::default();
        registry.register("demo", "one", vec![], Type::Text, Box::new(Fixed("first")))?;
        assert!(
            registry
                .register("demo", "one", vec![], Type::Text, Box::new(Fixed("second")))
                .is_err()
        );
        let entry = registry.get("demo", "one").ok_or(Error::Denied)?;
        assert_eq!(
            entry
                .function
                .call(&ComputeContext::new("demo", "one", None), &[]),
            Ok(json!("first"))
        );
        Ok(())
    }

    #[test]
    fn context_reports_the_deadline_and_checkpoints() {
        let context = ComputeContext::new("demo", "one", None);
        assert_eq!(context.module(), "demo");
        assert_eq!(context.export(), "one");
        assert!(context.remaining().is_none());
        assert_eq!(context.checkpoint(), Ok(()));

        let context = ComputeContext::new("demo", "one", Some(Instant::now()));
        assert_eq!(context.remaining(), Some(Duration::ZERO));
        assert_eq!(context.checkpoint(), Err(ComputeError::Aborted));
    }

    #[test]
    fn compute_errors_map_to_failure_classes() {
        assert!(matches!(ComputeError::Denied.into_error(), Error::Denied));
        assert!(matches!(ComputeError::Aborted.into_error(), Error::Aborted));
        assert!(matches!(
            ComputeError::Conflict.into_error(),
            Error::Conflict
        ));
        assert!(matches!(
            ComputeError::InvalidInput.into_error(),
            Error::Input(_)
        ));
        assert!(matches!(
            ComputeError::Message("x".into()).into_error(),
            Error::Propagation(message) if message == "x"
        ));
    }

    struct Fixed(&'static str);

    impl Compute for Fixed {
        fn call(
            &self,
            _: &ComputeContext<'_>,
            _: &[Json],
        ) -> std::result::Result<Json, ComputeError> {
            Ok(Json::String(self.0.into()))
        }
    }
}
