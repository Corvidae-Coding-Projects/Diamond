//! Deterministic module dependency and lexical symbol resolution.

use super::{Module, ModuleConfig, Target, input};
use crate::error::{Error, Result};
use crate::syntax::{
    Declaration, Expr, ExprKind, HtmlAttribute, HtmlNode, Pattern, Statement, StatementKind,
};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path};

pub(super) fn identifier(name: &str) -> bool {
    let mut bytes = name.bytes();
    bytes
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == b'_')
        && bytes.all(|c| c.is_ascii_alphanumeric() || c == b'_')
        && name.len() <= 128
        && !name.starts_with("__diamond_")
}

pub(super) fn relative_path(path: &str) -> Result<()> {
    if path.is_empty()
        || path.len() > 1024
        || path.contains('\\')
        || Path::new(path)
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(input(
            "module source paths must be relative and cannot contain parent/root components",
        ));
    }
    Ok(())
}

pub(super) fn order(configs: &BTreeMap<String, ModuleConfig>) -> Result<Vec<String>> {
    let mut done = BTreeSet::new();
    let mut active = BTreeSet::new();
    let mut order = Vec::new();
    for name in configs.keys() {
        visit(name, configs, &mut active, &mut done, &mut order)?;
    }
    Ok(order)
}

fn visit(
    name: &str,
    configs: &BTreeMap<String, ModuleConfig>,
    active: &mut BTreeSet<String>,
    done: &mut BTreeSet<String>,
    order: &mut Vec<String>,
) -> Result<()> {
    if done.contains(name) {
        return Ok(());
    }
    if !active.insert(name.into()) {
        return Err(input("module import cycle"));
    }
    let config = configs
        .get(name)
        .ok_or_else(|| input("import refers to an undeclared module"))?;
    let mut seen = BTreeSet::new();
    for imported in &config.imports {
        if !seen.insert(imported) {
            return Err(input("duplicate module import"));
        }
        let dependency = configs
            .get(imported)
            .ok_or_else(|| input("import refers to an undeclared module"))?;
        let allowed = match config.target {
            Target::Server => dependency.target != Target::Browser,
            Target::Browser => dependency.target != Target::Server,
            Target::Shared => dependency.target == Target::Shared,
        };
        if !allowed {
            return Err(input(
                "module import crosses a forbidden browser/server target boundary",
            ));
        }
        visit(imported, configs, active, done, order)?;
    }
    active.remove(name);
    done.insert(name.into());
    order.push(name.into());
    Ok(())
}

pub(super) fn reachable(root: &str, configs: &BTreeMap<String, ModuleConfig>) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    let mut queue = vec![root.to_owned()];
    while let Some(name) = queue.pop() {
        if found.insert(name.clone()) {
            queue.extend(configs[&name].imports.iter().cloned());
        }
    }
    found
}

pub(super) fn symbols(modules: &mut BTreeMap<String, Module>) -> Result<()> {
    let mut own = BTreeMap::new();
    for (name, module) in modules.iter() {
        let mut symbols = BTreeMap::new();
        for declaration in &module.parsed.program.declarations {
            if let Declaration::Function(function) = declaration {
                if !identifier(&function.name) {
                    return Err(input("invalid or reserved project function name"));
                }
                symbols.insert(
                    function.name.clone(),
                    format!("__diamond_fn_{}_{name}_{}", name.len(), function.name),
                );
            }
        }
        own.insert(name.clone(), symbols);
    }
    for (name, module) in modules.iter() {
        let mut visible = own[name].clone();
        for imported in &module.config.imports {
            for exported in &modules[imported].parsed.exports {
                let Some(symbol) = own[imported].get(exported) else {
                    return Err(input(
                        "importing browser components is outside the W1 subset",
                    ));
                };
                if visible.insert(exported.clone(), symbol.clone()).is_some() {
                    return Err(input("ambiguous imported/local declaration name"));
                }
            }
        }
        for exported in &module.parsed.exports {
            if !visible.contains_key(exported)
                && !module
                    .parsed
                    .components
                    .iter()
                    .any(|component| &component.name == exported)
            {
                return Err(input("export names an unknown declaration"));
            }
        }
    }
    let exports: BTreeMap<_, _> = modules
        .iter()
        .map(|(name, module)| (name.clone(), module.parsed.exports.clone()))
        .collect();
    for (name, module) in modules.iter_mut() {
        let mut visible = own[name].clone();
        for imported in &module.config.imports {
            for exported in &exports[imported] {
                if let Some(symbol) = own[imported].get(exported) {
                    visible.insert(exported.clone(), symbol.clone());
                }
            }
        }
        module.symbols = visible.clone();
        for declaration in &mut module.parsed.program.declarations {
            if let Declaration::Function(function) = declaration {
                let mut locals = BTreeSet::new();
                for (parameter, _) in &function.parameters {
                    local(parameter)?;
                    locals.insert(parameter.clone());
                }
                function.name = own[name][&function.name].clone();
                function.location.line += module.line_base;
                block(&mut function.body, &visible, &mut locals, module.line_base)?;
            }
        }
        components(module, &visible)?;
    }
    Ok(())
}

