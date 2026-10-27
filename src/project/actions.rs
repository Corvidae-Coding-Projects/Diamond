//! Checked single-attempt and durable transactional action dispatch.

pub(super) mod durable;
mod eligibility;
pub(super) mod footprint;
mod receipts;
mod tickets;
pub(in crate::project) use tickets::Submission;

use super::{CheckedProject, Module, input, rpc, storage::PostgresStore, wire};
use crate::error::{Error, Result};
use crate::value::{Type, Value, ValueKind};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub function: String,
    #[serde(default = "transactional")]
    pub replay: String,
    pub resource: Option<String>,
    pub parent: Option<String>,
    pub label: Option<String>,
}

fn transactional() -> String {
    "transactional".into()
}

impl Binding {
    pub(super) fn durable(&self) -> bool {
        self.replay == "transactional"
    }
}

pub(super) fn schema(store: &mut PostgresStore, initialize: bool) -> Result<()> {
    tickets::schema(store, initialize)?;
    receipts::schema(store, initialize)
}

pub(super) fn checked(project: &CheckedProject) -> Result<()> {
    if !project.manifest.actions.is_empty()
        && (project.manifest.storage.is_none() || project.manifest.identity.is_none())
    {
        return Err(input(
            "LP2 actions require relational-v1 storage and accounts-v1 identity",
        ));
    }
    let declarations = project
        .modules
        .values()
        .flat_map(|m| m.parsed.actions.iter().cloned())
        .collect::<std::collections::BTreeSet<_>>();
    for function in project.server.functions.values() {
        check_body(&function.body, &declarations)?;
    }
    let mut bound = std::collections::BTreeSet::new();
    for (name, binding) in &project.manifest.actions {
        if !["never", "transactional"].contains(&binding.replay.as_str()) {
            return Err(input("action replay must be transactional or never"));
        }
        eligibility::checked(project, name, binding)?;
        let op = &project.rpc[name];
        if !matches!(op.result, Type::AppResult(..)) {
            return Err(input(
                "actions require Result<Value, declared application enum>",
            ));
        }
        if project.server.functions[&op.function]
            .effects
            .iter()
            .any(|effect| {
                !matches!(
                    effect,
                    crate::authority::Effect::Read(_) | crate::authority::Effect::Write(_)
                )
            })
        {
            return Err(input(
                "transactional and single-attempt actions support database effects only; external effects cannot gain replay",
            ));
        }
        if !bound.insert(op.function.clone()) {
            return Err(input(
                "each declared action function requires one unique actions binding; API operations may alias that binding",
            ));
        }
    }
    for module in project.modules.values() {
        for function in &module.parsed.actions {
            if !bound.contains(function) {
                return Err(input(
                    "each declared action needs an explicit actions binding",
                ));
            }
        }
    }
    Ok(())
}

fn check_body(
    body: &[crate::syntax::Statement],
    actions: &std::collections::BTreeSet<String>,
) -> Result<()> {
    for statement in body {
        match &statement.kind {
            crate::syntax::StatementKind::Let(_, value)
            | crate::syntax::StatementKind::Expression(value)
            | crate::syntax::StatementKind::Return(value)
            | crate::syntax::StatementKind::Redirect(value) => check_expression(value, actions)?,
            crate::syntax::StatementKind::Transaction(body) => check_body(body, actions)?,
        }
    }
    Ok(())
}

fn check_expression(
    expression: &crate::syntax::Expr,
    actions: &std::collections::BTreeSet<String>,
) -> Result<()> {
    use crate::syntax::ExprKind;
    match &expression.kind {
        ExprKind::Name(name) if actions.contains(name) => {
            return Err(Error::diagnostic(
                "D333",
                expression.location,
                "actions are dispatcher boundaries and cannot be called as ordinary source functions",
            ));
        }
        ExprKind::Unary(_, value) | ExprKind::Field(value, _) | ExprKind::Try(value) => {
            check_expression(value, actions)?;
        }
        ExprKind::Binary(a, _, b) => {
            check_expression(a, actions)?;
            check_expression(b, actions)?;
        }
        ExprKind::Call {
            callee, arguments, ..
        } => {
            check_expression(callee, actions)?;
            for (_, value) in arguments {
                check_expression(value, actions)?;
            }
        }
        ExprKind::List(values) => {
            for value in values {
                check_expression(value, actions)?;
            }
        }
        ExprKind::For(_, values, body) => {
            check_expression(values, actions)?;
            check_body(body, actions)?;
        }
        ExprKind::Match(value, arms) => {
            check_expression(value, actions)?;
            for arm in arms {
                check_body(&arm.body, actions)?;
            }
        }
        ExprKind::Html(nodes) => check_html(nodes, actions)?,
        _ => {}
    }
    Ok(())
}

