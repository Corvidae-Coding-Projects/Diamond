//! External native caller for public W3 compile/evaluate/render APIs.
#![deny(unsafe_code)]
use std::path::Path;

fn main() -> Result<(), diamond::Error> {
    let argument = std::env::args()
        .nth(1)
        .ok_or_else(|| diamond::Error::Input("project path required".into()))?;
    let source = "fn main() -> Text:\n  decimal_text(decimal_multiply(decimal_units(-3), decimal_units(500000), \"half_even\"))\n";
    let program = diamond::compile(source)?;
    let value = program.evaluate()?.display_text()?;
    if value != "-0.000002" {
        return Err(diamond::Error::Input(format!(
            "native decimal mismatch: {value}"
        )));
    }
    let failed = diamond::compile(
        "enum AppError:\n  Rejected(reason: Text)\n\nfn main() -> Result<Int, AppError>:\n  Err(Rejected(\"native error payload\"))\n",
    )?;
    let result = failed.evaluate()?.into_result();
    if !matches!(result, Err(diamond::Error::Application(_))) {
        return Err(diamond::Error::Input("application error class lost".into()));
    }
    let host_failed = diamond::compile(
        "enum AppError:\n  Rejected\n\nfn main() -> Result<Int, AppError>:\n  Ok(1 / 0)\n",
    )?;
    if matches!(
        host_failed.evaluate(),
        Ok(_) | Err(diamond::Error::Application(_))
    ) {
        return Err(diamond::Error::Input(
            "runtime failure fabricated application error".into(),
        ));
    }
    let project = diamond::compile_project(Path::new(&argument))?;
    let page = project.render_page("/")?;
    if !page.contains("default &lt;text&gt;") || !page.contains(">17<") {
        return Err(diamond::Error::Input(
            "native SSR defaults/escaping mismatch".into(),
        ));
    }
    if project.render_page("/not-declared").is_ok() {
        return Err(diamond::Error::Input("undeclared route accepted".into()));
    }
    println!("native W3 consumer: 5 observable checks passed");
    Ok(())
}
