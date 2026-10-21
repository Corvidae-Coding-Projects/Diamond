//! Command-line entry point for the Diamond reference prototype.

mod diagnostic_help;

use anyhow::{Context, Result, bail};
use std::collections::BTreeSet;
use std::fmt;
use std::fmt::Write as _;
use std::io::Read;
use std::net::SocketAddr;
use std::path::Path;
use std::path::PathBuf;
use zeroize::Zeroizing;

#[derive(Debug)]
struct Options {
    database: Option<PathBuf>,
    accounts: Option<PathBuf>,
    listen: SocketAddr,
    maximum: Option<usize>,
}

#[derive(Debug)]
struct RenderedDiagnostic(String);

impl fmt::Display for RenderedDiagnostic {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for RenderedDiagnostic {}

fn main() {
    if let Err(error) = try_main() {
        if let Some(diagnostic) = error.downcast_ref::<RenderedDiagnostic>() {
            eprintln!("{diagnostic}");
        } else {
            eprintln!("error: {}", safe_text(&format!("{error:#}")));
        }
        std::process::exit(1);
    }
}

fn try_main() -> Result<()> {
    let arguments = std::env::args_os()
        .skip(1)
        .map(|argument| {
            argument
                .into_string()
                .map_err(|_| anyhow::Error::msg("command-line arguments must be valid UTF-8"))
        })
        .collect::<Result<Vec<_>>>()?;
    let Some(command) = arguments.first() else {
        help();
        return Ok(());
    };
    if ["--help", "help"].contains(&command.as_str()) {
        help();
        return Ok(());
    }
    if command == "--version" {
        println!("diamond {} (checkpoint six)", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if command == "explain" {
        return explain(&arguments[1..]);
    }
    if command == "user-add" {
        return user_add(&arguments[1..]);
    }
    if !["check", "run", "db-init"].contains(&command.as_str()) {
        bail!("unsupported command {}; use --help", safe_text(command));
    }
    let path = arguments
        .get(1)
        .context("a source file is required; use --help")?;
    let options = parse_options(&arguments[2..])?;
    if command == "check" && arguments.len() != 2 {
        bail!("check accepts only a source file and has no execution side effects");
    }
    let source = read_source(path)?;
    let program = diamond::compile(&source).map_err(|error| {
        anyhow::Error::new(RenderedDiagnostic(render_diagnostic(
            Path::new(path),
            &source,
            &error,
        )))
    })?;
    match command.as_str() {
        "check" => print!("checked {}: {}", safe_text(path), program.summary()),
        "db-init" => {
            if arguments[2..]
                .iter()
                .any(|value| ["--auth", "--listen", "--max-requests"].contains(&value.as_str()))
            {
                bail!("db-init accepts only --database PATH");
            }
            program
                .initialize_database(
                    options
                        .database
                        .as_deref()
                        .context("db-init requires --database PATH")?,
                )
                .context("initialize checked database schema")?;
            println!("initialized checked schema");
        }
        "run" => run(&program, &options)?,
        _ => bail!("unsupported command"),
    }
    Ok(())
}

fn read_source(path: &str) -> Result<String> {
    let mut bytes = Vec::with_capacity(diamond::MAX_SOURCE_BYTES + 1);
    open_source(path)?
        .take((diamond::MAX_SOURCE_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .with_context(|| format!("read Diamond source {}", safe_text(path)))?;
    if bytes.len() > diamond::MAX_SOURCE_BYTES {
        bail!(
            "Diamond source size exceeds the {}-byte limit",
            diamond::MAX_SOURCE_BYTES
        );
    }
    String::from_utf8(bytes).context("Diamond source must be valid UTF-8")
}

fn open_source(path: &str) -> Result<std::fs::File> {
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = options
        .open(path)
        .with_context(|| format!("read Diamond source {}", safe_text(path)))?;
    if !file
        .metadata()
        .with_context(|| format!("inspect Diamond source {}", safe_text(path)))?
        .is_file()
    {
        bail!("Diamond source must be a regular file");
    }
    Ok(file)
}

fn explain(arguments: &[String]) -> Result<()> {
    let [requested] = arguments else {
        bail!("explain requires exactly one diagnostic code, such as D004");
    };
    let Some(explanation) = diagnostic_help::lookup(requested) else {
        bail!("unknown diagnostic code {}", safe_text(requested));
    };
    println!(
        "{} — {}\n{}",
        explanation.code, explanation.title, explanation.remedy
    );
    Ok(())
}

fn render_diagnostic(path: &Path, source: &str, error: &diamond::Error) -> String {
    let (code, location, message) = match error {
        diamond::Error::Diagnostic {
            code,
            location,
            message,
        } => (*code, *location, message.clone()),
        diamond::Error::Unsupported { location, feature } => {
            ("D900", *location, format!("unsupported feature: {feature}"))
        }
        _ => {
            return format!(
                "error: check {}: {error}",
                safe_text(&path.to_string_lossy())
            );
        }
    };
    let line = location.line();
    let column = location.column();
    let safe_path = safe_text(&path.to_string_lossy());
    let mut rendered = format!("error[{code}]: {message}\n --> {safe_path}:{line}:{column}");
    if let Some(source_line) = source.lines().nth(line.saturating_sub(1)) {
        let (excerpt, caret) = source_excerpt(source_line, column);
        let width = line.to_string().len();
        assert!(
            write!(
                rendered,
                "\n{blank:>width$} |\n{line:>width$} | {excerpt}\n{blank:>width$} | {caret:>caret_width$}^",
                blank = "",
                width = width,
                caret = "",
                caret_width = caret,
            )
            .is_ok(),
            "writing to a String cannot fail"
        );
    }
    rendered
}

fn source_excerpt(source_line: &str, column: usize) -> (String, usize) {
    const MAXIMUM: usize = 120;
    const BEFORE: usize = 80;
    let characters: Vec<_> = source_line.chars().collect();
    let target = column.saturating_sub(1).min(characters.len());
    let start = target.saturating_sub(BEFORE);
    let end = (start + MAXIMUM).min(characters.len());
    let leading_ellipsis = usize::from(start > 0);
    let trailing_ellipsis = end < characters.len();
    let mut excerpt = String::new();
    if leading_ellipsis == 1 {
        excerpt.push('…');
    }
    for character in &characters[start..end] {
        excerpt.push(safe_character(*character));
    }
    if trailing_ellipsis {
        excerpt.push('…');
    }
    (excerpt, target - start + leading_ellipsis)
}

fn safe_text(value: &str) -> String {
    value.chars().map(safe_character).collect()
}

fn safe_character(character: char) -> char {
    if character.is_control()
        || matches!(
            character,
            '\u{200e}'
                | '\u{200f}'
                | '\u{202a}'..='\u{202e}'
                | '\u{2066}'..='\u{2069}'
        )
    {
        '�'
    } else {
        character
    }
}

fn run(program: &diamond::CheckedProgram, options: &Options) -> Result<()> {
    if program.has_routes() {
        match options.accounts.as_deref() {
            Some(accounts) => program.serve_with_auth(
                options.listen,
                options.database.as_deref(),
                accounts,
                options.maximum,
            ),
            None => program.serve(options.listen, options.database.as_deref(), options.maximum),
        }
        .context("serve checked program")?;
    } else {
        if options.accounts.is_some() || options.maximum.is_some() {
            bail!("HTTP options require a program with routes");
        }
        let value = match options.database.as_deref() {
            Some(path) => program.evaluate_with_database(path),
            None => program.evaluate(),
        }
        .context("execute main")?;
        println!(
            "{}",
            value
                .into_result()
                .context("main returned an error")?
                .display_text()
                .context("display main result; return a scalar or typed HTML")?
        );
    }
    Ok(())
}

fn parse_options(arguments: &[String]) -> Result<Options> {
    let mut options = Options {
        database: None,
        accounts: None,
        listen: "127.0.0.1:3000".parse().context("parse default listener")?,
        maximum: None,
    };
    let mut seen = BTreeSet::new();
    let mut cursor = 0;
    while cursor < arguments.len() {
        let option = &arguments[cursor];
        if !seen.insert(option) {
            bail!("duplicate option {}", safe_text(option));
        }
        let value = arguments
            .get(cursor + 1)
            .with_context(|| format!("{} requires a value", safe_text(option)))?;
        match option.as_str() {
            "--database" => options.database = Some(PathBuf::from(value)),
            "--auth" => options.accounts = Some(PathBuf::from(value)),
            "--listen" => {
                options.listen = value.parse().context("parse loopback listener address")?;
            }
            "--max-requests" => {
                let count = value.parse().context("parse request limit")?;
                if count == 0 {
                    bail!("request limit must be positive");
                }
                options.maximum = Some(count);
            }
            _ => bail!("unknown option {}", safe_text(option)),
        }
        cursor += 2;
    }
    Ok(options)
}

fn user_add(arguments: &[String]) -> Result<()> {
    let mut accounts = None;
    let mut username = None;
    let mut password_stdin = false;
    let mut seen = BTreeSet::new();
    let mut cursor = 0;
    while cursor < arguments.len() {
        let option = &arguments[cursor];
        if !seen.insert(option) {
            bail!("duplicate option {}", safe_text(option));
        }
        if option == "--password-stdin" {
            password_stdin = true;
            cursor += 1;
            continue;
        }
        let value = arguments
            .get(cursor + 1)
            .with_context(|| format!("{} requires a value", safe_text(option)))?;
        match option.as_str() {
            "--auth" => accounts = Some(PathBuf::from(value)),
            "--user" => username = Some(value.as_str()),
            _ => bail!("unsupported user-add option {}", safe_text(option)),
        }
        cursor += 2;
    }
    let accounts = accounts.context("user-add requires --auth PATH")?;
    let username = username.context("user-add requires --user USERNAME")?;
    let password = if password_stdin {
        let mut input = Zeroizing::new(String::new());
        std::io::stdin()
            .lock()
            .take(1026)
            .read_to_string(&mut input)
            .context("read password from stdin")?;
        if input.ends_with('\n') {
            input.pop();
            if input.ends_with('\r') {
                input.pop();
            }
        }
        if input.contains(['\n', '\r']) {
            bail!("password stdin accepts one line");
        }
        input
    } else {
        Zeroizing::new(
            rpassword::prompt_password("Password: ").context("read password without echo")?,
        )
    };
    diamond::add_local_user(&accounts, username, &password).context("provision local account")?;
    println!("local account created");
    Ok(())
}

fn help() {
    println!(
        "Diamond {} — checkpoint-six interpreted MVP\n\n  diamond check FILE\n  diamond run FILE [--database PATH] [--auth PATH] [--listen 127.0.0.1:PORT] [--max-requests N]\n  diamond db-init FILE --database PATH\n  diamond user-add --auth PATH --user USERNAME [--password-stdin]\n  diamond explain CODE\n\nOpen http://localhost:PORT for browser secure-cookie support.\nSee README.md for installation, examples, supported language, and limits.",
        env!("CARGO_PKG_VERSION")
    );
}
