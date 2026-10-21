//! Deterministic project artifacts, bounded reads, and rechecked loading.

use super::{CheckedProject, FORMAT_VERSION, MAX_PROJECT_BYTES, compile_sources, input};
use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
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
    artifacts: BTreeMap<String, String>,
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
    let js = super::browser::emit(&project.browser)?;
    let asset = format!("assets/{}.js", hash(js.as_bytes()));
    let page = format!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{}</title></head><body><div id=\"diamond-app\"></div><p id=\"diamond-error\" role=\"alert\" hidden></p><script type=\"module\" src=\"/{asset}\"></script></body></html>\n",
        crate::template::escape(&project.manifest.name)
    );
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
        ("index.html".into(), page),
        (asset, js),
    ]);
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
        artifacts: files
            .iter()
            .map(|(path, content)| (path.clone(), hash(content.as_bytes())))
            .collect(),
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

pub(super) fn hash(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(64);
    let alphabet = b"0123456789abcdef";
    for byte in Sha256::digest(bytes) {
        output.push(char::from(alphabet[usize::from(byte >> 4)]));
        output.push(char::from(alphabet[usize::from(byte & 15)]));
    }
    output
}
