//! Declarative, adapter-owned secret consumption inventory.

use super::{CheckedProject, Manifest, input};
use crate::error::Result;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SecretUse {
    pub(super) adapter: String,
    pub(super) binding: String,
    pub(super) kind: String,
    pub(super) audience: String,
}

pub(super) fn check(manifest: &Manifest) -> Result<()> {
    if manifest.secrets.len() > 32 {
        return Err(input("at most 32 declarative secret bindings"));
    }
    for (name, secret) in &manifest.secrets {
        if !super::resolve::identifier(name)
            || !super::resolve::identifier(&secret.binding)
            || secret.audience != secret.binding
        {
            return Err(input("invalid secret name, binding, or audience"));
        }
        match (secret.adapter.as_str(), secret.kind.as_str()) {
            ("http", "bearer")
                if manifest
                    .provides
                    .contains(&format!("Http<{}>", secret.binding)) => {}
            ("oauth", "oauth_client")
                if manifest
                    .identity
                    .as_ref()
                    .and_then(|identity| identity.oauth.as_ref())
                    .is_some_and(|oauth| {
                        oauth
                            .providers
                            .iter()
                            .any(|provider| provider.name() == secret.binding)
                    }) => {}
            _ => {
                return Err(input(
                    "secret adapter, kind, audience, and declared capability/provider must agree",
                ));
            }
        }
    }
    let mut consumers = manifest
        .secrets
        .values()
        .map(|secret| (secret.adapter.as_str(), secret.binding.as_str()))
        .collect::<Vec<_>>();
    consumers.sort_unstable();
    consumers.dedup();
    if consumers.len() != manifest.secrets.len() {
        return Err(input(
            "one secret declaration is allowed per adapter binding",
        ));
    }
    Ok(())
}

pub(super) fn validate_runtime(
    project: &CheckedProject,
    runtime: &crate::RuntimeGrants,
) -> Result<()> {
    for secret in project.manifest.secrets.values() {
        let present = match secret.adapter.as_str() {
            "http" => runtime.has_http_credential(&secret.binding),
            "oauth" => runtime.has_oauth_client(&secret.binding),
            _ => false,
        };
        if !present {
            return Err(crate::Error::Denied);
        }
    }
    Ok(())
}

pub(super) fn contracts(bindings: &BTreeMap<String, SecretUse>) -> serde_json::Value {
    serde_json::json!({
        "bindings":bindings,
        "injection":"native adapter only; source receives no secret value or handle",
        "rotation":"new runtime configuration captures a new opaque generation",
        "diagnostics":"values and deployment environment names are excluded"
    })
}
