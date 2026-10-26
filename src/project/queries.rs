//! Checked, single-plan graph queries over policy-aware resources.

use super::{Manifest, Module, input, rpc};
use crate::{
    authority::Effect,
    error::{Error, Result},
    hir::CheckedProgram,
    syntax::{Expr, ExprKind, StatementKind},
    value::{Type, Value, ValueKind},
};
use serde::{Deserialize, Serialize};
use serde_json::Value as Json;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    pub(super) resources: BTreeSet<String>,
    pub(super) selection: BTreeSet<String>,
    pub(super) cursor_argument: String,
    pub(super) limit_argument: String,
    pub(super) max_depth: usize,
    pub(super) max_nodes: usize,
    pub(super) max_rows: i64,
    pub(super) max_cost: usize,
    pub(super) max_queries: usize,
    pub(super) timeout_ms: u64,
    pub(super) max_response_bytes: usize,
}

#[derive(Clone, Debug)]
pub(super) struct Plan {
    config: Config,
    cursor: usize,
    limit: usize,
}

type QueryCall<'a> = (&'a str, &'a str, &'a [(Option<String>, Expr)]);

fn query_call(expression: &Expr) -> Option<QueryCall<'_>> {
    let expression = if let ExprKind::Try(inner) = &expression.kind {
        inner.as_ref()
    } else {
        expression
    };
    let ExprKind::Call {
        callee,
        arguments,
        types,
    } = &expression.kind
    else {
        return None;
    };
    if !types.is_empty() {
        return None;
    }
    let ExprKind::Field(base, method) = &callee.kind else {
        return None;
    };
    let ExprKind::Name(repository) = &base.kind else {
        return None;
    };
    Some((repository, method, arguments))
}

fn wrapper_is_declarative(
    program: &CheckedProgram,
    operation: &rpc::Operation,
    config: &Config,
) -> bool {
    let function = &program.functions[&operation.function];
    let Some(StatementKind::Expression(expression) | StatementKind::Return(expression)) =
        function.body.first().map(|statement| &statement.kind)
    else {
        return false;
    };
    if function.body.len() != 1 {
        return false;
    }
    let Some((repository, method, arguments)) = query_call(expression) else {
        return false;
    };
    let resource = program
        .resources
        .keys()
        .find(|resource| resource.to_ascii_lowercase() == repository);
    let Some(resource) = resource else {
        return false;
    };
    if !matches!(method, "page" | "join") || !config.resources.contains(resource) {
        return false;
    }
    let parameter = |configured: &str, argument: &str| {
        arguments
            .iter()
            .find_map(|(name, value)| (name.as_deref() == Some(argument)).then_some(value))
            .is_some_and(|value| matches!(&value.kind, ExprKind::Name(name) if name == configured))
    };
    parameter(&config.limit_argument, "limit") && parameter(&config.cursor_argument, "after")
}

fn leaves(
    ty: &Type,
    program: &CheckedProgram,
    prefix: &str,
    depth: usize,
    seen: &mut BTreeSet<String>,
    output: &mut BTreeSet<String>,
) -> Result<usize> {
    match ty {
        Type::List(inner) | Type::Option(inner) => {
            leaves(inner, program, prefix, depth, seen, output)
        }
        Type::Record(name) => {
            if !seen.insert(name.clone()) {
                return Err(input(
                    "query result type cannot contain a recursive record graph",
                ));
            }
            let mut maximum = depth;
            for (field, definition) in &program.records[name].fields {
                let path = if prefix.is_empty() {
                    field.clone()
                } else {
                    format!("{prefix}.{field}")
                };
                maximum = maximum.max(leaves(
                    &definition.ty,
                    program,
                    &path,
                    depth + 1,
                    seen,
                    output,
                )?);
            }
            seen.remove(name);
            Ok(maximum)
        }
        Type::Enum(_) | Type::Result(_) | Type::AppResult(..) | Type::Success(_) => Err(input(
            "query result graph supports records, lists, options, and scalar leaves",
        )),
        _ => {
            if prefix.is_empty() || !crate::project::wire::safe_type(ty, program) {
                return Err(input(
                    "query selection must end in a wire-safe scalar field",
                ));
            }
            output.insert(prefix.into());
            Ok(depth)
        }
    }
}

