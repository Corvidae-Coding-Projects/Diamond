//! Bounded, pure compute operations registered by the embedding host.
//!
//! Compute operations are trusted embedder extensions in the sense of
//! `docs/compute-extensions.md`: they receive wire-encoded values, return
//! wire-encoded values, and perform no I/O. The runtime treats their limits as
//! cooperative and advisory; only the sandboxed WebAssembly tier (future work)
//! carries enforced isolation.

use crate::error::{Error, Result};
use crate::value::Type;
use std::collections::BTreeMap;

/// A pure compute kernel registered by the embedding host.
///
/// Arguments arrive in Diamond's canonical wire encoding (for example `Int`
/// values are JSON strings of decimal digits); the reply must use the same
/// encoding for the declared result type. Kernels must be deterministic, must
/// not perform I/O, and must not retain state between calls.
pub trait Compute: Send + Sync {
    /// Run one bounded, pure computation over wire-encoded arguments.
    ///
    /// # Errors
    /// Return an error message to reject the call; the message is transported
    /// to Diamond as an application-level failure without runtime details.
    fn call(
        &self,
        arguments: &[serde_json::Value],
    ) -> std::result::Result<serde_json::Value, String>;
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
pub struct ComputeRegistry {
    entries: BTreeMap<(String, String), ComputeEntry>,
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
        if self.entries.len() >= 64 {
            return Err(Error::Limit("registered compute operations"));
        }
        if self
            .entries
            .insert(
                (module.to_string(), export.to_string()),
                ComputeEntry {
                    arguments,
                    result,
                    function,
                },
            )
            .is_some()
        {
            return Err(Error::Input(format!(
                "compute operation {module}.{export} is already registered"
            )));
        }
        Ok(())
    }

    pub(crate) fn get(&self, module: &str, export: &str) -> Option<&ComputeEntry> {
        self.entries.get(&(module.to_string(), export.to_string()))
    }

    #[cfg(test)]
    pub(crate) fn is_empty(&self) -> bool {
        self.entries.is_empty()
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
    parse_type_inner(name.trim(), 0)
}

fn parse_type_inner(text: &str, depth: usize) -> Result<Type> {
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

pub(crate) fn encoded_size(value: &serde_json::Value) -> Result<usize> {
    serde_json::to_vec(value)
        .map(|bytes| bytes.len())
        .map_err(|_| Error::Input("compute payload is not representable".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_supported_compute_types() -> Result<()> {
        assert_eq!(parse_compute_type("Text")?, Type::Text);
        assert_eq!(
            parse_compute_type(" List<Int> ")?,
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
    fn registry_rejects_duplicate_registration() -> Result<()> {
        let mut registry = ComputeRegistry::default();
        registry.register("demo", "one", vec![Type::Text], Type::Text, Box::new(Noop))?;
        assert!(
            registry
                .register("demo", "one", vec![Type::Text], Type::Text, Box::new(Noop))
                .is_err()
        );
        assert!(!registry.is_empty());
        Ok(())
    }

    struct Noop;

    impl Compute for Noop {
        fn call(
            &self,
            _arguments: &[serde_json::Value],
        ) -> std::result::Result<serde_json::Value, String> {
            Ok(serde_json::Value::Null)
        }
    }
}
