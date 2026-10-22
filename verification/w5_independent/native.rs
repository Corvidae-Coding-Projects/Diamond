//! External public W5 API consumer. Run only on its owned disposable PostgreSQL.
#![deny(unsafe_code)]

use std::error::Error;
use std::path::Path;

fn main() -> Result<(), Box<dyn Error>> {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() != 6 {
        return Err("expected MODE PROJECT MAILBOX ADDRESS ORIGIN".into());
    }
    let configuration = std::env::var("DIAMOND_W5_INDEPENDENT_POSTGRES")?;
    let project = diamond::compile_project(Path::new(&arguments[2]))?;
    match arguments[1].as_str() {
        "initialize" => {
            project.initialize_postgres(&configuration)?;
            let password = std::env::var("DIAMOND_W5_NATIVE_PASSWORD")?;
            project.add_postgres_administrator(
                &configuration,
                "native-admin",
                "native-admin@qualification.invalid",
                &password,
            )?;
            if project
                .add_postgres_administrator(
                    &configuration,
                    "native-admin",
                    "replacement@qualification.invalid",
                    &password,
                )
                .is_ok()
            {
                return Err("duplicate administrator replaced an existing account".into());
            }
            println!("Native account provisioning completed without duplicate replacement");
        }
        "serve" => {
            project.serve_accounts(
                arguments[4].parse()?,
                &arguments[5],
                &configuration,
                Path::new(&arguments[3]),
            )?;
        }
        _ => return Err("unknown native qualification mode".into()),
    }
    Ok(())
}
