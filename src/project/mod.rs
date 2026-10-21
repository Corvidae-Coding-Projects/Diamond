//! Checked project graphs, target separation, and versioned application builds.

mod artifact;
mod browser;
mod lower;
mod resolve;
mod server;

use crate::authority::{Effect, Effects, Principal, Scope};
use crate::error::{Error, Result};
use crate::hir::CheckedProgram;
use crate::syntax::{
    Declaration, Expr, ExprKind, Function, Program, ProjectModule, Statement, StatementKind,
};
use crate::value::Value;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::net::SocketAddr;
use std::path::Path;

const FORMAT_VERSION: u32 = 1;
const MAX_PROJECT_BYTES: usize = 8 * crate::MAX_SOURCE_BYTES;
const LINE_STRIDE: usize = crate::MAX_SOURCE_BYTES + 1;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum Target {
    Server,
    Browser,
    Shared,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ModuleConfig {
    path: String,
    target: Target,
    #[serde(default)]
    imports: Vec<String>,
    #[serde(default)]
    provides: BTreeSet<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema_version: u32,
    name: String,
    server: String,
    browser: String,
    component: String,
    #[serde(default)]
    provides: BTreeSet<String>,
    modules: BTreeMap<String, ModuleConfig>,
}

#[derive(Clone, Debug)]
struct Module {
    config: ModuleConfig,
    parsed: ProjectModule,
    symbols: BTreeMap<String, String>,
    line_base: usize,
}

/// A checked multi-file application with separated server and browser targets.
/// Host access is supplied explicitly at execution and is never loaded from a build.
///
/// ```no_run
/// let project = diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
/// assert!(project.summary().contains("browser"));
/// # Ok::<(), diamond::Error>(())
/// ```
#[derive(Clone, Debug)]
pub struct CheckedProject {
    manifest_source: String,
    sources: BTreeMap<String, String>,
    manifest: Manifest,
    modules: BTreeMap<String, Module>,
    server: CheckedProgram,
    browser: lower::BrowserProgram,
}

/// Read and check a bounded project without opening a database or listener.
///
/// # Errors
/// Rejects invalid manifests, source paths, modules, types, targets, or effects.
///
/// ```no_run
/// let project = diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
/// assert!(project.summary().contains("PostgresRead"));
/// # Ok::<(), diamond::Error>(())
/// ```
pub fn compile_project(path: &Path) -> Result<CheckedProject> {
    let path = path.canonicalize()?;
    let root = path
        .parent()
        .ok_or_else(|| input("project manifest needs a parent directory"))?;
    let manifest_source = artifact::read_text(&path, crate::MAX_SOURCE_BYTES)?;
    let manifest = manifest(&manifest_source)?;
    let mut sources = BTreeMap::new();
    let mut total = manifest_source.len();
    for module in manifest.modules.values() {
        resolve::relative_path(&module.path)?;
        let source_path = root.join(&module.path).canonicalize()?;
        if !source_path.starts_with(root) {
            return Err(input("module path escapes the project root"));
        }
        let source = artifact::read_text(&source_path, crate::MAX_SOURCE_BYTES)?;
        total += source.len();
        if total > MAX_PROJECT_BYTES {
            return Err(Error::Limit("project source bytes"));
        }
        sources.insert(module.path.clone(), source);
    }
    compile_sources(manifest_source, sources)
}

/// Load a build by rechecking its source and verifying all generated artifacts.
///
/// # Errors
/// Rejects tampering, unsupported versions, missing files, invalid source, or I/O failures.
///
/// ```no_run
/// let project = diamond::load_project_build(std::path::Path::new(".diamond/build"))?;
/// assert!(project.summary().contains("server"));
/// # Ok::<(), diamond::Error>(())
/// ```
pub fn load_project_build(directory: &Path) -> Result<CheckedProject> {
    artifact::load(directory)
}

impl CheckedProject {
    /// Describe targets, exported interfaces, and maximum host requirements.
    ///
    /// ```no_run
    /// let project = diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
    /// assert!(project.summary().contains("Foundations"));
    /// # Ok::<(), diamond::Error>(())
    /// ```
    #[must_use]
    pub fn summary(&self) -> String {
        format!(
            "project {}: {} modules; server {}; browser {} / {}; capabilities {:?}\n",
            self.manifest.name,
            self.modules.len(),
            self.manifest.server,
            self.manifest.browser,
            self.manifest.component,
            self.manifest.provides
        )
    }

    /// Evaluate the checked server entry with an optional explicit local `PostgreSQL` grant.
    /// The connection string is never put in build artifacts or diagnostic output.
    ///
    /// # Errors
    /// Rejects missing grants, unsupported connection profiles, and runtime failures.
    ///
    /// ```no_run
    /// let project = diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
    /// assert!(project.evaluate(None).is_err());
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn evaluate(&self, postgres_configuration: Option<&str>) -> Result<Value> {
        let principal = Principal::Anonymous;
        let mut grants = Effects::new();
        if postgres_configuration.is_some() {
            if !self.manifest.provides.contains("PostgresRead") {
                return Err(Error::Denied);
            }
            grants.insert(Effect::Host("PostgresRead".into()));
        }
        let scope = Scope::new(&principal, &grants);
        for effect in &self
            .server
            .functions
            .get("main")
            .ok_or(Error::Denied)?
            .effects
        {
            scope.require(effect)?;
        }
        let mut interpreter = crate::eval::Interpreter::new(&self.server, &scope, None, None);
        if let Some(configuration) = postgres_configuration {
            let mut host = crate::host::HostRuntime::connect(configuration)?;
            interpreter.with_host(&mut host).main()
        } else {
            interpreter.main()
        }
    }

    /// Write deterministic server/browser artifacts to a new output directory.
    ///
    /// # Errors
    /// Rejects an existing destination and returns serialization or filesystem errors.
    ///
    /// ```no_run
    /// let project = diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
    /// project.build(std::path::Path::new(".diamond/new-build"))?;
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn build(&self, output: &Path) -> Result<()> {
        artifact::write(self, output)
    }

    /// Serve the compiled browser application concurrently on loopback.
    /// This W1 skeleton exposes no server function, login, RPC, or production profile.
    ///
    /// # Errors
    /// Rejects non-loopback addresses, an existing async runtime, or listener/runtime failures.
    ///
    /// ```no_run
    /// let project = diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
    /// project.serve("127.0.0.1:3000".parse().map_err(|_|diamond::Error::Input("address".into()))?)?;
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn serve(&self, address: SocketAddr) -> Result<()> {
        server::serve(self, address)
    }
}

fn manifest(source: &str) -> Result<Manifest> {
    let manifest: Manifest = toml::from_str(source)
        .map_err(|error| input(&format!("invalid project manifest: {error}")))?;
    if manifest.schema_version != FORMAT_VERSION {
        return Err(input("unsupported project manifest version"));
    }
    if !resolve::identifier(&manifest.name)
        || manifest.modules.is_empty()
        || manifest.modules.len() > 64
    {
        return Err(input("project needs a valid name and 1..64 modules"));
    }
    if !resolve::identifier(&manifest.component) {
        return Err(input("invalid root component name"));
    }
    if manifest.provides.iter().any(|name| name != "PostgresRead") {
        return Err(input("unknown application host capability"));
    }
    let mut paths = BTreeSet::new();
    for (name, config) in &manifest.modules {
        resolve::relative_path(&config.path)?;
        if !resolve::identifier(name) || !paths.insert(&config.path) || config.imports.len() > 64 {
            return Err(input(
                "module names/paths must be valid and unique; imports are bounded",
            ));
        }
        if !config.provides.is_subset(&manifest.provides) {
            return Err(input("module host budget exceeds the application budget"));
        }
        if config.target != Target::Server && !config.provides.is_empty() {
            return Err(input(
                "browser/shared modules cannot receive host capabilities",
            ));
        }
    }
    for (name, target) in [
        (&manifest.server, Target::Server),
        (&manifest.browser, Target::Browser),
    ] {
        if !manifest
            .modules
            .get(name)
            .is_some_and(|module| module.target == target)
        {
            return Err(input("entry module is missing or has the wrong target"));
        }
    }
    resolve::order(&manifest.modules)?;
    Ok(manifest)
}

fn compile_sources(
    manifest_source: String,
    sources: BTreeMap<String, String>,
) -> Result<CheckedProject> {
    let manifest = manifest(&manifest_source)?;
    if sources.len() != manifest.modules.len()
        || sources.values().map(String::len).sum::<usize>() + manifest_source.len()
            > MAX_PROJECT_BYTES
    {
        return Err(Error::Limit("project sources"));
    }
    let modules = parse_modules(&manifest, &sources)?;
    let server = check_server(&manifest, &modules)?;
    let browser_names = resolve::reachable(&manifest.browser, &manifest.modules);
    let browser_source = combined(&modules, &browser_names);
    let browser_checked =
        crate::checker::check(&browser_source).map_err(|error| locate(&modules, error))?;
    let root = &modules[&manifest.browser];
    if !root.parsed.exports.contains(&manifest.component) {
        return Err(input("root browser component must be exported"));
    }
    let component = root
        .parsed
        .components
        .iter()
        .find(|component| component.name == manifest.component)
        .ok_or_else(|| input("root browser export is not a component"))?;
    let browser =
        lower::lower(&browser_checked, component).map_err(|error| locate(&modules, error))?;
    Ok(CheckedProject {
        manifest_source,
        sources,
        manifest,
        modules,
        server,
        browser,
    })
}

fn parse_modules(
    manifest: &Manifest,
    sources: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, Module>> {
    let mut modules = BTreeMap::new();
    for (index, (name, config)) in manifest.modules.iter().enumerate() {
        let source = sources
            .get(&config.path)
            .ok_or_else(|| input("missing module source"))?;
        let parsed = crate::parser::project_module(source)
            .map_err(|error| source_error(&config.path, error))?;
        if config.target != Target::Browser && !parsed.components.is_empty() {
            return Err(input("components belong to browser modules"));
        }
        modules.insert(
            name.clone(),
            Module {
                config: config.clone(),
                parsed,
                symbols: BTreeMap::new(),
                line_base: index * LINE_STRIDE,
            },
        );
    }
    if modules
        .values()
        .map(|module| module.parsed.program.declarations.len() + module.parsed.components.len())
        .sum::<usize>()
        > 4096
    {
        return Err(Error::Limit("project declarations"));
    }
    resolve::symbols(&mut modules).map_err(|error| locate(&modules, error))?;
    let server_names = resolve::reachable(&manifest.server, &manifest.modules);
    let browser_names = resolve::reachable(&manifest.browser, &manifest.modules);
    if server_names.union(&browser_names).count() != modules.len() {
        return Err(input(
            "every declared module must be reachable from an entry",
        ));
    }
    if modules
        .values()
        .map(|module| module.parsed.components.len())
        .sum::<usize>()
        != 1
    {
        return Err(input(
            "W1 projects require exactly one root browser component",
        ));
    }
    Ok(modules)
}

fn check_server(manifest: &Manifest, modules: &BTreeMap<String, Module>) -> Result<CheckedProgram> {
    let names = resolve::reachable(&manifest.server, &manifest.modules);
    let mut source = combined(modules, &names);
    let entry_module = &modules[&manifest.server];
    if !entry_module.parsed.exports.contains("main") {
        return Err(input("server entry module must export fn main"));
    }
    let entry = entry_module
        .symbols
        .get("main")
        .ok_or_else(|| input("missing server main"))?;
    source.declarations.push(entry_wrapper(&source, entry)?);
    let grants = manifest
        .provides
        .iter()
        .map(|name| Effect::Host(name.clone()))
        .collect();
    let server =
        crate::checker::check_project(&source, grants).map_err(|error| locate(modules, error))?;
    for name in &names {
        let module = &modules[name];
        for declaration in &module.parsed.program.declarations {
            if let Declaration::Function(function) = declaration {
                let checked = &server.functions[&function.name];
                if checked.effects.iter().any(|effect| !matches!(effect,Effect::Host(capability) if module.config.provides.contains(capability))) {
                    return Err(source_error(&module.config.path,input("function effects exceed its module budget")));
                }
            }
        }
    }
    Ok(server)
}

fn entry_wrapper(source: &Program, entry: &str) -> Result<Declaration> {
    let function = source
        .declarations
        .iter()
        .find_map(|declaration| match declaration {
            Declaration::Function(function) if function.name == entry => Some(function),
            _ => None,
        })
        .ok_or_else(|| input("server main must be a function"))?;
    if !function.parameters.is_empty() {
        return Err(input("server main must have zero arguments"));
    }
    let location = function.location;
    Ok(Declaration::Function(Function {
        name: "main".into(),
        parameters: vec![],
        result: function.result.clone(),
        location,
        body: vec![Statement {
            location,
            kind: StatementKind::Expression(Expr {
                location,
                kind: ExprKind::Call {
                    callee: Box::new(Expr {
                        location,
                        kind: ExprKind::Name(entry.into()),
                    }),
                    arguments: vec![],
                    types: vec![],
                },
            }),
        }],
    }))
}

fn combined(modules: &BTreeMap<String, Module>, names: &BTreeSet<String>) -> Program {
    Program {
        declarations: names
            .iter()
            .flat_map(|name| modules[name].parsed.program.declarations.clone())
            .collect(),
    }
}

fn locate(modules: &BTreeMap<String, Module>, mut error: Error) -> Error {
    let (Error::Diagnostic { location, .. } | Error::Unsupported { location, .. }) = &mut error
    else {
        return error;
    };
    if let Some(module) = modules.values().find(|module| {
        location.line > module.line_base && location.line <= module.line_base + LINE_STRIDE
    }) {
        location.line -= module.line_base;
        return source_error(&module.config.path, error);
    }
    error
}

fn source_error(path: &str, error: Error) -> Error {
    Error::Source {
        path: path.into(),
        source: Box::new(error),
    }
}
fn input(message: &str) -> Error {
    Error::Input(message.into())
}
