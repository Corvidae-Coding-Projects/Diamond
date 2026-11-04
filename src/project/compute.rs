//! `[compute.<module>]` declarations: static export tables, bounded wire-safe
//! signatures, cooperative limits, and export-specific capabilities.
//!
//! The manifest is the single source of truth the checker resolves
//! `host.compute(module, export, ...)` against. Registered host kernels are
//! verified against these declarations at deployment, not at check time.

use super::{Manifest, input, resolve};
use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(super) enum Kind {
    Host,
    Wasm,
}

const fn input_limit() -> usize {
    65_536
}

const fn output_limit() -> usize {
    262_144
}

const fn duration_limit() -> u64 {
    2_000
}

const fn deterministic_default() -> bool {
    true
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ExportConfig {
    pub(super) arguments: Vec<String>,
    pub(super) result: String,
    #[serde(default = "deterministic_default")]
    pub(super) deterministic: bool,
    #[serde(default = "input_limit")]
    pub(super) max_input_bytes: usize,
    #[serde(default = "output_limit")]
    pub(super) max_output_bytes: usize,
    #[serde(default = "duration_limit")]
    pub(super) max_duration_ms: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ModuleConfig {
    pub(super) kind: Kind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) sha256: Option<String>,
    #[serde(default)]
    pub(super) exports: BTreeMap<String, ExportConfig>,
}

/// Validate every `[compute.<module>]` declaration: bounded shapes,
/// wire-safe type names, determinism, cooperative limits, and the exact
/// export-specific `Compute<module.export>` capability for each export.
///
/// # Errors
/// Rejects malformed, oversized, undeterministic, or under-provided declarations.
pub(super) fn validate(manifest: &Manifest) -> Result<()> {
    if manifest.compute.len() > 16 {
        return Err(input("at most 16 compute modules are supported"));
    }
    for (name, module) in &manifest.compute {
        if !resolve::identifier(name) {
            return Err(input("compute module names must be valid identifiers"));
        }
        match module.kind {
            Kind::Host => {
                if module.path.is_some() || module.sha256.is_some() {
                    return Err(input(
                        "host compute operations are registered by the embedding runtime",
                    ));
                }
            }
            Kind::Wasm => {
                return Err(input(
                    "WASM compute modules are not implemented in this profile",
                ));
            }
        }
        if module.exports.is_empty() || module.exports.len() > 32 {
            return Err(input("compute modules need 1..32 declared exports"));
        }
        for (export, config) in &module.exports {
            validate_export(name, export, config, manifest)?;
        }
    }
    Ok(())
}

fn validate_export(
    module: &str,
    export: &str,
    config: &ExportConfig,
    manifest: &Manifest,
) -> Result<()> {
    if !resolve::identifier(export) {
        return Err(input("compute export names must be valid identifiers"));
    }
    if !config.deterministic {
        return Err(input(
            "compute exports must declare deterministic = true; authoritative results require it",
        ));
    }
    if config.arguments.len() > 16 {
        return Err(Error::Limit("compute arguments"));
    }
    for argument in &config.arguments {
        crate::compute::parse_compute_type(argument)?;
    }
    crate::compute::parse_compute_type(&config.result)?;
    if !(1..=crate::MAX_SOURCE_BYTES).contains(&config.max_input_bytes)
        || !(1..=crate::MAX_SOURCE_BYTES).contains(&config.max_output_bytes)
    {
        return Err(Error::Limit("compute payload limits"));
    }
    if !(1..=10_000).contains(&config.max_duration_ms) {
        return Err(Error::Limit("compute duration limit"));
    }
    if !manifest
        .provides
        .contains(&format!("Compute<{module}.{export}>"))
    {
        return Err(input(&format!(
            "compute export {module}.{export} requires its exact Compute<{module}.{export}> capability"
        )));
    }
    Ok(())
}

/// Parse every declared export into its checked binding: exact signature,
/// export-specific capability, and cooperative limits.
///
/// # Errors
/// Rejects declarations that fail manifest validation or type parsing.
pub(super) fn bindings(
    manifest: &Manifest,
) -> Result<BTreeMap<(String, String), crate::hir::ComputeBinding>> {
    validate(manifest)?;
    let mut bindings = BTreeMap::new();
    for (name, module) in &manifest.compute {
        for (export, config) in &module.exports {
            let mut arguments = Vec::with_capacity(config.arguments.len());
            for argument in &config.arguments {
                arguments.push(crate::compute::parse_compute_type(argument)?);
            }
            bindings.insert(
                (name.clone(), export.clone()),
                crate::hir::ComputeBinding {
                    arguments,
                    result: crate::compute::parse_compute_type(&config.result)?,
                    capability: format!("Compute<{name}.{export}>"),
                    max_input_bytes: config.max_input_bytes,
                    max_output_bytes: config.max_output_bytes,
                    max_duration_ms: config.max_duration_ms,
                },
            );
        }
    }
    Ok(bindings)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::authority::{Effect, Effects, Principal, Scope};
    use crate::value::{Value, ValueKind};
    use std::sync::atomic::{AtomicUsize, Ordering};

    static CALLS: AtomicUsize = AtomicUsize::new(0);

    /// One test project: a server module with `main` plus a minimal browser root.
    fn project(manifest: &str, server: &str) -> Result<crate::CheckedProject> {
        project_with(manifest, server, &default_browser())
    }

    fn default_browser() -> String {
        "export component Counter:\n  state label: Text = \"lab\"\n\n  html:\n    <p>{label}</p>\n"
            .to_string()
    }

    fn project_with(manifest: &str, server: &str, browser: &str) -> Result<crate::CheckedProject> {
        let sources = BTreeMap::from([
            ("server.dia".to_string(), server.to_string()),
            ("counter.dia".to_string(), browser.to_string()),
        ]);
        crate::project::compile_sources(manifest.to_string(), sources)
    }

    fn manifest(provides: &str, exports: &str) -> String {
        format!(
            "schema_version = 1\nname = \"ComputeLab\"\nserver = \"server\"\n\
             browser = \"counter\"\ncomponent = \"Counter\"\nprovides = [{provides}]\n\
             [modules.server]\npath = \"server.dia\"\ntarget = \"server\"\nprovides = [{provides}]\n\
             [modules.counter]\npath = \"counter.dia\"\ntarget = \"browser\"\n\
             [compute.demo]\nkind = \"host\"\n{exports}"
        )
    }

    fn echo_export() -> String {
        "[compute.demo.exports.echo]\narguments = [\"Text\"]\nresult = \"Text\"\ndeterministic = true\n".to_string()
    }

    fn echo_call() -> &'static str {
        "export fn main() -> Result<Text, DataError>:\n  host.compute<Text>(\"demo\", \"echo\", \"hello\")\n"
    }

    fn run(
        project: &crate::CheckedProject,
        runtime: &crate::RuntimeGrants,
        capable: bool,
    ) -> Result<Value> {
        let mut grants = Effects::new();
        if capable {
            grants.insert(Effect::Host("Compute<demo.echo>".into()));
        }
        let principal = Principal::Anonymous;
        let scope = Scope::new(&principal, &grants);
        crate::eval::Interpreter::new(&project.server, &scope, None, None)
            .with_integrations(runtime)
            .main()
    }

    struct Echo;

    impl crate::Compute for Echo {
        fn call(
            &self,
            arguments: &[serde_json::Value],
        ) -> std::result::Result<serde_json::Value, String> {
            arguments
                .first()
                .cloned()
                .ok_or_else(|| "missing argument".to_string())
        }
    }

    struct Oversized;

    impl crate::Compute for Oversized {
        fn call(&self, _: &[serde_json::Value]) -> std::result::Result<serde_json::Value, String> {
            Ok(serde_json::Value::String("x".repeat(300_000)))
        }
    }

    struct Panics;

    impl crate::Compute for Panics {
        fn call(&self, _: &[serde_json::Value]) -> std::result::Result<serde_json::Value, String> {
            panic!("kernel bug");
        }
    }

    struct Counted;

    impl crate::Compute for Counted {
        fn call(&self, _: &[serde_json::Value]) -> std::result::Result<serde_json::Value, String> {
            CALLS.fetch_add(1, Ordering::SeqCst);
            Ok(serde_json::Value::String("unused".into()))
        }
    }

    fn registered() -> Result<crate::RuntimeGrants> {
        let mut runtime = crate::RuntimeGrants::default();
        runtime.register_compute("demo", "echo", &["Text"], "Text", Echo)?;
        Ok(runtime)
    }

    #[test]
    fn declared_export_checks_and_dispatches() -> Result<()> {
        let project = project(
            &manifest("\"Compute<demo.echo>\"", &echo_export()),
            echo_call(),
        )?;
        let effects = &project
            .server
            .functions
            .get("main")
            .ok_or(Error::Denied)?
            .effects;
        assert!(effects.contains(&Effect::Host("Compute<demo.echo>".into())));
        let runtime = registered()?;
        runtime.validate_compute(&bindings(&project.manifest)?)?;
        let value = run(&project, &runtime, true)?;
        assert!(
            matches!(&value.0, ValueKind::Ok(inner) if matches!(&inner.0, ValueKind::Text(text) if text == "hello")),
            "unexpected value: {value:?}"
        );
        Ok(())
    }

    #[test]
    fn evaluation_requires_the_export_capability() -> Result<()> {
        let project = project(
            &manifest("\"Compute<demo.echo>\"", &echo_export()),
            echo_call(),
        )?;
        let runtime = registered()?;
        assert!(run(&project, &runtime, false).is_err());
        Ok(())
    }

    #[test]
    fn declaration_requires_the_exact_capability() {
        let error = project(&manifest("\"Jobs\"", &echo_export()), echo_call())
            .err()
            .map(|error| format!("{error}"));
        assert!(
            error
                .as_deref()
                .is_some_and(|text| text.contains("Compute<demo.echo>")),
            "unexpected error: {error:?}"
        );
    }

    #[test]
    fn checker_rejects_undeclared_and_non_static_names() {
        let manifest_source = manifest("\"Compute<demo.echo>\"", &echo_export());
        let undeclared = project(
            &manifest_source,
            "export fn main() -> Result<Text, DataError>:\n  host.compute<Text>(\"demo\", \"missing\", \"hello\")\n",
        )
        .err()
        .map(|error| format!("{error}"));
        assert!(
            undeclared
                .as_deref()
                .is_some_and(|text| text.contains("not declared in the project manifest")),
            "unexpected error: {undeclared:?}"
        );
        let dynamic = project(
            &manifest_source,
            "export fn main() -> Result<Text, DataError>:\n  host.compute<Text>(\"demo\", \"echo\", \"hello\")\n\nexport fn dynamic(name: Text) -> Result<Text, DataError>:\n  host.compute<Text>(name, \"echo\", \"hello\")\n",
        )
        .err()
        .map(|error| format!("{error}"));
        assert!(
            dynamic
                .as_deref()
                .is_some_and(|text| text.contains("static module name")),
            "unexpected error: {dynamic:?}"
        );
    }

    #[test]
    fn checker_rejects_signature_mismatches() {
        let manifest_source = manifest("\"Compute<demo.echo>\"", &echo_export());
        let wrong_arity = project(
            &manifest_source,
            "export fn main() -> Result<Text, DataError>:\n  host.compute<Text>(\"demo\", \"echo\")\n",
        );
        assert!(wrong_arity.is_err());
        let wrong_result = project(
            &manifest_source,
            "export fn main() -> Result<Int, DataError>:\n  host.compute<Int>(\"demo\", \"echo\", \"hello\")\n",
        );
        assert!(wrong_result.is_err());
        let wrong_argument = project(
            &manifest_source,
            "export fn main() -> Result<Text, DataError>:\n  host.compute<Text>(\"demo\", \"echo\", 1)\n",
        );
        assert!(wrong_argument.is_err());
    }

    #[test]
    fn shared_and_browser_modules_cannot_call_compute() {
        let browser = "export component Counter:\n  state label: Text = \"lab\"\n\n  html:\n    <p>{label}</p>\n\nfn probe() -> Result<Text, DataError>:\n  host.compute<Text>(\"demo\", \"echo\", \"lab\")\n";
        let error = project_with(
            &manifest("\"Compute<demo.echo>\"", &echo_export()),
            echo_call(),
            browser,
        )
        .err()
        .map(|error| format!("{error}"));
        assert!(
            error.as_deref().is_some_and(
                |text| text.contains("host operations require a server project module")
            ),
            "unexpected error: {error:?}"
        );
    }

    #[test]
    fn declaration_rejects_unsafe_shapes() {
        let bad_type = project(
            &manifest(
                "\"Compute<demo.echo>\"",
                "[compute.demo.exports.echo]\narguments = [\"Id\"]\nresult = \"Text\"\ndeterministic = true\n",
            ),
            echo_call(),
        );
        assert!(bad_type.is_err());
        let nondeterministic = project(
            &manifest(
                "\"Compute<demo.echo>\"",
                "[compute.demo.exports.echo]\narguments = [\"Text\"]\nresult = \"Text\"\ndeterministic = false\n",
            ),
            echo_call(),
        );
        assert!(nondeterministic.is_err());
        let wasm = project(
            &manifest(
                "\"Compute<demo.echo>\"",
                "kind = \"wasm\"\npath = \"compute/demo.wasm\"\nsha256 = \"abc\"\n[compute.demo.exports.echo]\narguments = [\"Text\"]\nresult = \"Text\"\n",
            ),
            echo_call(),
        );
        assert!(wasm.is_err());
    }

    #[test]
    fn runtime_rejects_missing_and_mismatched_registrations() -> Result<()> {
        let project = project(
            &manifest("\"Compute<demo.echo>\"", &echo_export()),
            echo_call(),
        )?;
        let declared = bindings(&project.manifest)?;
        assert!(
            crate::RuntimeGrants::default()
                .validate_compute(&declared)
                .is_err()
        );
        let mut mismatched = crate::RuntimeGrants::default();
        mismatched.register_compute("demo", "echo", &["Int"], "Text", Echo)?;
        assert!(mismatched.validate_compute(&declared).is_err());
        registered()?.validate_compute(&declared)?;
        Ok(())
    }

    #[test]
    fn runtime_enforces_limits_and_catches_panics() -> Result<()> {
        let project = project(
            &manifest("\"Compute<demo.echo>\"", &echo_export()),
            echo_call(),
        )?;
        let runtime = registered()?;
        let value = run(&project, &runtime, true)?;
        assert!(
            matches!(&value.0, ValueKind::Ok(inner) if matches!(&inner.0, ValueKind::Text(text) if text == "hello")),
            "unexpected value: {value:?}"
        );

        let mut grants = crate::RuntimeGrants::default();
        grants.register_compute("demo", "echo", &["Text"], "Text", Oversized)?;
        let error = run(&project, &grants, true)
            .err()
            .map(|error| format!("{error}"));
        assert!(
            error
                .as_deref()
                .is_some_and(|text| text.contains("compute output")),
            "unexpected error: {error:?}"
        );

        let mut grants = crate::RuntimeGrants::default();
        grants.register_compute("demo", "echo", &["Text"], "Text", Panics)?;
        let value = run(&project, &grants, true)?;
        assert!(
            matches!(value.0, ValueKind::Err(_)),
            "unexpected value: {value:?}"
        );
        assert!(
            !format!("{value:?}").contains("kernel bug"),
            "panic text must not reach Diamond: {value:?}"
        );
        Ok(())
    }

    #[test]
    fn runtime_rejects_oversized_input_before_execution() -> Result<()> {
        let project = project(
            &manifest(
                "\"Compute<demo.echo>\"",
                "[compute.demo.exports.echo]\narguments = [\"Text\"]\nresult = \"Text\"\ndeterministic = true\nmax_input_bytes = 1\n",
            ),
            echo_call(),
        )?;
        let mut runtime = crate::RuntimeGrants::default();
        runtime.register_compute("demo", "echo", &["Text"], "Text", Counted)?;
        let before = CALLS.load(Ordering::SeqCst);
        assert!(run(&project, &runtime, true).is_err());
        assert_eq!(CALLS.load(Ordering::SeqCst), before);
        Ok(())
    }

    #[test]
    fn artifact_publishes_compute_contracts() -> Result<()> {
        let project = project(
            &manifest("\"Compute<demo.echo>\"", &echo_export()),
            echo_call(),
        )?;
        let files = crate::project::artifact::files(&project)?;
        let contract: serde_json::Value =
            serde_json::from_str(files.get("contract.json").ok_or(Error::Denied)?)
                .map_err(|_| Error::Denied)?;
        let export = &contract["compute"]["demo"]["exports"]["echo"];
        assert_eq!(export["result"], serde_json::json!("Text"));
        assert_eq!(export["deterministic"], serde_json::json!(true));
        assert_eq!(export["max_input_bytes"], serde_json::json!(65_536));
        Ok(())
    }
}
