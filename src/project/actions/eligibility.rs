//! The durable profile is finite CRUD with automatically captured read dependencies.
use super::{Binding, CheckedProject, input};
use crate::{
    authority::Effect,
    error::{Error, Result},
    syntax::{Expr, ExprKind, Statement, StatementKind},
    value::Type,
};
use std::collections::BTreeSet;

pub(super) fn checked(project: &CheckedProject, name: &str, binding: &Binding) -> Result<()> {
    if binding
        .label
        .as_ref()
        .is_some_and(|label| label.trim().is_empty() || label.chars().count() > 80)
    {
        return Err(input("action label requires 1..80 nonblank Text scalars"));
    }
    if !binding.durable() {
        if binding.resource.is_some() || binding.parent.is_some() {
            return Err(input(
                "action parent/resource requires transactional replay",
            ));
        }
        return Ok(());
    }
    let resource = binding
        .resource
        .as_ref()
        .ok_or_else(|| input("transactional actions require a parent resource and parameter"))?;
    let parent = binding
        .parent
        .as_ref()
        .ok_or_else(|| input("transactional actions require a parent resource and parameter"))?;
    let record = project
        .server
        .resources
        .get(resource)
        .and_then(|name| project.server.records.get(name))
        .ok_or_else(|| input("transactional action parent resource is unknown"))?;
    let function = &project.server.functions[&project.rpc[name].function];
    if !function.parameters.iter().any(|(name, ty)| {
        name == parent && matches!(ty, Type::Id(_)) && ty == &record.fields["id"].ty
    }) {
        return Err(input(
            "transactional action parent parameter must match its resource nominal ID",
        ));
    }
    if !project
        .server
        .grants
        .contains(&Effect::Read(resource.clone()))
        || function.effects.iter().any(|effect| match effect {
            Effect::Read(resource) | Effect::Write(resource) => !project
                .server
                .grants
                .contains(&Effect::Read(resource.clone())),
            Effect::Host(_) => true,
        })
    {
        return Err(input(
            "transactional actions require declared read authority for parent and CRUD dependencies, and no external effects",
        ));
    }
    let mut visited = BTreeSet::new();
    visit_function(project, &project.rpc[name].function, &mut visited)
}
fn visit_function(
    project: &CheckedProject,
    name: &str,
    visited: &mut BTreeSet<String>,
) -> Result<()> {
    if !visited.insert(name.into()) {
        return Ok(());
    }
    visit_body(project, &project.server.functions[name].body, visited)
}
fn visit_body(
    project: &CheckedProject,
    body: &[Statement],
    visited: &mut BTreeSet<String>,
) -> Result<()> {
    for statement in body {
        match &statement.kind {
            StatementKind::Let(_, value)
            | StatementKind::Expression(value)
            | StatementKind::Return(value)
            | StatementKind::Redirect(value) => visit(project, value, visited)?,
            StatementKind::Transaction(body) => visit_body(project, body, visited)?,
        }
    }
    Ok(())
}
fn visit(
    project: &CheckedProject,
    expression: &Expr,
    visited: &mut BTreeSet<String>,
) -> Result<()> {
    match &expression.kind {
        ExprKind::Call {
            callee, arguments, ..
        } => {
            if let ExprKind::Field(base, method) = &callee.kind
                && let ExprKind::Name(name) = &base.kind
                && project
                    .server
                    .resources
                    .keys()
                    .any(|resource| resource.to_ascii_lowercase() == *name)
                && !["get", "create", "update", "delete"].contains(&method.as_str())
            {
                return Err(Error::diagnostic(
                    "D334",
                    expression.location,
                    "transactional actions support bounded CRUD dependencies; set and aggregate queries are not eligible",
                ));
            }
            if let ExprKind::Name(name) = &callee.kind
                && project.server.functions.contains_key(name)
            {
                visit_function(project, name, visited)?;
            }
            for (_, value) in arguments {
                visit(project, value, visited)?;
            }
        }
        ExprKind::Unary(_, value) | ExprKind::Field(value, _) | ExprKind::Try(value) => {
            visit(project, value, visited)?;
        }
        ExprKind::Binary(a, _, b) => {
            visit(project, a, visited)?;
            visit(project, b, visited)?;
        }
        ExprKind::List(values) => {
            for value in values {
                visit(project, value, visited)?;
            }
        }
        ExprKind::For(_, values, body) => {
            visit(project, values, visited)?;
            visit_body(project, body, visited)?;
        }
        ExprKind::Match(value, arms) => {
            visit(project, value, visited)?;
            for arm in arms {
                visit_body(project, &arm.body, visited)?;
            }
        }
        _ => {}
    }
    Ok(())
}