pub(super) fn checked(
    manifest: &Manifest,
    modules: &BTreeMap<String, Module>,
    program: &CheckedProgram,
    operations: &BTreeMap<String, rpc::Operation>,
) -> Result<BTreeMap<String, Plan>> {
    if manifest.queries.len() > 64 {
        return Err(input("at most 64 checked query documents"));
    }
    let plans: BTreeMap<String, Plan> = manifest.queries.iter().map(|(name, config)| {
        let operation=operations.get(name).ok_or_else(|| input("query document requires a same-named RPC operation"))?;
        let function=&program.functions[&operation.function];
        let module_export=manifest.rpc.get(name).and_then(|binding| binding.split_once('.')).and_then(|(module,function)|modules.get(module).map(|module|(module,function))).is_some_and(|(module,function)|module.parsed.exports.contains(function));
        let reads=function.effects.iter().filter_map(|effect|match effect{Effect::Read(resource)=>Some(resource.clone()),_=>None}).collect::<BTreeSet<_>>();
        if !module_export || function.effects.len()!=reads.len() || reads!=config.resources || config.resources.is_empty() || !wrapper_is_declarative(program,operation,config) {
            return Err(input("query must be an exported, same-named, single repository page/join wrapper with exact read resources"));
        }
        let cursor=function.parameters.iter().position(|(parameter,ty)|parameter==&config.cursor_argument && Type::Text.accepts(ty)).ok_or_else(||input("query cursor argument must be Text"))?;
        let limit=function.parameters.iter().position(|(parameter,ty)|parameter==&config.limit_argument && *ty==Type::Int).ok_or_else(||input("query limit argument must be Int"))?;
        let Type::List(row)=&operation.result else{return Err(input("query result must be a list of checked records"));};
        let mut selection=BTreeSet::new();
        let depth=leaves(row,program,"",0,&mut BTreeSet::new(),&mut selection)?;
        let nodes=usize::try_from(config.max_rows).ok().and_then(|rows|rows.checked_mul(selection.len()+1)).ok_or(Error::Denied)?;
        let cost=nodes.checked_mul(depth.max(1)).ok_or(Error::Denied)?;
        if selection!=config.selection || !(1..=8).contains(&config.max_depth) || depth>config.max_depth || !(1..=10_000).contains(&config.max_nodes) || nodes>config.max_nodes || !(1..=100).contains(&config.max_rows) || !(1..=100_000).contains(&config.max_cost) || cost>config.max_cost || config.max_queries!=1 || config.timeout_ms!=5_000 || !(1..=1_048_576).contains(&config.max_response_bytes) {
            return Err(input("query selection or depth/node/row/cost/query/time/response budget is invalid"));
        }
        Ok((name.clone(),Plan{config:config.clone(),cursor,limit}))
    }).collect::<Result<_>>()?;
    let functions = plans
        .keys()
        .map(|name| operations[name].function.as_str())
        .collect::<BTreeSet<_>>();
    if functions.len() != plans.len() {
        return Err(input(
            "each query document requires a distinct resolver function",
        ));
    }
    Ok(plans)
}

impl Plan {
    pub(super) fn check_arguments(&self, arguments: &[Value]) -> Result<i64> {
        let cursor = arguments
            .get(self.cursor)
            .and_then(|value| match &value.0 {
                ValueKind::Text(value) => Some(value),
                _ => None,
            })
            .ok_or(Error::Denied)?;
        let limit = arguments
            .get(self.limit)
            .and_then(|value| match value.0 {
                ValueKind::Int(value) => Some(value),
                _ => None,
            })
            .ok_or(Error::Denied)?;
        if cursor.len() > 512 || !(1..=self.config.max_rows).contains(&limit) {
            return Err(input("query cursor or row limit exceeds its document"));
        }
        Ok(limit)
    }

    pub(super) fn check_result(&self, value: &Json, requested: i64) -> Result<()> {
        let rows = value
            .as_array()
            .filter(|rows| rows.len() <= usize::try_from(requested).unwrap_or(0))
            .ok_or(Error::Limit("query rows"))?;
        let (nodes, depth) = measure(value, 0)?;
        let bytes = serde_json::to_vec(value).map_err(|_| Error::Denied)?.len();
        if rows.len() > usize::try_from(self.config.max_rows).unwrap_or(0)
            || nodes > self.config.max_nodes
            || depth > self.config.max_depth
            || bytes > self.config.max_response_bytes
        {
            return Err(Error::Limit("query result budget"));
        }
        Ok(())
    }

    pub(super) const fn max_queries(&self) -> usize {
        self.config.max_queries
    }
}

fn measure(value: &Json, depth: usize) -> Result<(usize, usize)> {
    if depth > 8 {
        return Err(Error::Limit("query depth"));
    }
    let children: Vec<&Json> = match value {
        Json::Array(values) => values.iter().collect(),
        Json::Object(values) => values.values().collect(),
        _ => Vec::new(),
    };
    let mut nodes = 1usize;
    let mut maximum = depth;
    for child in children {
        let (count, child_depth) = measure(child, depth + 1)?;
        nodes = nodes
            .checked_add(count)
            .ok_or(Error::Limit("query nodes"))?;
        maximum = maximum.max(child_depth);
    }
    Ok((nodes, maximum.saturating_sub(1)))
}

pub(super) fn contracts(plans: &BTreeMap<String, Plan>) -> serde_json::Value {
    serde_json::to_value(
        plans
            .iter()
            .map(|(name, plan)| (name, &plan.config))
            .collect::<BTreeMap<_, _>>(),
    )
    .unwrap_or(Json::Null)
}
