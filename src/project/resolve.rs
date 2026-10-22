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
    public_interfaces(modules)?;
    let own = own_symbols(modules)?;
    for module in modules.values_mut() {
        for declaration in &module.parsed.program.declarations {
            if let Declaration::Enum(e) = declaration
                && module.parsed.exports.contains(&e.name)
            {
                for (name, _) in &e.variants {
                    module.parsed.exports.insert(name.clone());
                }
            }
        }
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
                for (_, ty) in &mut function.parameters {
                    resolve_type_name(ty, &visible)?;
                }
                resolve_type_name(&mut function.result, &visible)?;
                function.name = own[name][&function.name].clone();
                function.location.line += module.line_base;
                block(&mut function.body, &visible, &mut locals, module.line_base)?;
            } else {
                server_declaration(declaration, &visible, module.line_base)?;
            }
        }
        components(module, &visible)?;
        for component in &mut module.parsed.components {
            component.name = own[name][&component.name].clone();
        }
    }
    Ok(())
}

fn server_declaration(
    declaration: &mut Declaration,
    visible: &BTreeMap<String, String>,
    base: usize,
) -> Result<()> {
    match declaration {
        Declaration::Alias(name, ty, location) => {
            local(name)?;
            if let Some(n) = visible.get(name) {
                *name = n.clone();
            }
            resolve_type_name(ty, visible)?;
            location.line += base;
        }
        Declaration::Resource(name, record, location) => {
            local(name)?;
            if let Some(n) = visible.get(record) {
                *record = n.clone();
            }
            location.line += base;
        }
        Declaration::Enum(e) => {
            local(&e.name)?;
            if let Some(n) = visible.get(&e.name) {
                e.name = n.clone();
            }
            e.location.line += base;
            for (name, fields) in &mut e.variants {
                if let Some(n) = visible.get(name) {
                    *name = n.clone();
                }
                for (_, ty) in fields {
                    resolve_type_name(ty, visible)?;
                }
            }
        }
        Declaration::Record(record) => {
            local(&record.name)?;
            if let Some(n) = visible.get(&record.name) {
                record.name = n.clone();
            }
            record.location.line += base;
            for field in &mut record.fields {
                local(&field.name)?;
                resolve_type_name(&mut field.ty, visible)?;
                field.location.line += base;
                if let Some(value) = &mut field.default {
                    expression(value, visible, &BTreeSet::new(), base)?;
                }
            }
        }
        Declaration::Policy(policy) => {
            policy.location.line += base;
            for action in &mut policy.actions {
                action.location.line += base;
                let locals = action
                    .bindings
                    .iter()
                    .cloned()
                    .chain([action.principal.clone()])
                    .collect();
                expression(&mut action.expression, &BTreeMap::new(), &locals, base)?;
            }
        }
        _ => return Err(input("unsupported project declaration")),
    }
    Ok(())
}

