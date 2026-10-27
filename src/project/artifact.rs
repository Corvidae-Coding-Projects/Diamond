//! Deterministic project artifacts, bounded reads, and rechecked loading.

use super::{CheckedProject, FORMAT_VERSION, MAX_PROJECT_BYTES, compile_sources, input};
use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;

const MAX_ARTIFACT_BYTES: usize = 16 * crate::MAX_SOURCE_BYTES;

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ServerArtifact {
    format_version: u32,
    language_version: String,
    manifest: String,
    sources: BTreeMap<String, String>,
}

#[derive(Debug, Serialize)]
struct Contract<'a> {
    format_version: u32,
    language_version: &'static str,
    input_hash: String,
    modules: BTreeMap<&'a str, ModuleContract<'a>>,
    browser: &'a super::lower::BrowserProgram,
    host_operations: Vec<crate::host::OperationContract>,
    integration_operations: serde_json::Value,
    file_operations: serde_json::Value,
    private_files: Option<&'a super::files::Config>,
    git_operations: serde_json::Value,
    git: Option<&'a super::git::Config>,
    mail_operations: serde_json::Value,
    mail: Option<&'a super::mail::Config>,
    workflow_operations: serde_json::Value,
    workflows: &'a BTreeMap<String, super::workflows::Binding>,
    secret_inventory: serde_json::Value,
    queries: serde_json::Value,
    jobs: &'a BTreeMap<String, super::jobs::Binding>,
    events: &'a BTreeMap<String, super::events::Binding>,
    artifacts: BTreeMap<String, String>,
    rpc: BTreeMap<String, super::rpc::Contract>,
    rpc_id: String,
    wire_version: u32,
    http_api: Option<serde_json::Value>,
}

#[derive(Debug, Serialize)]
struct ModuleContract<'a> {
    target: super::Target,
    path: &'a str,
    exports: &'a std::collections::BTreeSet<String>,
    imports: &'a [String],
    provides: &'a std::collections::BTreeSet<String>,
}

pub(super) fn files(project: &CheckedProject) -> Result<BTreeMap<String, String>> {
    let mut js = super::browser::emit(&project.browser, &contract_id(project)?)?;
    let source_map = super::browser::source_map(project, &js)?;
    let map_asset = format!("assets/{}.js.map", hash(source_map.as_bytes()));
    write!(js, "\n//# sourceMappingURL=/{map_asset}\n").map_err(|_| input("browser formatting"))?;
    let asset = format!("assets/{}.js", hash(js.as_bytes()));
    let (static_files, style) = super::assets::files(project)?;
    let mut pages = BTreeMap::new();
    for route in &project.browser.routes {
        let content = project.render_page(route)?;
        let page = format!(
            "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{}</title><link rel=\"stylesheet\" href=\"/{style}\"></head><body><div id=\"diamond-app\" data-contract=\"{}\">{content}</div><aside id=\"diamond-recovery\" aria-label=\"Pending changes\" hidden></aside><p id=\"diamond-error\" role=\"alert\" hidden></p><script type=\"module\" src=\"/{asset}\"></script></body></html>\n",
            crate::template::escape(&project.manifest.name),
            contract_id(project)?
        );
        pages.insert(page_key(route), page);
    }
    let server = ServerArtifact {
        format_version: FORMAT_VERSION,
        language_version: env!("CARGO_PKG_VERSION").into(),
        manifest: project.manifest_source.clone(),
        sources: project.sources.clone(),
    };
    let encoded = serde_json::to_string_pretty(&server)
        .map_err(|_| input("server artifact serialization failed"))?
        + "\n";
    let mut files = BTreeMap::from([
        ("server.json".into(), encoded),
        (asset, js),
        (map_asset, source_map),
    ]);
    files.extend(static_files);
    files.extend(pages);
    let inputs = serde_json::to_vec(&(&project.manifest_source, &project.sources))
        .map_err(|_| input("input hashing failed"))?;
    let contract = Contract {
        format_version: FORMAT_VERSION,
        language_version: env!("CARGO_PKG_VERSION"),
        input_hash: hash(&inputs),
        modules: project
            .modules
            .iter()
            .map(|(name, module)| {
                (
                    name.as_str(),
                    ModuleContract {
                        target: module.config.target,
                        path: &module.config.path,
                        exports: &module.parsed.exports,
                        imports: &module.config.imports,
                        provides: &module.config.provides,
                    },
                )
            })
            .collect(),
        browser: &project.browser,
        host_operations: crate::host::contracts(),
        integration_operations: crate::integrations::contracts(),
        file_operations: super::files::contracts(),
        private_files: project.manifest.files.as_ref(),
        git_operations: super::git::contracts(),
        git: project.manifest.git.as_ref(),
        mail_operations: super::mail::contracts(),
        mail: project.manifest.mail.as_ref(),
        workflow_operations: super::workflows::contracts(),
        workflows: &project.manifest.workflows,
        secret_inventory: super::secrets::contracts(&project.manifest.secrets),
        queries: super::queries::contracts(&project.queries),
        jobs: &project.manifest.jobs,
        events: &project.manifest.events,
        artifacts: files
            .iter()
            .map(|(path, content)| (path.clone(), hash(content.as_bytes())))
            .collect(),
        rpc: super::rpc::contracts(&project.rpc, &project.server, &project.manifest.public_rpc),
        rpc_id: contract_id(project)?,
        wire_version: super::WIRE_VERSION,
        http_api: project
            .manifest
            .http_api
            .as_ref()
            .map(|_| super::api::contract(&project.api, &project.server)),
    };
    files.insert(
        "contract.json".into(),
        serde_json::to_string_pretty(&contract)
            .map_err(|_| input("contract serialization failed"))?
            + "\n",
    );
    if files
        .values()
        .any(|content| content.len() > MAX_ARTIFACT_BYTES)
    {
        return Err(Error::Limit("project artifact bytes"));
    }
    Ok(files)
}

