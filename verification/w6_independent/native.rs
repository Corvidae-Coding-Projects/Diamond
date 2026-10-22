//! An external consumer of the public W6 native interfaces, with no product internals.
#![deny(unsafe_code)]
use std::{error::Error, path::Path};

fn main() -> Result<(), Box<dyn Error>> {
    let args = std::env::args().collect::<Vec<_>>();
    if args.len() != 6 { return Err("MODE PROJECT MAILBOX ADDRESS ORIGIN required".into()); }
    let config = std::env::var("DIAMOND_W6_INDEPENDENT_POSTGRES")?;
    let project = diamond::compile_project(Path::new(&args[2]))?;
    let raw = std::env::var("DIAMOND_W6_INDEPENDENT_RUNTIME")?;
    let grants = diamond::RuntimeGrants::from_json(&raw)?;
    if format!("{grants:?}") != "RuntimeGrants([PRIVATE])" { return Err("native grant debug leaked configuration".into()); }
    match args[1].as_str() {
        "initialize" => { println!("{}",project.maintain_files(&config,&grants,true)?); }
        "clean" => { println!("{}",project.maintain_files(&config,&grants,false)?); }
        "serve" => { project.serve_with_runtime(args[4].parse()?,&args[5],&config,Path::new(&args[3]),grants)?; }
        _ => return Err("unknown mode".into()),
    }
    Ok(())
}