fn components(module: &mut Module, visible: &BTreeMap<String, String>) -> Result<()> {
    for component in &mut module.parsed.components {
        local(&component.name)?;
        component.location.line += module.line_base;
        let locals: BTreeSet<_> = component
            .states
            .iter()
            .map(|(name, _, _)| name.clone())
            .collect();
        if locals.len() != component.states.len() {
            return Err(input("duplicate component state"));
        }
        for (name, _, value) in &mut component.states {
            local(name)?;
            expression(value, visible, &BTreeSet::new(), module.line_base)?;
        }
        let events: BTreeSet<_> = component
            .events
            .iter()
            .map(|(name, _)| name.clone())
            .collect();
        if events.len() != component.events.len() || !events.is_disjoint(&locals) {
            return Err(input("duplicate state/event name"));
        }
        for (event, assignments) in &mut component.events {
            local(event)?;
            for (state, value) in assignments {
                local(state)?;
                expression(value, visible, &locals, module.line_base)?;
            }
        }
        html(&mut component.template, visible, &locals, module.line_base)?;
    }
    Ok(())
}

fn local(name: &str) -> Result<()> {
    if !identifier(name) || name == "host" {
        Err(input("invalid or reserved project local name"))
    } else {
        Ok(())
    }
}

fn block(
    statements: &mut [Statement],
    symbols: &BTreeMap<String, String>,
    locals: &mut BTreeSet<String>,
    base: usize,
) -> Result<()> {
    for statement in statements {
        statement.location.line += base;
        match &mut statement.kind {
            StatementKind::Let(name, value) => {
                expression(value, symbols, locals, base)?;
                local(name)?;
                locals.insert(name.clone());
            }
            StatementKind::Expression(value)
            | StatementKind::Return(value)
            | StatementKind::Redirect(value) => expression(value, symbols, locals, base)?,
            StatementKind::Transaction(body) => block(body, symbols, &mut locals.clone(), base)?,
        }
    }
    Ok(())
}

fn expression(
    expr: &mut Expr,
    symbols: &BTreeMap<String, String>,
    locals: &BTreeSet<String>,
    base: usize,
) -> Result<()> {
    expr.location.line += base;
    match &mut expr.kind {
        ExprKind::Name(name) => {
            if name.starts_with("__diamond_") {
                return Err(Error::diagnostic(
                    "D320",
                    expr.location,
                    "compiler-generated names are reserved and cannot be referenced by source",
                ));
            }
            if !locals.contains(name)
                && let Some(resolved) = symbols.get(name)
            {
                name.clone_from(resolved);
            }
        }
        ExprKind::Unary(_, value) | ExprKind::Try(value) | ExprKind::Field(value, _) => {
            expression(value, symbols, locals, base)?;
        }
        ExprKind::Binary(a, _, b) => {
            expression(a, symbols, locals, base)?;
            expression(b, symbols, locals, base)?;
        }
        ExprKind::Call {
            callee, arguments, ..
        } => {
            expression(callee, symbols, locals, base)?;
            for (_, argument) in arguments {
                expression(argument, symbols, locals, base)?;
            }
        }
        ExprKind::List(values) => {
            for value in values {
                expression(value, symbols, locals, base)?;
            }
        }
        ExprKind::Html(nodes) => html(nodes, symbols, locals, base)?,
        ExprKind::For(name, values, body) => {
            expression(values, symbols, locals, base)?;
            local(name)?;
            let mut nested = locals.clone();
            nested.insert(name.clone());
            block(body, symbols, &mut nested, base)?;
        }
        ExprKind::Match(value, arms) => {
            expression(value, symbols, locals, base)?;
            for arm in arms {
                arm.location.line += base;
                let mut nested = locals.clone();
                match &arm.pattern {
                    Pattern::Binding(name) => {
                        local(name)?;
                        nested.insert(name.clone());
                    }
                    Pattern::Constructor(_, names) => {
                        for name in names {
                            local(name)?;
                            nested.insert(name.clone());
                        }
                    }
                    Pattern::Bool(_) => {}
                }
                block(&mut arm.body, symbols, &mut nested, base)?;
            }
        }
        ExprKind::Int(_) | ExprKind::Text(_) | ExprKind::Bool(_) | ExprKind::Role(_) => {}
    }
    Ok(())
}

fn html(
    nodes: &mut [HtmlNode],
    symbols: &BTreeMap<String, String>,
    locals: &BTreeSet<String>,
    base: usize,
) -> Result<()> {
    for node in nodes {
        match node {
            HtmlNode::Text(_) => {}
            HtmlNode::Expression(value) => expression(value, symbols, locals, base)?,
            HtmlNode::Element {
                attributes,
                children,
                ..
            } => {
                for (name, attribute) in attributes {
                    if let HtmlAttribute::Expression(value) = attribute {
                        if name == "on-click" {
                            value.location.line += base;
                        } else {
                            expression(value, symbols, locals, base)?;
                        }
                    }
                }
                html(children, symbols, locals, base)?;
            }
        }
    }
    Ok(())
}