pub(super) fn contract_id(project: &CheckedProject) -> Result<String> {
    let encoded = serde_json::to_vec(&(
        env!("CARGO_PKG_VERSION"),
        &project.manifest_source,
        &project.sources,
    ))
    .map_err(|_| input("RPC contract hashing failed"))?;
    Ok(hash(&encoded))
}

pub(super) fn write(project: &CheckedProject, output: &Path) -> Result<()> {
    let files = files(project)?;
    if output.exists() || output.symlink_metadata().is_ok() {
        return Err(input("build output already exists; choose a new directory"));
    }
    let parent = output
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)?;
    let name = output
        .file_name()
        .ok_or_else(|| input("build output needs a directory name"))?
        .to_string_lossy();
    let staging = parent.join(format!(
        ".{name}.diamond-{}",
        crate::authority::random_hex()?
    ));
    fs::create_dir(&staging)?;
    let result = (|| {
        for (path, content) in files {
            let path = staging.join(path);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
            file.write_all(content.as_bytes())?;
            file.sync_all()?;
        }
        if output.exists() {
            return Err(input("build output appeared during build"));
        }
        fs::rename(&staging, output)?;
        Ok(())
    })();
    if result.is_err() {
        fs::remove_dir_all(&staging)?;
    }
    result
}

#[derive(Clone, Debug)]
pub(super) struct PageShell {
    prefix: String,
    suffix: String,
}

impl PageShell {
    pub(super) fn from_files(
        project: &CheckedProject,
        files: &BTreeMap<String, String>,
    ) -> Result<Self> {
        let asset = files
            .keys()
            .find(|path| {
                path.starts_with("assets/")
                    && Path::new(path)
                        .extension()
                        .is_some_and(|extension| extension.eq_ignore_ascii_case("js"))
            })
            .ok_or_else(|| input("missing browser asset"))?;
        let (_, style) = super::assets::files(project)?;
        Ok(Self {
            prefix: format!(
                "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{}</title><link rel=\"stylesheet\" href=\"/{style}\"></head><body><div id=\"diamond-app\" data-contract=\"{}\">",
                crate::template::escape(&project.manifest.name),
                contract_id(project)?
            ),
            suffix: format!(
                "</div><aside id=\"diamond-recovery\" aria-label=\"Pending changes\" hidden></aside><p id=\"diamond-error\" role=\"alert\" hidden></p><script type=\"module\" src=\"/{asset}\"></script></body></html>\n"
            ),
        })
    }

    pub(super) fn render(&self, project: &CheckedProject, path: &str) -> Result<String> {
        let content = project.render_page(path)?;
        Ok(format!("{}{content}{}", self.prefix, self.suffix))
    }
}

pub(super) fn load(directory: &Path) -> Result<CheckedProject> {
    let server = read_text(&directory.join("server.json"), MAX_ARTIFACT_BYTES)?;
    let server: ServerArtifact =
        serde_json::from_str(&server).map_err(|_| input("invalid server artifact"))?;
    if server.format_version != FORMAT_VERSION
        || server.language_version != env!("CARGO_PKG_VERSION")
    {
        return Err(input(
            "incompatible server artifact format/language version",
        ));
    }
    if server.manifest.len() > crate::MAX_SOURCE_BYTES
        || server
            .sources
            .values()
            .any(|source| source.len() > crate::MAX_SOURCE_BYTES)
        || server.sources.values().map(String::len).sum::<usize>() > MAX_PROJECT_BYTES
    {
        return Err(Error::Limit("artifact source bytes"));
    }
    let project = compile_sources(server.manifest, server.sources)?;
    for (path, expected) in files(&project)? {
        if read_text(&directory.join(&path), MAX_ARTIFACT_BYTES)? != expected {
            return Err(input(
                "build artifact mismatch; rebuild from trusted project sources",
            ));
        }
    }
    Ok(project)
}

pub(super) fn read_text(path: &Path, maximum: usize) -> Result<String> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
    }
    let file = options.open(path)?;
    if !file.metadata()?.is_file() {
        return Err(input("project inputs must be regular files"));
    }
    let mut bytes = Vec::new();
    file.take((maximum + 1) as u64).read_to_end(&mut bytes)?;
    if bytes.len() > maximum {
        return Err(Error::Limit("project input bytes"));
    }
    String::from_utf8(bytes).map_err(|_| input("project inputs must be valid UTF-8"))
}

pub(crate) fn hash(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(64);
    let alphabet = b"0123456789abcdef";
    for byte in Sha256::digest(bytes) {
        output.push(char::from(alphabet[usize::from(byte >> 4)]));
        output.push(char::from(alphabet[usize::from(byte & 15)]));
    }
    output
}

pub(super) fn page_key(path: &str) -> String {
    if path == "/" {
        "index.html".into()
    } else {
        format!("pages/{}.html", hash(path.as_bytes()))
    }
}
pub(super) fn content_type(path: &str) -> &'static str {
    match Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "html" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "svg" => "image/svg+xml",
        "map" => "application/json; charset=utf-8",
        "js" => "text/javascript; charset=utf-8",
        _ => "text/plain; charset=utf-8",
    }
}
