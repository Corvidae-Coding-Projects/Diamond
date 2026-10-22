//! Run only against a deliberately granted disposable `PostgreSQL` database.
//! The caller explicitly supplies the connection, new account name and password.

use anyhow::{Context, Result};
use std::path::Path;
use zeroize::Zeroizing;

fn main() -> Result<()> {
    let project = diamond::compile_project(Path::new("examples/workspace/diamond.toml"))
        .context("check workspace project")?;
    let value = project
        .evaluate(None)
        .context("evaluate pure project entry")?;
    if value.display_text()? != "Workspace tracker" {
        anyhow::bail!("unexpected checked entry result");
    }
    let configuration = Zeroizing::new(
        std::env::var("DIAMOND_EXAMPLE_POSTGRES")
            .context("supply an explicit disposable PostgreSQL grant")?,
    );
    let username =
        std::env::var("DIAMOND_EXAMPLE_USER").context("name the new disposable account")?;
    let password = Zeroizing::new(
        std::env::var("DIAMOND_EXAMPLE_PASSWORD").context("supply the new account password")?,
    );
    project
        .initialize_postgres(&configuration)
        .context("initialize W2 profile")?;
    project
        .add_postgres_user(&configuration, &username, &password)
        .context("provision shared account")?;
    println!("W2 native project consumer passed");
    Ok(())
}