fn components(module: &mut Module, visible: &BTreeMap<String, String>) -> Result<()> {
    for component in &mut module.parsed.components {
        local(&component.name)?;
        component.location.line += module.line_base;
        let mut locals: BTreeSet<_> = component
            .states
            .iter()
            .map(|(name, _, _)| name.clone())
            .collect();
        if locals.len() != component.states.len() {
            return Err(input("duplicate component state"));
        }
        for (name, ty) in &mut component.properties {
            local(name)?;
            resolve_type_name(ty, visible)?;
            if !locals.insert(name.clone()) {
                return Err(input("duplicate component property/state"));
            }
        }
        let mut initial = component
            .properties
            .iter()
            .map(|(n, _)| n.clone())
            .collect::<BTreeSet<_>>();
        for (name, ty, value) in &mut component.states {
            resolve_type_name(ty, visible)?;
            local(name)?;
            expression(value, visible, &initial, module.line_base)?;
            initial.insert(name.clone());
        }
        for builtin in ["pending", "error", "route"] {
            if !locals.insert(builtin.into()) {
                return Err(input("reserved component state name"));
            }
        }
        for (name, ty, value) in &mut component.derived {
            local(name)?;
            resolve_type_name(ty, visible)?;
            expression(value, visible, &locals, module.line_base)?;
            if !locals.insert(name.clone()) {
                return Err(input("duplicate derived name"));
            }
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
    if !identifier(name) || ["host", "rpc", "session"].contains(&name) {
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
            callee,
            arguments,
            types,
        } => {
            for ty in types {
                resolve_type_name(ty, symbols)?;
            }
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
                match &mut arm.pattern {
                    Pattern::Binding(name) => {
                        local(name)?;
                        nested.insert(name.clone());
                    }
                    Pattern::Constructor(constructor, names) => {
                        if constructor.starts_with("__diamond_") {
                            return Err(input("compiler-generated variant names are reserved"));
                        }
                        if let Some(n) = symbols.get(constructor) {
                            *constructor = n.clone();
                        }
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
                let mut nested = locals.clone();
                for (name, attribute) in attributes.iter() {
                    if name == "as"
                        && let HtmlAttribute::Literal(n) = attribute
                    {
                        local(n)?;
                        nested.insert(n.clone());
                    }
                }
                for (name, attribute) in attributes {
                    if name == "component"
                        && let HtmlAttribute::Literal(n) = attribute
                    {
                        if n.starts_with("__diamond_") {
                            return Err(input("compiler-generated component names are reserved"));
                        }
                        if let Some(resolved) = symbols.get(n) {
                            *n = resolved.clone();
                        }
                    }
                    if let HtmlAttribute::Expression(value) = attribute {
                        if ["on-click", "on-submit", "bind-value", "bind-checked"]
                            .contains(&name.as_str())
                        {
                            value.location.line += base;
                        } else {
                            expression(value, symbols, &nested, base)?;
                        }
                    }
                }
                html(children, symbols, &nested, base)?;
            }
        }
    }
    Ok(())
}

fn resolve_type_name(
    ty: &mut crate::syntax::TypeName,
    symbols: &BTreeMap<String, String>,
) -> Result<()> {
    match ty {
        crate::syntax::TypeName::Named(n) => {
            if n.starts_with("__diamond_") {
                return Err(input("compiler-generated type names are reserved"));
            }
            if let Some(resolved) = symbols.get(n) {
                *n = resolved.clone();
            }
        }
        crate::syntax::TypeName::Generic(name, args) => {
            if name.starts_with("__diamond_") {
                return Err(input("compiler-generated type names are reserved"));
            }
            for arg in args {
                resolve_type_name(arg, symbols)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn public_interfaces(modules: &BTreeMap<String, Module>) -> Result<()> {
    use crate::syntax::TypeName;
    fn check(ty: &TypeName, private: &BTreeSet<String>) -> Result<()> {
        match ty {
            TypeName::Named(n) if private.contains(n) => {
                return Err(input("public interfaces cannot expose private types"));
            }
            TypeName::Generic(_, args) => {
                for a in args {
                    check(a, private)?;
                }
            }
            _ => {}
        }
        Ok(())
    }
    for module in modules.values() {
        let private = module
            .parsed
            .program
            .declarations
            .iter()
            .filter_map(|d| match d {
                Declaration::Alias(n, ..) => Some(n),
                Declaration::Record(r) => Some(&r.name),
                Declaration::Enum(e) => Some(&e.name),
                _ => None,
            })
            .filter(|n| !module.parsed.exports.contains(*n))
            .cloned()
            .collect();
        if module.config.target != Target::Shared {
            continue;
        }
        for d in &module.parsed.program.declarations {
            match d {
                Declaration::Function(f) if module.parsed.exports.contains(&f.name) => {
                    check(&f.result, &private)?;
                    for (_, t) in &f.parameters {
                        check(t, &private)?;
                    }
                }
                Declaration::Alias(n, t, _) if module.parsed.exports.contains(n) => {
                    check(t, &private)?;
                }
                Declaration::Record(r) if module.parsed.exports.contains(&r.name) => {
                    for f in &r.fields {
                        check(&f.ty, &private)?;
                    }
                }
                Declaration::Enum(e) if module.parsed.exports.contains(&e.name) => {
                    for (_, fs) in &e.variants {
                        for (_, t) in fs {
                            check(t, &private)?;
                        }
                    }
                }
                _ => {}
            }
        }
    }
    Ok(())
}
pub(super) fn public_name(name: &str) -> &str {
    let Some(rest) = name.strip_prefix("__diamond_variant_") else {
        return name;
    };
    let Some((length, rest)) = rest.split_once('_') else {
        return name;
    };
    let Ok(length) = length.parse::<usize>() else {
        return name;
    };
    rest.get(length + 1..).unwrap_or(name)
}

fn own_symbols(
    modules: &BTreeMap<String, Module>,
) -> Result<BTreeMap<String, BTreeMap<String, String>>> {
    let mut own = BTreeMap::new();
    for (name, module) in modules {
        let mut symbols = BTreeMap::new();
        for declaration in &module.parsed.program.declarations {
            if let Declaration::Function(function) = declaration {
                if !identifier(&function.name) || crate::standard::is_builtin(&function.name) {
                    return Err(input("invalid or reserved project function name"));
                }
                symbols.insert(
                    function.name.clone(),
                    format!("__diamond_fn_{}_{name}_{}", name.len(), function.name),
                );
            }
        }
        for declaration in &module.parsed.program.declarations {
            let typed = match declaration {
                Declaration::Alias(n, _, _) if module.config.target == Target::Shared => Some(n),
                Declaration::Record(r) if module.config.target == Target::Shared => Some(&r.name),
                Declaration::Enum(e) if module.config.target == Target::Shared => Some(&e.name),
                _ => None,
            };
            if let Some(n) = typed {
                symbols.insert(
                    n.clone(),
                    format!("__diamond_type_{}_{name}_{n}", name.len()),
                );
            }
            if let Declaration::Enum(e) = declaration {
                for (variant, _) in &e.variants {
                    if !identifier(variant)
                        || !variant.chars().next().is_some_and(char::is_uppercase)
                        || symbols.contains_key(variant)
                    {
                        return Err(input("invalid or conflicting enum constructor"));
                    }
                    symbols.insert(
                        variant.clone(),
                        format!("__diamond_variant_{}_{name}_{variant}", name.len()),
                    );
                }
            }
        }
        for component in &module.parsed.components {
            symbols.insert(
                component.name.clone(),
                format!(
                    "__diamond_component_{}_{name}_{}",
                    name.len(),
                    component.name
                ),
            );
        }
        own.insert(name.clone(), symbols);
    }
    Ok(own)
}
