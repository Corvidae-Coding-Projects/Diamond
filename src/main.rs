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

fn command_arguments() -> Result<Vec<String>> {
    std::env::args_os()
        .skip(1)
        .map(|argument| {
            argument
                .into_string()
                .map_err(|_| anyhow::Error::msg("command-line arguments must be valid UTF-8"))
        })
        .collect::<Result<Vec<_>>>()
}

fn try_main() -> Result<()> {
    let arguments = command_arguments()?;
    let Some(command) = arguments.first() else {
        help();
        return Ok(());
    };
    if ["--help", "help"].contains(&command.as_str()) {
        help();
        return Ok(());
    }
    if command == "--version" {
        println!(
            "diamond {} (W7 jobs/live apps and W8 operable deployment profile)",
            env!("CARGO_PKG_VERSION")
        );
        return Ok(());
    }
    if command == "explain" {
        return explain(&arguments[1..]);
    }
    if command == "new" {
        return scaffold(&arguments[1..]);
    }
    if command == "user-add" {
        return user_add(&arguments[1..]);
    }
    if [
        "build",
        "serve",
        "project-init",
        "project-files-init",
        "project-files-clean",
        "project-work",
        "project-deploy-check",
        "project-recover",
        "project-backup",
        "project-restore",
        "project-user-add",
        "project-admin-add",
        "project-status",
        "project-migrate",
        "project-backfill",
        "project-audit",
    ]
    .contains(&command.as_str())
        || (["check", "run"].contains(&command.as_str())
            && arguments.get(1).is_some_and(|path| {
                Path::new(path)
                    .extension()
                    .is_some_and(|extension| extension == "toml")
                    || Path::new(path).is_dir()
            }))
    {
        return project_command(command, &arguments[1..]);
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

#[derive(Debug)]
struct ProjectOptions<'a> {
    output: Option<PathBuf>,
    listen: &'a str,
    origin: Option<&'a str>,
    postgres_env: Option<&'a str>,
    runtime_env: Option<&'a str>,
    username: Option<&'a str>,
    password_stdin: bool,
    email: Option<&'a str>,
    mailbox: Option<PathBuf>,
    batch: usize,
    policy: Option<&'a str>,
    backup_key_env: Option<&'a str>,
    backup_source: Option<PathBuf>,
}

impl Default for ProjectOptions<'_> {
    fn default() -> Self {
        Self {
            output: None,
            listen: "127.0.0.1:3000",
            origin: None,
            postgres_env: None,
            runtime_env: None,
            username: None,
            password_stdin: false,
            email: None,
            mailbox: None,
            batch: 100,
            policy: None,
            backup_key_env: None,
            backup_source: None,
        }
    }
}

fn project_options<'a>(command: &str, arguments: &'a [String]) -> Result<ProjectOptions<'a>> {
    let mut parsed = ProjectOptions::default();
    let mut seen = BTreeSet::new();
    let mut cursor = 0;
    while cursor < arguments.len() {
        let option = &arguments[cursor];
        if !seen.insert(option) {
            bail!("repeated project option {}", safe_text(option));
        }
        if ["project-user-add", "project-admin-add"].contains(&command)
            && option == "--password-stdin"
        {
            parsed.password_stdin = true;
            cursor += 1;
            continue;
        }
        let value = arguments
            .get(cursor + 1)
            .context("project option requires a value")?;
        match (command, option.as_str()) {
            ("project-backup" | "project-restore", "--backup-key-env") => {
                parsed.backup_key_env = Some(value);
            }
            ("project-restore", "--from") => parsed.backup_source = Some(PathBuf::from(value)),
            ("project-backup" | "build", "--out") => parsed.output = Some(PathBuf::from(value)),
            ("project-recover", "--policy") => parsed.policy = Some(value),
            (
                "serve"
                | "project-files-init"
                | "project-files-clean"
                | "project-work"
                | "project-deploy-check"
                | "project-backup"
                | "project-restore",
                "--runtime-env",
            ) => {
                if value.is_empty()
                    || value.len() > 128
                    || !value
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_')
                {
                    bail!("invalid runtime environment variable name");
                }
                parsed.runtime_env = Some(value);
            }
            ("project-backfill", "--batch") => {
                parsed.batch = value.parse().context("invalid backfill batch")?;
            }

            ("serve", "--listen") => parsed.listen = value,
            ("serve" | "project-deploy-check", "--origin") => parsed.origin = Some(value),
            ("project-user-add" | "project-admin-add", "--user") => parsed.username = Some(value),
            ("project-admin-add", "--email") => parsed.email = Some(value),
            ("serve" | "project-deploy-check", "--identity-mailbox") => {
                parsed.mailbox = Some(PathBuf::from(value));
            }
            (
                "run"
                | "serve"
                | "project-init"
                | "project-files-init"
                | "project-files-clean"
                | "project-user-add"
                | "project-admin-add"
                | "project-status"
                | "project-migrate"
                | "project-backfill"
                | "project-audit"
                | "project-work"
                | "project-deploy-check"
                | "project-recover"
                | "project-backup"
                | "project-restore",
                "--postgres-env",
            ) => {
                if value.is_empty()
                    || value.len() > 128
                    || !value
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'_')
                {
                    bail!("invalid PostgreSQL environment variable name");
                }
                parsed.postgres_env = Some(value);
            }
            _ => bail!("unsupported project option {}", safe_text(option)),
        }
        cursor += 2;
    }
    Ok(parsed)
}

