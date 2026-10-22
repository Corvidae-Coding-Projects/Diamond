//! Checked project graphs, target separation, and versioned application builds.

pub(crate) mod artifact;
mod assets;
mod browser;
mod connected;
mod identity;
mod lower;
mod render;
pub(crate) mod resolve;
mod rpc;
mod server;
pub(crate) mod storage;
pub(crate) mod wire;

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
    #[serde(default)]
    rpc: BTreeMap<String, String>,
    #[serde(default)]
    styles: Vec<String>,
    #[serde(default)]
    assets: BTreeMap<String, String>,
    #[serde(default)]
    storage: Option<crate::relational::Storage>,
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
    browser_checked: CheckedProgram,
    rpc: BTreeMap<String, rpc::Operation>,
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
    let paths = manifest
        .modules
        .values()
        .map(|m| m.path.clone())
        .chain(manifest.styles.iter().cloned())
        .chain(manifest.assets.values().cloned())
        .collect::<BTreeSet<_>>();
    for input_path in paths {
        resolve::relative_path(&input_path)?;
        let source_path = root.join(&input_path).canonicalize()?;
        if !source_path.starts_with(root) {
            return Err(input("module path escapes the project root"));
        }
        let source = artifact::read_text(&source_path, crate::MAX_SOURCE_BYTES)?;
        total += source.len();
        if total > MAX_PROJECT_BYTES {
            return Err(Error::Limit("project source bytes"));
        }
        sources.insert(input_path, source);
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

    /// Render a declared route's public initial component content without host authority.
    ///
    /// # Errors
    /// Rejects unknown routes, invalid initial values and resource-budget failures.
    ///
    /// ```
    /// let project=diamond::compile_project(std::path::Path::new("examples/foundations/diamond.toml"))?;
    /// assert!(project.render_page("/")?.contains("diamond-component"));
    /// assert!(project.render_page("/not-declared").is_err());
    /// # Ok::<(),diamond::Error>(())
    /// ```
    pub fn render_page(&self, path: &str) -> Result<String> {
        if !self.browser.routes.contains(path) {
            return Err(input("unknown browser route"));
        }
        render::render(self, path)
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
        if !self.rpc.is_empty() {
            return Err(input(
                "RPC projects require serve_connected with an explicit PostgreSQL grant and origin",
            ));
        }
        server::serve(self, address)
    }

    /// Initialize the explicitly selected `PostgreSQL` storage profile.
    ///
    /// # Errors
    /// Rejects missing/unsupported connection grants or an incompatible stored schema.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/workspace/diamond.toml"))?;
    /// project.initialize_postgres("host=/tmp/postgres user=diamond dbname=diamond")?;
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn initialize_postgres(&self, configuration: &str) -> Result<()> {
        synchronous_context()?;
        let mut store = storage::PostgresStore::open(self, configuration, true)?;
        store.finish_request(Ok(()))
    }

    /// Inspect current schema version, shape and backfill progress.
    /// Returns a JSON object; this administrative interface requires separate database credentials.
    /// Application servers must be stopped before a schema migration; there is no mixed-version support.
    ///
    /// # Errors
    /// Rejects missing relational metadata, incompatible schemas, invalid batches or database failures.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/relational-workspace/diamond.toml"))?;
    /// let report=project.postgres_status("host=/tmp/postgres dbname=diamond")?;
    /// assert!(report.starts_with('{'));
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn postgres_status(&self, configuration: &str) -> Result<String> {
        synchronous_context()?;
        if self.server.storage.is_none() {
            return Err(input("administrative command requires relational-v1"));
        }
        let mut connection = crate::host::HostRuntime::connect_worker(configuration)?;
        connection.enable_relational();
        let app = artifact::hash(self.manifest.name.as_bytes());
        crate::relational::status(&mut connection, &self.server, &app)
    }

    /// Apply one additive schema version under the migration coordinator.
    /// Returns a JSON object; this administrative interface requires separate database credentials.
    /// Application servers must be stopped before a schema migration; there is no mixed-version support.
    ///
    /// # Errors
    /// Rejects missing relational metadata, incompatible schemas, invalid batches or database failures.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/relational-workspace/diamond.toml"))?;
    /// let report=project.migrate_postgres("host=/tmp/postgres dbname=diamond")?;
    /// assert!(report.starts_with('{'));
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn migrate_postgres(&self, configuration: &str) -> Result<String> {
        synchronous_context()?;
        if self.server.storage.is_none() {
            return Err(input("administrative command requires relational-v1"));
        }
        let mut connection = crate::host::HostRuntime::connect_worker(configuration)?;
        connection.enable_relational();
        let app = artifact::hash(self.manifest.name.as_bytes());
        crate::relational::migrate(&mut connection, &self.server, &app)
    }

    /// Commit one resumable backfill batch of 1 through 1,000 rows.
    /// Returns a JSON object; this administrative interface requires separate database credentials.
    /// Application servers must be stopped before a schema migration; there is no mixed-version support.
    ///
    /// # Errors
    /// Rejects missing relational metadata, incompatible schemas, invalid batches or database failures.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/relational-workspace/diamond.toml"))?;
    /// let report=project.backfill_postgres("host=/tmp/postgres dbname=diamond", 100)?;
    /// assert!(report.starts_with('{'));
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn backfill_postgres(&self, configuration: &str, batch: usize) -> Result<String> {
        synchronous_context()?;
        if self.server.storage.is_none() {
            return Err(input("administrative command requires relational-v1"));
        }
        let mut connection = crate::host::HostRuntime::connect_worker(configuration)?;
        connection.enable_relational();
        let app = artifact::hash(self.manifest.name.as_bytes());
        crate::relational::backfill(&mut connection, &self.server, &app, batch)
    }

    /// Validate stored records in bounded batches under a consistent snapshot.
    /// Returns a JSON object; this administrative interface requires separate database credentials.
    /// Application servers must be stopped before a schema migration; there is no mixed-version support.
    ///
    /// # Errors
    /// Rejects missing relational metadata, incompatible schemas, invalid batches or database failures.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/relational-workspace/diamond.toml"))?;
    /// let report=project.audit_postgres("host=/tmp/postgres dbname=diamond")?;
    /// assert!(report.starts_with('{'));
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn audit_postgres(&self, configuration: &str) -> Result<String> {
        synchronous_context()?;
        if self.server.storage.is_none() {
            return Err(input("administrative command requires relational-v1"));
        }
        let mut connection = crate::host::HostRuntime::connect_worker(configuration)?;
        connection.enable_relational();
        let app = artifact::hash(self.manifest.name.as_bytes());
        crate::relational::audit(&mut connection, &self.server, &app)
    }

    /// Provision a shared development account without replacing an existing password.
    ///
    /// # Errors
    /// Rejects invalid credentials, duplicate accounts, incompatible stores and I/O failures.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/workspace/diamond.toml"))?;
    /// project.add_postgres_user("host=/tmp/postgres dbname=diamond", "alice", "a-long-test-password")?;
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn add_postgres_user(
        &self,
        configuration: &str,
        username: &str,
        password: &str,
    ) -> Result<()> {
        synchronous_context()?;
        let mut store = storage::PostgresStore::open(self, configuration, false)?;
        let result = store.add_user(username, password);
        store.finish_request(result)
    }

    /// Serve generated RPC and a Diamond browser component with shared `PostgreSQL` sessions.
    /// The exact public origin is the loopback proxy/browser origin, not a forwarded header.
    ///
    /// # Errors
    /// Rejects invalid origins/listeners, missing stores/grants and runtime failures.
    ///
    /// ```no_run
    /// let project=diamond::compile_project(std::path::Path::new("examples/workspace/diamond.toml"))?;
    /// project.serve_connected("127.0.0.1:3001".parse().map_err(|_|diamond::Error::Input("address".into()))?,
    ///     "http://localhost:3000", "host=/tmp/postgres dbname=diamond")?;
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn serve_connected(
        &self,
        address: SocketAddr,
        public_origin: &str,
        configuration: &str,
    ) -> Result<()> {
        connected::serve(self, address, public_origin, configuration)
    }
}

