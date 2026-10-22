//! External public-API W4 consumer operated against a real isolated PostgreSQL.
#![deny(unsafe_code)]

use std::error::Error;
use std::path::Path;

fn main() -> Result<(), Box<dyn Error>> {
    let arguments = std::env::args().collect::<Vec<_>>();
    if arguments.len() != 3 {
        return Err("expected current and next project paths".into());
    }
    let configuration = std::env::var("DIAMOND_W4_INDEPENDENT_POSTGRES")?;
    let current = diamond::compile_project(Path::new(&arguments[1]))?;
    let next = diamond::compile_project(Path::new(&arguments[2]))?;
    current.initialize_postgres(&configuration)?;
    println!("{}", current.postgres_status(&configuration)?);
    println!("{}", current.audit_postgres(&configuration)?);
    println!("{}", next.migrate_postgres(&configuration)?);
    for batch in [0, 1_001] {
        if !matches!(
            next.backfill_postgres(&configuration, batch),
            Err(diamond::Error::Input(_))
        ) {
            return Err("out-of-range backfill batch did not fail as input".into());
        }
    }
    println!("{}", next.backfill_postgres(&configuration, 7)?);
    println!("{}", next.audit_postgres(&configuration)?);
    Ok(())
}