fn project_command(command: &str, arguments: &[String]) -> Result<()> {
    let path = Path::new(
        arguments
            .first()
            .context("a project manifest or build directory is required")?,
    );
    let options = project_options(command, &arguments[1..])?;
    let project = if path.is_dir() {
        diamond::load_project_build(path)
    } else {
        diamond::compile_project(path)
    }
    .map_err(|error| anyhow::Error::msg(safe_text(&error.to_string())))?;
    let configuration = project_configuration(&options)?;
    match command {
        "project-backup"
        | "project-restore"
        | "project-work"
        | "project-deploy-check"
        | "project-recover" => project_operation(
            command,
            &project,
            &options,
            configuration.as_deref().map(String::as_str),
        )?,
        "check" => print!("{}", project.summary()),
        "build" => {
            let output = options
                .output
                .context("build requires --out PATH naming a new directory")?;
            project.build(&output).context("build project")?;
            println!("built project at {}", safe_text(&output.to_string_lossy()));
        }
        "run" => {
            let value = project
                .evaluate(configuration.as_deref().map(String::as_str))
                .context("evaluate project")?
                .into_result()?;
            println!("{}", safe_text(&value.display_text()?));
        }
        "serve" => serve_project(
            &project,
            &options,
            configuration.as_deref().map(String::as_str),
        )?,
        "project-files-init" | "project-files-clean" => {
            let source = Zeroizing::new(
                std::env::var(
                    options
                        .runtime_env
                        .context("file administration requires --runtime-env VARIABLE")?,
                )
                .map_err(|_| {
                    anyhow::Error::msg("runtime configuration is missing or invalid UTF-8")
                })?,
            );
            let grants = diamond::RuntimeGrants::from_json(&source)?;
            println!(
                "{}",
                project.maintain_files(
                    configuration
                        .as_deref()
                        .context("file administration requires --postgres-env VARIABLE")?,
                    &grants,
                    command == "project-files-init"
                )?
            );
        }
        "project-init" => project
            .initialize_postgres(
                configuration
                    .as_deref()
                    .context("project-init requires --postgres-env VARIABLE")?,
            )
            .context("initialize project PostgreSQL profile")?,
        "project-status" | "project-migrate" | "project-backfill" | "project-audit" => {
            let configuration = configuration
                .as_deref()
                .context("relational administration requires --postgres-env VARIABLE")?;
            let report = match command {
                "project-status" => project.postgres_status(configuration),
                "project-migrate" => project.migrate_postgres(configuration),
                "project-backfill" => project.backfill_postgres(configuration, options.batch),
                _ => project.audit_postgres(configuration),
            }?;
            println!("{report}");
        }
        "project-admin-add" | "project-user-add" => provision_project_user(
            &project,
            &options,
            configuration.as_deref().map(String::as_str),
            command == "project-admin-add",
        )?,
        _ => bail!("unsupported project command"),
    }
    Ok(())
}

fn project_configuration(options: &ProjectOptions<'_>) -> Result<Option<Zeroizing<String>>> {
    options
        .postgres_env
        .map(|name| {
            std::env::var(name).map(Zeroizing::new).map_err(|_| {
                anyhow::Error::msg(
                    "PostgreSQL grant environment variable is missing or invalid UTF-8",
                )
            })
        })
        .transpose()
}

