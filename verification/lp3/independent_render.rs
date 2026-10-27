//! Public API consumer of the actual checked renderer.
use std::io::BufRead;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let manifest = std::env::args().nth(1).ok_or("manifest")?;
    let project = diamond::compile_project(std::path::Path::new(&manifest))?;
    for target in std::io::stdin().lock().lines() {
        match project.render_page(&target?) {
            Ok(html) => {
                print!("OK ");
                for byte in html.bytes() { print!("{byte:02x}"); }
                println!();
            }
            Err(error) => println!("ERR {error}"),
        }
    }
    Ok(())
}