fn check_html(
    nodes: &[crate::syntax::HtmlNode],
    actions: &std::collections::BTreeSet<String>,
) -> Result<()> {
    for node in nodes {
        match node {
            crate::syntax::HtmlNode::Expression(expression) => {
                check_expression(expression, actions)?;
            }
            crate::syntax::HtmlNode::Element {
                attributes,
                children,
                ..
            } => {
                for (_, attribute) in attributes {
                    if let crate::syntax::HtmlAttribute::Expression(expression) = attribute {
                        check_expression(expression, actions)?;
                    }
                }
                check_html(children, actions)?;
            }
            crate::syntax::HtmlNode::Text(_) => {}
        }
    }
    Ok(())
}

pub(super) fn declared(modules: &BTreeMap<String, Module>, function: &str) -> bool {
    modules
        .values()
        .any(|module| module.parsed.actions.contains(function))
}

pub(super) fn finish(
    store: &mut PostgresStore,
    result: Result<Value>,
) -> Result<serde_json::Value> {
    let result = result.and_then(|value| match value.0 {
        ValueKind::AppErr(error) => Err(Error::Application(*error)),
        _ => Ok(value),
    });
    // A caught policy/validation failure still poisons the enclosing action.
    let result = match store.action_failure() {
        Some(error) => Err(error),
        None => result,
    };
    let outcome = match store.finish_request(result) {
        Ok(Value(ValueKind::Ok(value))) => crate::action::value("ActionSucceeded", vec![*value]),
        Ok(_) => return Err(input("action did not return its checked Result shape")),
        Err(Error::Application(error)) => crate::action::value("ActionRejected", vec![error]),
        Err(Error::Denied | Error::Unauthenticated) => crate::action::value("ActionDenied", vec![]),
        Err(Error::Input(_)) => crate::action::value("ActionInvalid", vec![]),
        Err(Error::Aborted | Error::Conflict) => crate::action::value("ActionAborted", vec![]),
        Err(_) => crate::action::value("ActionUnknown", vec![]),
    };
    wire::encode(&outcome)
}

pub(super) fn path<'a>(
    path: &'a str,
    operations: &BTreeMap<String, rpc::Operation>,
) -> Option<&'a str> {
    endpoint(path, operations).map(|endpoint| endpoint.name)
}

#[derive(Clone, Copy, Debug)]
pub(super) struct Endpoint<'a> {
    pub name: &'a str,
    pub control: durable::Control,
}

pub(super) fn split(path: &str) -> (&str, durable::Control) {
    for (suffix, control) in [
        ("/prepare", durable::Control::Prepare),
        ("/status", durable::Control::Status),
        ("/ack", durable::Control::Ack),
    ] {
        if let Some(name) = path.strip_suffix(suffix) {
            return (name, control);
        }
    }
    (path, durable::Control::Execute)
}

pub(super) fn endpoint<'a>(
    path: &'a str,
    operations: &BTreeMap<String, rpc::Operation>,
) -> Option<Endpoint<'a>> {
    let mut control = durable::Control::Execute;
    let (name, action) = if let Some(name) = path.strip_prefix("/_diamond/actions/") {
        let (operation, mode) = split(name);
        control = mode;
        (operation, true)
    } else {
        (path.strip_prefix("/_diamond/rpc/")?, false)
    };
    operations
        .get(name)
        .filter(|op| op.action == action)
        .map(|_| Endpoint { name, control })
}