fn project_operation(
    command: &str,
    project: &diamond::CheckedProject,
    options: &ProjectOptions<'_>,
    configuration: Option<&str>,
) -> Result<()> {
    match command {
        "project-backup" | "project-restore" => {
            let config = configuration.context("backup operation requires --postgres-env")?;
            let runtime = Zeroizing::new(
                std::env::var(
                    options
                        .runtime_env
                        .context("backup requires --runtime-env")?,
                )
                .map_err(|_| anyhow::anyhow!("runtime configuration unavailable"))?,
            );
            let runtime = diamond::RuntimeGrants::from_json(&runtime)?;
            let key = Zeroizing::new(
                std::env::var(
                    options
                        .backup_key_env
                        .context("backup requires --backup-key-env")?,
                )
                .map_err(|_| anyhow::anyhow!("backup passphrase unavailable"))?,
            );
            let result = if command == "project-backup" {
                project.backup_project(
                    config,
                    &runtime,
                    options
                        .output
                        .as_deref()
                        .context("backup requires --out NEWDIR")?,
                    &key,
                )?
            } else {
                project.restore_project(
                    config,
                    &runtime,
                    options
                        .backup_source
                        .as_deref()
                        .context("restore requires --from DIRECTORY")?,
                    &key,
                )?
            };
            println!("{result}");
        }
        "project-work" | "project-deploy-check" | "project-recover" => {
            let config = configuration.context("operation requires --postgres-env")?;
            if command == "project-recover" {
                project.recover_jobs(
                    config,
                    options
                        .policy
                        .context("recovery requires --policy resume|cancel")?,
                )?;
            } else {
                let runtime = options
                    .runtime_env
                    .map(|name| {
                        std::env::var(name)
                            .map_err(|_| anyhow::anyhow!("runtime configuration unavailable"))
                    })
                    .transpose()?
                    .unwrap_or_else(|| "{}".into());
                let runtime = diamond::RuntimeGrants::from_json(&runtime)?;
                if command == "project-work" {
                    project.work_jobs(config, runtime)?;
                } else {
                    println!(
                        "{}",
                        project.validate_deployment(
                            config,
                            options.origin.context("deployment requires --origin")?,
                            &runtime,
                            options
                                .mailbox
                                .as_deref()
                                .context("deployment requires --identity-mailbox")?
                        )?
                    );
                }
            }
        }
        _ => bail!("unsupported operational command"),
    }
    Ok(())
}

