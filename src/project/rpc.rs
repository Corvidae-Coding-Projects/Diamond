//! Explicit exported-operation contracts and checked scalar dispatch metadata.

use super::{Manifest, Module, Target, input, resolve, wire};
use crate::error::Result;
use crate::hir::CheckedProgram;
use crate::value::Type;
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Debug)]
pub(super) struct Operation {
    pub function: String,
    pub arguments: Vec<Type>,
    pub result: Type,
}

#[derive(Debug, Serialize)]
pub(super) struct Contract {
    arguments: Vec<String>,
    result: String,
    effects: Vec<String>,
    authenticated: bool,
    replay: &'static str,
}

pub(super) fn operations(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
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
                    .any(|effect| matches!(effect, crate::authority::Effect::Host(name) if !name.starts_with("Http<") && !name.starts_with("GitRead<") && !name.starts_with("GitWrite<") && !name.starts_with("GitReview<") && !name.starts_with("GitAdmin<") && !["Jobs","Events","JobKey"].contains(&name.as_str())))
            {
                return Err(input("RPC does not dispatch this native host operation"));
            }
            Ok((
                name.clone(),
                Operation {
                    function: function.clone(),
                    arguments: checked
                        .parameters
                        .iter()
                        .map(|(_, ty)| ty.clone())
                        .collect(),
                    result: result.clone(),
                },
            ))
        })
        .collect()
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
                    arguments: op.arguments.iter().map(ToString::to_string).collect(),
                    result: op.result.to_string(),
                    effects: program.functions[&op.function]
                        .effects
                        .iter()
                        .map(|v| format!("{v:?}"))
                        .collect(),
                    authenticated: !public.contains(name),
                    replay: "never automatic; failed mutation delivery may have an unknown outcome",
                },
            )
        })
        .collect()
}
