//! A real consumer of host-tier compute extensions.

use anyhow::{Result, ensure};
use diamond::{Compute, ComputeContext, ComputeError, RuntimeGrants};
use serde_json::{Value as Json, json};

/// Deterministic `tick(state, days)` kernel over wire-encoded arguments.
struct Tick;

impl Compute for Tick {
    fn call(
        &self,
        context: &ComputeContext<'_>,
        arguments: &[Json],
    ) -> std::result::Result<Json, ComputeError> {
        context.checkpoint()?;
        let state = arguments.first().and_then(Json::as_str).unwrap_or_default();
        let days = integer(arguments, 1)?;
        Ok(Json::String(format!("{state}/{days}")))
    }
}

/// Deterministic `plan(width, height)` kernel returning `List<Int>`.
struct Plan;

impl Compute for Plan {
    fn call(
        &self,
        _: &ComputeContext<'_>,
        arguments: &[Json],
    ) -> std::result::Result<Json, ComputeError> {
        let width = integer(arguments, 0)?;
        let height = integer(arguments, 1)?;
        Ok(json!([
            Json::String(width.to_string()),
            Json::String(height.to_string()),
            Json::String(width.saturating_mul(height).to_string()),
        ]))
    }
}

/// `Int` values use Diamond's canonical wire encoding: decimal strings.
fn integer(arguments: &[Json], index: usize) -> std::result::Result<i64, ComputeError> {
    arguments
        .get(index)
        .and_then(Json::as_str)
        .and_then(|value| value.parse().ok())
        .ok_or(ComputeError::InvalidInput)
}

fn main() -> Result<()> {
    let project =
        diamond::compile_project(std::path::Path::new("examples/compute-lab/diamond.toml"))?;
    let mut runtime = RuntimeGrants::default();
    runtime.register_compute("sim", "tick", &["Text", "Int"], "Text", Tick)?;
    runtime.register_compute("sim", "plan", &["Int", "Int"], "List<Int>", Plan)?;
    project.validate_compute(&runtime)?;

    let value = project.evaluate_with(&runtime)?.into_result()?;
    ensure!(
        value.display_text()? == "seed-0/7",
        "unexpected compute result: {}",
        value.display_text()?
    );

    let unregistered = project.evaluate_with(&RuntimeGrants::default())?;
    ensure!(
        unregistered.into_result().is_err(),
        "an unregistered kernel was accepted"
    );
    println!(
        "compute embedding: static call surface, export capabilities, deployment validation, and registered kernels passed"
    );
    Ok(())
}
