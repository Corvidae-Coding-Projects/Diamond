//! Explicit versioned external operation bindings; never reflect server functions.
use super::{Manifest, Module, input, rpc};
use crate::{authority::Effect, error::Result, hir::CheckedProgram};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    pub version: u32,
    pub operations: BTreeMap<String, Binding>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub function: String,
    pub auth: Auth,
    #[serde(default)]
    pub allow_unscoped: bool,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(super) enum Auth {
    Public,
    Token,
}
#[derive(Clone, Debug)]
pub(super) struct Operation {
    pub rpc: rpc::Operation,
    pub auth: Auth,
    pub allow_unscoped: bool,
    pub scope_tags: BTreeSet<String>,
}
pub(super) fn operations(
    m: &Manifest,
    modules: &BTreeMap<String, Module>,
    p: &CheckedProgram,
) -> Result<BTreeMap<String, Operation>> {
    let Some(config) = &m.http_api else {
        return Ok(BTreeMap::new());
    };
    if config.version != 1 || config.operations.is_empty() || config.operations.len() > 128 {
        return Err(input("HTTP API requires version 1 and 1..128 operations"));
    }
    let mut proxy = m.clone();
    proxy.rpc = config
        .operations
        .iter()
        .map(|(k, v)| (k.clone(), v.function.clone()))
        .collect();
    rpc::api_operations(&proxy, modules, p)?
        .into_iter()
        .map(|(name, rpc)| {
            if name == "_contract" {
                return Err(input("_contract is reserved for API metadata"));
            }
            let binding = &config.operations[&name];
            if rpc.action && binding.auth != Auth::Token {
                return Err(input("API actions require token authentication"));
            }
            let mut tags = BTreeSet::new();
            let mut effects = p.functions[&rpc.function].effects.clone();
            if let Some(binding) = m.actions.values().find(|binding| {
                binding.durable()
                    && binding
                        .function
                        .split_once('.')
                        .and_then(|(module, name)| modules.get(module)?.symbols.get(name))
                        == Some(&rpc.function)
            }) && let Some(resource) = &binding.resource
            {
                effects.insert(Effect::Read(resource.clone()));
            }
            for effect in &effects {
                if binding.auth == Auth::Public && !matches!(effect, Effect::Read(_)) {
                    return Err(input("public API operations must be read-only"));
                }
                if let Effect::Read(r) | Effect::Write(r) = effect {
                    let storage = p
                        .storage
                        .as_ref()
                        .ok_or_else(|| input("API requires relational storage"))?;
                    let resource = storage
                        .resources
                        .get(r)
                        .ok_or_else(|| input("API resource is unknown"))?;
                    if let Some(field) = &resource.scope {
                        let record = p
                            .resources
                            .get(r)
                            .and_then(|name| p.records.get(name))
                            .ok_or_else(|| input("API resource record is unknown"))?;
                        let crate::value::Type::Id(tag) = &record
                            .fields
                            .get(field)
                            .ok_or_else(|| input("API resource scope field is unknown"))?
                            .ty
                        else {
                            return Err(input("API resource scope must be a nominal Id"));
                        };
                        tags.insert(tag.clone());
                    } else if !binding.allow_unscoped {
                        return Err(input(
                            "API access to unscoped resources requires allow_unscoped=true",
                        ));
                    }
                }
            }
            Ok((
                name,
                Operation {
                    rpc,
                    auth: binding.auth,
                    allow_unscoped: binding.allow_unscoped,
                    scope_tags: tags,
                },
            ))
        })
        .collect()
}
pub(super) fn contract(ops: &BTreeMap<String, Operation>, p: &CheckedProgram) -> serde_json::Value {
    let operations=ops.iter().map(|(name,op)|(name,serde_json::json!({"arguments":op.rpc.arguments.iter().map(ToString::to_string).collect::<Vec<_>>(),"result":op.rpc.client_result.to_string(),"argument_types":op.rpc.arguments,"result_type":op.rpc.client_result,"action":op.rpc.action,"auth":op.auth,"scope_tags":op.scope_tags,"allow_unscoped":op.allow_unscoped}))).collect::<BTreeMap<_,_>>();
    let mut records = BTreeMap::new();
    let mut enums = BTreeMap::new();
    for op in ops.values() {
        for ty in op
            .rpc
            .arguments
            .iter()
            .chain(std::iter::once(&op.rpc.result))
        {
            definitions(ty, p, &mut records, &mut enums);
        }
    }
    serde_json::json!({"wire_version":super::WIRE_VERSION,"records":records,"enums":enums,"api_version":1,"operations":operations,"codecs":{"Int":"canonical signed decimal string, i64 range","Decimal":"{tag:Decimal,value:canonical scaled signed integer string}; scale 6","Timestamp":"{tag:Timestamp,value:canonical signed milliseconds since Unix epoch string}","Duration":"{tag:Duration,value:canonical signed milliseconds string}","Id":"{tag:declared nominal tag,value:ASCII string}","Option":"{tag:None} or {tag:Some,value:payload}","enum":"{tag:public variant name,fields:[payloads]}","Action":"{tag:ActionSucceeded|ActionRejected|ActionInvalid|ActionDenied|ActionAborted|ActionUnknown|ActionExpired|ActionConflict|ActionCorrupt,fields:[typed payload if applicable]}"},"errors":["authentication_required","denied","invalid_input","aborted","rate_limited","overloaded","deadline_exceeded","unavailable","outcome_unknown"],"overload":{"status":503,"code":"overloaded","retry_after":"seconds","safe_replay_header":"X-Diamond-Retry: safe","meaning":"rejected before application execution"},"replay":"durable operations use prepare/execute/status/ack reconciliation; ordinary database-only operations retry confirmed transaction conflicts with fresh authorization within configured request and node-wide budgets; clients may retry explicit safe overloads, never unknown outcomes or lost mutation replies"})
}

fn definitions(
    ty: &crate::value::Type,
    p: &CheckedProgram,
    records: &mut BTreeMap<String, BTreeMap<String, crate::value::Type>>,
    enums: &mut BTreeMap<String, BTreeMap<String, Vec<crate::value::Type>>>,
) {
    use crate::value::Type;
    match ty {
        Type::List(t) | Type::Option(t) | Type::Result(t) | Type::Success(t) => {
            definitions(t, p, records, enums);
        }
        Type::AppResult(t, e) => {
            definitions(t, p, records, enums);
            definitions(e, p, records, enums);
        }
        Type::Record(n) if !records.contains_key(n) => {
            let fields = p.records[n]
                .fields
                .iter()
                .map(|(k, f)| (k.clone(), f.ty.clone()))
                .collect::<BTreeMap<_, _>>();
            records.insert(n.clone(), fields.clone());
            for f in fields.values() {
                definitions(f, p, records, enums);
            }
        }
        Type::Enum(n) if !enums.contains_key(n) => {
            let fields = p.enums[n]
                .variants
                .iter()
                .map(|(k, fields)| {
                    (
                        super::resolve::public_name(k).to_owned(),
                        fields.iter().map(|(_, t)| t.clone()).collect::<Vec<_>>(),
                    )
                })
                .collect::<BTreeMap<_, _>>();
            enums.insert(n.clone(), fields.clone());
            for t in fields.values().flatten() {
                definitions(t, p, records, enums);
            }
        }
        _ => {}
    }
}
