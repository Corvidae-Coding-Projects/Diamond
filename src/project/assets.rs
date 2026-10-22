//! Explicit project assets and literal references; application text is never rewritten.
use super::{CheckedProject, Manifest, artifact, input, lower};
use crate::error::Result;
use std::collections::BTreeMap;
use std::path::Path;

pub(super) fn urls(
    manifest: &Manifest,
    sources: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, String>> {
    manifest
        .assets
        .iter()
        .map(|(name, path)| {
            let content = sources
                .get(path)
                .ok_or_else(|| input("missing asset source"))?;
            let ext = Path::new(path)
                .extension()
                .and_then(|e| e.to_str())
                .ok_or_else(|| input("asset extension"))?;
            Ok((
                name.clone(),
                format!("/assets/{}.{}", artifact::hash(content.as_bytes()), ext),
            ))
        })
        .collect()
}
pub(super) fn resolve(
    browser: &mut lower::BrowserProgram,
    urls: &BTreeMap<String, String>,
) -> Result<()> {
    for c in browser.components.values_mut() {
        references(&mut c.template, urls)?;
    }
    Ok(())
}
fn references(nodes: &mut [lower::Node], urls: &BTreeMap<String, String>) -> Result<()> {
    for node in nodes {
        match node {
            lower::Node::Element {
                attributes,
                children,
                ..
            } => {
                for (n, v) in attributes {
                    if n == "src" {
                        let lower::Attribute::Text(s) = v else {
                            return Err(input("asset reference must be static"));
                        };
                        *s = urls
                            .get(s.strip_prefix("asset:").ok_or_else(|| {
                                input("src requires an explicit asset:NAME reference")
                            })?)
                            .ok_or_else(|| input("unknown asset reference"))?
                            .clone();
                    }
                }
                references(children, urls)?;
            }
            lower::Node::When(_, children)
            | lower::Node::Each { children, .. }
            | lower::Node::Route(_, children) => references(children, urls)?,
            _ => {}
        }
    }
    Ok(())
}
fn css_references(css: &str, urls: &BTreeMap<String, String>) -> Result<String> {
    let mut out = String::new();
    let mut rest = css;
    while let Some(at) = rest.find("asset:") {
        out.push_str(&rest[..at]);
        rest = &rest[at + 6..];
        let length = rest
            .bytes()
            .take_while(|c| c.is_ascii_alphanumeric() || *c == b'_')
            .count();
        let name = &rest[..length];
        out.push_str(
            urls.get(name)
                .ok_or_else(|| input("unknown CSS asset reference"))?,
        );
        rest = &rest[length..];
    }
    out.push_str(rest);
    Ok(out)
}
pub(super) fn files(project: &CheckedProject) -> Result<(BTreeMap<String, String>, String)> {
    let urls = urls(&project.manifest, &project.sources)?;
    let mut files = project
        .manifest
        .assets
        .iter()
        .map(|(n, p)| {
            (
                urls[n].trim_start_matches('/').into(),
                project.sources[p].clone(),
            )
        })
        .collect::<BTreeMap<_, _>>();
    let mut css = String::from(
        "diamond-component,diamond-fragment{display:contents}\n[hidden]{display:none!important}\n",
    );
    for path in &project.manifest.styles {
        css.push_str(
            project
                .sources
                .get(path)
                .ok_or_else(|| input("missing stylesheet"))?,
        );
        css.push('\n');
    }
    let css = css_references(&css, &urls)?;
    let style = format!("assets/{}.css", artifact::hash(css.as_bytes()));
    files.insert(style.clone(), css);
    Ok((files, style))
}