fn serve_project(
    project: &diamond::CheckedProject,
    options: &ProjectOptions<'_>,
    configuration: Option<&str>,
) -> Result<()> {
    let address = options
        .listen
        .parse()
        .context("invalid project listener address")?;
    if let Some(configuration) = configuration {
        let origin = options
            .origin
            .context("connected serving requires --origin URL")?;
        if let Some(mailbox) = options.mailbox.as_deref() {
            if let Some(name) = options.runtime_env {
                let source = Zeroizing::new(std::env::var(name).map_err(|_| {
                    anyhow::Error::msg("runtime configuration is missing or invalid UTF-8")
                })?);
                project.serve_with_runtime(
                    address,
                    origin,
                    configuration,
                    mailbox,
                    diamond::RuntimeGrants::from_json(&source)?,
                )
            } else {
                project.serve_accounts(address, origin, configuration, mailbox)
            }
        } else {
            if options.runtime_env.is_some() {
                bail!("runtime grants require --identity-mailbox");
            }
            project.serve_connected(address, origin, configuration)
        }
        .context("serve connected project")?;
    } else {
        if options.origin.is_some() || options.mailbox.is_some() || options.runtime_env.is_some() {
            bail!("--origin and --identity-mailbox require an explicit PostgreSQL grant");
        }
        project.serve(address).context("serve project assets")?;
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
    let password = read_password(password_stdin)?;
    diamond::add_local_user(&accounts, username, &password).context("provision local account")?;
    println!("local account created");
    Ok(())
}

fn read_password(password_stdin: bool) -> Result<Zeroizing<String>> {
    Ok(if password_stdin {
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
    })
}

fn help() {
    println!(
        "Diamond {} — W7/W8 jobs, live apps and operable deployment\n\n  diamond new NEW_DIRECTORY [--template workspace|catalog]\n  diamond project-work PROJECT --postgres-env VARIABLE --runtime-env VARIABLE\n  diamond project-deploy-check PROJECT --postgres-env VARIABLE --runtime-env VARIABLE --origin https://app.example --identity-mailbox PRIVATE_DIRECTORY\n  diamond project-backup PROJECT --postgres-env VARIABLE --runtime-env VARIABLE --out NEW_DIRECTORY --backup-key-env VARIABLE\n  diamond project-restore PROJECT --postgres-env VARIABLE --runtime-env VARIABLE --from BACKUP_DIRECTORY --backup-key-env VARIABLE\n  diamond project-recover PROJECT --postgres-env VARIABLE --policy resume|cancel\n  diamond check FILE_OR_PROJECT\n  diamond build PROJECT --out NEW_DIRECTORY\n  diamond serve PROJECT_OR_BUILD [--listen 127.0.0.1:PORT] [--postgres-env VARIABLE --origin http://localhost:PORT] [--identity-mailbox PRIVATE_DIRECTORY] [--runtime-env VARIABLE]\n  diamond run PROJECT_OR_BUILD [--postgres-env VARIABLE]\n  diamond project-init PROJECT --postgres-env VARIABLE\n  diamond project-files-init PROJECT --postgres-env VARIABLE --runtime-env VARIABLE\n  diamond project-files-clean PROJECT --postgres-env VARIABLE --runtime-env VARIABLE\n  diamond project-user-add PROJECT --postgres-env VARIABLE --user USERNAME [--password-stdin]\n  diamond project-admin-add PROJECT --postgres-env VARIABLE --user USERNAME --email ADDRESS [--password-stdin]\n  diamond project-status PROJECT --postgres-env VARIABLE\n  diamond project-migrate PROJECT --postgres-env VARIABLE\n  diamond project-backfill PROJECT --postgres-env VARIABLE [--batch 100]\n  diamond project-audit PROJECT --postgres-env VARIABLE\n  diamond run FILE [--database PATH] [--auth PATH] [--listen 127.0.0.1:PORT] [--max-requests N]\n  diamond db-init FILE --database PATH\n  diamond user-add --auth PATH --user USERNAME [--password-stdin]\n  diamond explain CODE\n\nOpen http://localhost:PORT for browser secure-cookie support.\nSee README.md for installation, examples, supported language, and limits.",
        env!("CARGO_PKG_VERSION")
    );
}

fn provision_project_user(
    project: &diamond::CheckedProject,
    options: &ProjectOptions<'_>,
    configuration: Option<&str>,
    administrator: bool,
) -> Result<()> {
    let password = read_password(options.password_stdin)?;
    let configuration =
        configuration.context("user provisioning requires --postgres-env VARIABLE")?;
    let username = options
        .username
        .context("user provisioning requires --user USERNAME")?;
    if administrator {
        project
            .add_postgres_administrator(
                configuration,
                username,
                options
                    .email
                    .context("administrator requires --email ADDRESS")?,
                &password,
            )
            .context("provision accounts administrator")?;
    } else {
        project
            .add_postgres_user(configuration, username, &password)
            .context("provision shared project account")?;
    }
    println!("shared project account created");
    Ok(())
}

fn scaffold(arguments: &[String]) -> Result<()> {
    let destination = Path::new(arguments.first().context("new requires a new directory")?);
    let template = match arguments {
        [_] => "workspace",
        [_, option, value] if option == "--template" => value.as_str(),
        _ => bail!("new accepts DIRECTORY [--template workspace|catalog]"),
    };
    let files: &[(&str, &str)] = match template {
        "workspace" => &[
            (
                "diamond.toml",
                include_str!("../examples/live-workspace/diamond.toml"),
            ),
            (
                "server.dia",
                include_str!("../examples/live-workspace/server.dia"),
            ),
            (
                "shared.dia",
                include_str!("../examples/live-workspace/shared.dia"),
            ),
            (
                "cards.dia",
                include_str!("../examples/live-workspace/cards.dia"),
            ),
            (
                "tracker.dia",
                include_str!("../examples/live-workspace/tracker.dia"),
            ),
            (
                "tracker.css",
                include_str!("../examples/live-workspace/tracker.css"),
            ),
            (
                "diamond.svg",
                include_str!("../examples/live-workspace/diamond.svg"),
            ),
            (
                "README.md",
                include_str!("../examples/live-workspace/README.md"),
            ),
        ],
        "catalog" => &[
            (
                "diamond.toml",
                include_str!("../examples/catalog/diamond.toml"),
            ),
            ("server.dia", include_str!("../examples/catalog/server.dia")),
            ("ui.dia", include_str!("../examples/catalog/ui.dia")),
            (
                "catalog.css",
                include_str!("../examples/catalog/catalog.css"),
            ),
            ("README.md", include_str!("../examples/catalog/README.md")),
        ],
        _ => bail!("unknown template; use workspace or catalog"),
    };
    std::fs::create_dir(destination).context("scaffold destination must be new")?;
    for (name, content) in files {
        std::fs::write(destination.join(name), content).context("write scaffold input")?;
    }
    let project = diamond::compile_project(&destination.join("diamond.toml"))?;
    print!("{}", project.summary());
    Ok(())
}
