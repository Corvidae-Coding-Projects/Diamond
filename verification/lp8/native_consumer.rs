//! External LP8 consumer of Diamond's public checked-language API.
#![deny(unsafe_code)]

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let program = diamond::compile("fn main() -> Int:\n  6 * 7\n")?;
    assert_eq!(program.evaluate()?.display_text()?, "42");

    let source = "# external consumer\nfn main() -> Text:\n    \"native 雪\"\n";
    let formatted = diamond::format_source(source)?;
    assert_eq!(diamond::format_source(&formatted)?, formatted);
    assert!(formatted.contains("  \"native 雪\""));

    let error = diamond::compile("fn main() -> Int:\n  \"wrong\"\n")
        .expect_err("a checked type mismatch must not create an executable program");
    match error {
        diamond::Error::Diagnostic {
            code,
            location,
            message,
        } => {
            assert_eq!(code, "D110");
            assert_eq!((location.line(), location.column()), (2, 3));
            assert!(message.contains("expected Int, found Text"));
        }
        other => return Err(format!("unexpected public diagnostic: {other}").into()),
    }

    println!("checked=42 formatted=stable diagnostic=D110:2:3");
    Ok(())
}