fn synchronous_context() -> Result<()> {
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(input(
            "call synchronous project setup outside an async runtime",
        ));
    }
    Ok(())
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
    for capability in &manifest.provides {
        capability_effect(capability)?;
    }
    if manifest.rpc.len() > 64 {
        return Err(input("at most 64 RPC operations are supported"));
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
    if manifest.styles.len() + manifest.assets.len() > 64 {
        return Err(input("at most 64 style/asset inputs"));
    }
    if manifest
        .styles
        .iter()
        .any(|path| Path::new(path).extension().and_then(|e| e.to_str()) != Some("css"))
    {
        return Err(input("styles require CSS inputs"));
    }
    for path in manifest.styles.iter().chain(manifest.assets.values()) {
        resolve::relative_path(path)?;
        if !["css", "svg", "txt"].contains(
            &Path::new(path)
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or_default(),
        ) {
            return Err(input("W3 static inputs support UTF-8 CSS, SVG and text"));
        }
    }
    for (name, path) in &manifest.assets {
        if !resolve::identifier(name) || manifest.modules.values().any(|m| &m.path == path) {
            return Err(input("invalid asset name or module/asset collision"));
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
    let paths = manifest
        .modules
        .values()
        .map(|m| m.path.as_str())
        .chain(manifest.styles.iter().map(String::as_str))
        .chain(manifest.assets.values().map(String::as_str))
        .collect::<BTreeSet<_>>();
    if sources.keys().map(String::as_str).collect::<BTreeSet<_>>() != paths
        || sources.values().map(String::len).sum::<usize>() + manifest_source.len()
            > MAX_PROJECT_BYTES
    {
        return Err(Error::Limit("project sources"));
    }
    let modules = parse_modules(&manifest, &sources)?;
    let server = check_server(&manifest, &modules)?;
    let rpc = rpc::operations(&manifest, &modules, &server)?;
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
        .find(|component| root.symbols.get(&manifest.component) == Some(&component.name))
        .ok_or_else(|| input("root browser export is not a component"))?;
    let components = browser_names
        .iter()
        .flat_map(|n| modules[n].parsed.components.clone())
        .collect::<Vec<_>>();
    let mut browser = lower::lower(&browser_checked, &component.name, &components, &rpc)
        .map_err(|error| locate(&modules, error))?;
    let urls = assets::urls(&manifest, &sources)?;
    assets::resolve(&mut browser, &urls)?;
    Ok(CheckedProject {
        manifest_source,
        sources,
        manifest,
        modules,
        server,
        browser,
        browser_checked,
        rpc,
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
        if name != &manifest.server
            && config.target != Target::Shared
            && parsed
                .program
                .declarations
                .iter()
                .any(|d| !matches!(d, Declaration::Function(_)))
        {
            return Err(input(
                "W2 type/resource/policy declarations belong to the server entry module",
            ));
        }
        if config.target == Target::Shared
            && parsed.program.declarations.iter().any(|d| {
                !matches!(
                    d,
                    Declaration::Function(_)
                        | Declaration::Alias(..)
                        | Declaration::Record(_)
                        | Declaration::Enum(_)
                )
            })
        {
            return Err(input("shared modules contain pure types/functions only"));
        }
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
        > 64
    {
        return Err(input("at most 64 browser components"));
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
        .map(|name| capability_effect(name))
        .collect::<Result<Effects>>()?;
    let mut storage = manifest.storage.clone();
    if let Some(storage) = &mut storage {
        for join in storage.joins.values_mut() {
            let (module, name) = join.output.split_once('.').map_or(
                (manifest.server.as_str(), join.output.as_str()),
                |(m, n)| (m, n),
            );
            if !names.contains(module) {
                return Err(input("join output module is outside the server graph"));
            }
            join.output = modules[module]
                .symbols
                .get(name)
                .cloned()
                .or_else(|| {
                    modules[module]
                        .parsed
                        .program
                        .declarations
                        .iter()
                        .find_map(|d| match d {
                            Declaration::Record(r) if r.name == name => Some(r.name.clone()),
                            _ => None,
                        })
                })
                .ok_or_else(|| input("unknown source-level join output type"))?;
        }
    }
    let server = crate::checker::check_project(&source, grants, storage.as_ref())
        .map_err(|error| locate(modules, error))?;
    if server.grants.iter().any(|effect| match effect {
        Effect::Read(name) | Effect::Write(name) => !server.resources.contains_key(name),
        Effect::Host(_) => false,
    }) {
        return Err(input("capability refers to an undeclared resource"));
    }
    for name in &names {
        let module = &modules[name];
        for declaration in &module.parsed.program.declarations {
            if let Declaration::Function(function) = declaration {
                let checked = &server.functions[&function.name];
                let allowed = module
                    .config
                    .provides
                    .iter()
                    .map(|name| capability_effect(name))
                    .collect::<Result<Effects>>()?;
                if !checked.effects.is_subset(&allowed) {
                    return Err(source_error(
                        &module.config.path,
                        input("function effects exceed its module budget"),
                    ));
                }
            }
        }
    }
    Ok(server)
}

fn capability_effect(name: &str) -> Result<Effect> {
    if name == "PostgresRead" {
        return Ok(Effect::Host(name.into()));
    }
    for (prefix, write) in [("DbRead<", false), ("DbWrite<", true)] {
        if let Some(resource) = name.strip_prefix(prefix).and_then(|v| v.strip_suffix('>'))
            && resolve::identifier(resource)
        {
            return Ok(if write {
                Effect::Write(resource.into())
            } else {
                Effect::Read(resource.into())
            });
        }
    }
    Err(input("unknown application capability"))
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
