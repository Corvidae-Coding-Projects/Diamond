//! Explicit exported-operation contracts and checked scalar dispatch metadata.

use super::{Manifest, Module, Target, input, resolve, wire};
use crate::error::Result;
use crate::hir::CheckedProgram;
use crate::value::Type;
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub(super) struct Operation {
    pub action: bool,
    pub durable: bool,
    pub function: String,
    pub arguments: Vec<Type>,
    pub result: Type,
    pub client_result: Type,
}

#[derive(Debug, Serialize)]
pub(super) struct Contract {
    action: bool,
    submission: Option<&'static str>,
    arguments: Vec<String>,
    result: String,
    effects: Vec<String>,
    authenticated: bool,
    replay: &'static str,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BindingKind {
    Rpc,
    Action,
    Api,
}

pub(super) fn operations(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
) -> Result<BTreeMap<String, Operation>> {
    bindings(manifest, modules, program, BindingKind::Rpc)
}

pub(super) fn action_operations(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
) -> Result<BTreeMap<String, Operation>> {
    let mut proxy = manifest.clone();
    proxy.rpc = manifest
        .actions
        .iter()
        .map(|(name, binding)| (name.clone(), binding.function.clone()))
        .collect();
    bindings(&proxy, modules, program, BindingKind::Action)
}

pub(super) fn api_operations(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
) -> Result<BTreeMap<String, Operation>> {
    bindings(manifest, modules, program, BindingKind::Api)
}

fn bindings(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
    mode: BindingKind,
) -> Result<BTreeMap<String, Operation>> {
    manifest
        .rpc
        .iter()
        .map(|(name, binding)| {
            if !resolve::identifier(name) {
                return Err(input("invalid RPC operation name"));
            }
            let (module, function) = binding
                .split_once('.')
                .ok_or_else(|| input("RPC binding must be module.function"))?;
            let module = modules
                .get(module)
                .ok_or_else(|| input("unknown RPC module"))?;
            if module.config.target != Target::Server || !module.parsed.exports.contains(function) {
                return Err(input("RPC requires an explicitly exported server function"));
            }
            let function = module
                .symbols
                .get(function)
                .ok_or_else(|| input("RPC export must be a function"))?;
            let checked = program
                .functions
                .get(function)
                .ok_or_else(|| input("RPC function must be reachable from the server entry"))?;
            let action = super::actions::declared(modules, function);
            let durable = action
                && manifest
                    .actions
                    .values()
                    .any(|action| action.function == *binding && action.durable());
            if mode == BindingKind::Rpc && action || mode == BindingKind::Action && !action {
                return Err(input("action declarations require actions bindings and cannot use ordinary RPC"));
            }
            let result = if let Type::Result(inner) = &checked.result {
                inner.as_ref()
            } else {
                &checked.result
            };
            if !wire::safe_type(result, program)
                || checked
                    .parameters
                    .iter()
                    .any(|(_, ty)| !wire::safe_type(ty, program))
            {
                return Err(input(
                    "RPC signatures require finite managed wire-safe types",
                ));
            }
            if checked
                .effects
                .iter()
                    .any(|effect| matches!(effect, crate::authority::Effect::Host(name) if !name.starts_with("Http<") && !name.starts_with("GitRead<") && !name.starts_with("GitWrite<") && !name.starts_with("GitReview<") && !name.starts_with("GitAdmin<") && !name.starts_with("DeliveryRead<") && !name.starts_with("DeliveryWrite<") && !name.starts_with("OpsRead<") && !name.starts_with("OpsAdmin<") && !name.starts_with("Mail<") && !["Jobs","Events","JobKey","Workflows"].contains(&name.as_str())))
            {
                return Err(input("RPC does not dispatch this native host operation"));
            }
            Ok((
                name.clone(),
                Operation {
                    action,
                    durable,
                    function: function.clone(),
                    arguments: checked
                        .parameters
                        .iter()
                        .map(|(_, ty)| ty.clone())
                        .collect(),
                    result: result.clone(),
                    client_result: client_result(result, action)?,
                },
            ))
        })
        .collect()
}

fn client_result(result: &Type, action: bool) -> Result<Type> {
    if !action {
        return Ok(result.clone());
    }
    let Type::AppResult(value, error) = result else {
        return Err(input(
            "actions require Result<Value, declared application enum>",
        ));
    };
    Ok(Type::Action(value.clone(), error.clone()))
}

pub(super) fn contracts(
    operations: &BTreeMap<String, Operation>,
    program: &CheckedProgram,
    public: &std::collections::BTreeSet<String>,
) -> BTreeMap<String, Contract> {
    operations
        .iter()
        .map(|(name, op)| {
            (
                name.clone(),
                Contract {
                    action: op.action,
                    submission: op.action.then_some(if op.durable {
                        "runtime-issued durable invocation; prepare then execute/status/ack; the checked mutation and sealed receipt commit atomically"
                    } else {
                        "runtime-issued single-use proof; prepare then execute; duplicate/expired/uncertain proofs never execute and return ActionUnknown; no result recovery"
                    }),
                    arguments: op.arguments.iter().map(ToString::to_string).collect(),
                    result: op.client_result.to_string(),
                    effects: program.functions[&op.function]
                        .effects
                        .iter()
                        .map(|v| format!("{v:?}"))
                        .collect(),
                    authenticated: !public.contains(name),
                    replay: if op.durable {
                        "transactional reconciliation only; uncertain delivery uses the same invocation status and never repeats the mutation"
                    } else {
                        "never automatic; failed mutation delivery may have an unknown outcome"
                    },
                },
            )
        })
        .collect()
}
