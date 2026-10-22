//! External consumer of the generated shared corpus through server evaluation.
#![deny(unsafe_code)]
fn main() -> Result<(), diamond::Error> {
    let path = std::env::args()
        .nth(1)
        .ok_or_else(|| diamond::Error::Input("project path required".into()))?;
    let project = diamond::compile_project(std::path::Path::new(&path))?;
    println!("{}", project.evaluate(None)?.display_text()?);
    Ok(())
}
