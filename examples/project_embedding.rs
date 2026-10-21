//! A real consumer of checked project builds and explicit host grants.

use anyhow::{Context, Result, ensure};
use std::time::{SystemTime, UNIX_EPOCH};
use zeroize::Zeroizing;

fn main() -> Result<()> {
    let project =
        diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))
            .context("check example project")?;
    ensure!(
        project.evaluate(None).is_err(),
        "missing host grant was accepted"
    );
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let output = std::env::temp_dir().join(format!(
        "diamond-project-example-{}-{nonce}",
        std::process::id()
    ));
    project.build(&output).context("build example project")?;
    let built = diamond::load_project_build(&output).context("load checked artifact")?;
    ensure!(
        built.summary() == project.summary(),
        "artifact interface changed"
    );
    ensure!(
        built.evaluate(None).is_err(),
        "artifact manufactured a host grant"
    );
    if let Ok(configuration) = std::env::var("DIAMOND_EXAMPLE_POSTGRES") {
        let configuration = Zeroizing::new(configuration);
        let result = built.evaluate(Some(&configuration))?.display_text()?;
        ensure!(
            result.starts_with("A component written in Diamond / PostgreSQL "),
            "incorrect PostgreSQL result"
        );
    }
    std::fs::remove_dir_all(output).context("remove this example's disposable build")?;
    println!(
        "project embedding: checking, deterministic interfaces, artifact loading, and explicit host grants passed"
    );
    Ok(())
}
