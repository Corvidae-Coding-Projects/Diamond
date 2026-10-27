//! Declaration resolution, local typing, closed-call-graph effects, and route checks.

mod integrations;

use crate::authority::{Effect, Effects};
use crate::error::{Error, Location, Result};
use crate::hir::{self, CheckedProgram, Segment};
use crate::policy::Action;
use crate::syntax::{
    self, Binary, Declaration, Expr, ExprKind, HtmlAttribute, HtmlNode, MatchArm, Pattern,
    Statement, StatementKind, TypeName, Unary,
};
use crate::value::{Type, Value, ValueKind};
use std::collections::{BTreeMap, BTreeSet, VecDeque};

pub(crate) fn check(source: &syntax::Program) -> Result<CheckedProgram> {
    check_inner(source, Effects::new(), false, None, &BTreeMap::new())
}

pub(crate) fn check_project(
    source: &syntax::Program,
    grants: Effects,
    storage: Option<&crate::relational::Storage>,
    durable: &BTreeMap<String, (String, String)>,
) -> Result<CheckedProgram> {
    check_inner(source, grants, true, storage, durable)
}

fn check_inner(
    source: &syntax::Program,
    grants: Effects,
    host_operations: bool,
    storage: Option<&crate::relational::Storage>,
    durable: &BTreeMap<String, (String, String)>,
) -> Result<CheckedProgram> {
    let mut program = CheckedProgram {
        host_operations,
        durable: BTreeMap::new(),
        storage: storage.cloned(),
        app: None,
        aliases: BTreeMap::new(),
        records: BTreeMap::new(),
        enums: BTreeMap::new(),
        resources: BTreeMap::new(),
        functions: BTreeMap::new(),
        routes: Vec::new(),
        policies: BTreeMap::new(),
        grants,
    };
    if host_operations {
        program.app = Some(syntax::App {
            database: true,
            ..syntax::App::default()
        });
    }
    let aliases = declare_types(source, &mut program)?;
    for declaration in &source.declarations {
        if let Declaration::Alias(name, ty, location) = declaration {
            program.aliases.insert(
                name.clone(),
                resolve(ty, &aliases, &program.records, &program.enums, 0, *location)?,
            );
        }
    }
    resolve_records(source, &mut program, &aliases)?;
    resolve_enums(source, &mut program)?;
    resolve_resources(source, &mut program)?;
    resolve_functions(source, &mut program)?;
    resolve_durable(&mut program, durable)?;
    program.storage = storage.map(|s| s.checked(&program)).transpose()?;
    for declaration in &source.declarations {
        if let Declaration::Policy(policy) = declaration {
            let record_name = program.resources.get(&policy.resource).ok_or_else(|| {
                Error::diagnostic("D200", policy.location, "policy has no declared resource")
            })?;
            let record = program.records.get(record_name).ok_or(Error::Denied)?;
            if program
                .policies
                .insert(
                    policy.resource.clone(),
                    crate::policy::compile(policy, record, &program)?,
                )
                .is_some()
            {
                return Err(duplicate(policy.location, &policy.resource));
            }
        }
    }
    if let Some(app) = &program.app {
        for (effect, resource) in &app.grants {
            if !program.resources.contains_key(resource) {
                return Err(Error::diagnostic(
                    "D301",
                    app.location,
                    format!("unknown capability resource {resource}"),
                ));
            }
            let effect = match effect.as_str() {
                "DbRead" => Effect::Read(resource.clone()),
                "DbWrite" => Effect::Write(resource.clone()),
                _ => return Err(Error::unsupported(app.location, format!("effect {effect}"))),
            };
            program.grants.insert(effect);
        }
    }
    for declaration in &source.declarations {
        if let Declaration::Route(route) = declaration {
            add_route(&mut program, route)?;
        }
    }
    check_functions(&mut program)?;
    check_routes(&mut program)?;
    Ok(program)
}

fn resolve_durable(
    program: &mut CheckedProgram,
    durable: &BTreeMap<String, (String, String)>,
) -> Result<()> {
    for (binding, (resource, name)) in durable {
        let ty = if binding.starts_with("jobs/") || binding.starts_with("workflows/") {
            program
                .functions
                .get(name)
                .and_then(|f| f.parameters.get(1))
                .map(|(_, t)| t.clone())
                .ok_or(Error::Denied)?
        } else if name == "Text" {
            Type::Text
        } else if program.records.contains_key(name) {
            Type::Record(name.clone())
        } else if program.enums.contains_key(name) {
            Type::Enum(name.clone())
        } else {
            return Err(Error::Input("unknown event payload type".into()));
        };
        if !program.resources.contains_key(resource)
            || !crate::project::wire::safe_type(&ty, program)
        {
            return Err(Error::Input(
                "invalid durable binding resource or payload type".into(),
            ));
        }
        program
            .durable
            .insert(binding.clone(), (resource.clone(), ty));
    }
    Ok(())
}

fn declare_types(
    source: &syntax::Program,
    program: &mut CheckedProgram,
) -> Result<BTreeMap<String, TypeName>> {
    let mut aliases = BTreeMap::new();
    let mut type_names = BTreeSet::new();
    for declaration in &source.declarations {
        let declared_type = match declaration {
            Declaration::Alias(name, _, location) => Some((name, *location)),
            Declaration::Record(record) => Some((&record.name, record.location)),
            Declaration::Enum(enumeration) => Some((&enumeration.name, enumeration.location)),
            _ => None,
        };
        if let Some((name, location)) = declared_type
            && (!type_names.insert(name.clone())
                || [
                    "Decimal",
                    "Timestamp",
                    "Duration",
                    "Int",
                    "Bool",
                    "Text",
                    "Unit",
                    "Html",
                    "Url",
                    "DataError",
                    "Response",
                    "Option",
                    "Result",
                    "List",
                    "Blob",
                    "Stream",
                    "Action",
                ]
                .contains(&name.as_str()))
        {
            return Err(duplicate(location, name));
        }
        match declaration {
            Declaration::Alias(name, ty, location) => {
                if aliases.insert(name.clone(), ty.clone()).is_some()
                    || program.records.contains_key(name)
                {
                    return Err(duplicate(*location, name));
                }
            }
            Declaration::Record(record) => {
                if program
                    .records
                    .insert(
                        record.name.clone(),
                        hir::Record {
                            fields: BTreeMap::new(),
                        },
                    )
                    .is_some()
                    || aliases.contains_key(&record.name)
                {
                    return Err(duplicate(record.location, &record.name));
                }
            }
            Declaration::Enum(enumeration) => {
                program
                    .enums
                    .insert(enumeration.name.clone(), hir::Enum::default());
            }
            Declaration::App(app) if program.app.replace(app.clone()).is_some() => {
                return Err(duplicate(app.location, "app"));
            }
            _ => {}
        }
    }
    Ok(aliases)
}

fn duplicate(location: Location, name: &str) -> Error {
    Error::diagnostic("D101", location, format!("duplicate declaration {name}"))
}

fn resolve(
    ty: &TypeName,
    aliases: &BTreeMap<String, TypeName>,
    records: &BTreeMap<String, hir::Record>,
    enums: &BTreeMap<String, hir::Enum>,
    depth: usize,
    location: Location,
) -> Result<Type> {
    if depth > 32 {
        return Err(Error::diagnostic(
            "D103",
            location,
            "cyclic or excessively nested type alias",
        ));
    }
    Ok(match ty {
        TypeName::Named(name) => {
            return resolve_named(name, aliases, records, enums, depth, location);
        }
        TypeName::Id(tag) if !tag.is_empty() => Type::Id(tag.clone()),
        TypeName::Secret(tag) if crate::project::resolve::identifier(tag) => {
            Type::Secret(tag.clone())
        }
        TypeName::Blob(binding) if crate::project::resolve::identifier(binding) => {
            Type::Blob(binding.clone())
        }
        TypeName::Stream(channel) if crate::project::resolve::identifier(channel) => {
            Type::Stream(channel.clone())
        }
        TypeName::BoundedText(a, b) => Type::BoundedText(*a, *b),
        TypeName::Generic(name, arguments) if name == "Action" && arguments.len() == 2 => {
            Type::Action(
                Box::new(resolve(
                    &arguments[0],
                    aliases,
                    records,
                    enums,
                    depth + 1,
                    location,
                )?),
                Box::new(resolve(
                    &arguments[1],
                    aliases,
                    records,
                    enums,
                    depth + 1,
                    location,
                )?),
            )
        }
        TypeName::Generic(name, arguments)
            if ["Option", "List"].contains(&name.as_str()) && arguments.len() == 1 =>
        {
            let inner = Box::new(resolve(
                &arguments[0],
                aliases,
                records,
                enums,
                depth + 1,
                location,
            )?);
            if name == "Option" {
                Type::Option(inner)
            } else {
                Type::List(inner)
            }
        }
        TypeName::Generic(name, arguments)
            if name == "Result"
                && arguments.len() == 2
                && arguments[1] == TypeName::Named("DataError".into()) =>
        {
            Type::Result(Box::new(resolve(
                &arguments[0],
                aliases,
                records,
                enums,
                depth + 1,
                location,
            )?))
        }
        TypeName::Generic(name, args) if name == "Result" && args.len() == 2 => {
            let error = resolve(&args[1], aliases, records, enums, depth + 1, location)?;
            if !matches!(error, Type::Enum(_)) {
                return Err(Error::diagnostic(
                    "D103",
                    location,
                    "application Result errors must be enums",
                ));
            }
            Type::AppResult(
                Box::new(resolve(
                    &args[0],
                    aliases,
                    records,
                    enums,
                    depth + 1,
                    location,
                )?),
                Box::new(error),
            )
        }
        _ => {
            return Err(Error::unsupported(location, format!("type {ty:?}")));
        }
    })
}

fn resolve_named(
    name: &str,
    aliases: &BTreeMap<String, TypeName>,
    records: &BTreeMap<String, hir::Record>,
    enums: &BTreeMap<String, hir::Enum>,
    depth: usize,
    location: Location,
) -> Result<Type> {
    Ok(match name {
        "Int" => Type::Int,
        "Decimal" => Type::Decimal,
        "Timestamp" => Type::Timestamp,
        "Duration" => Type::Duration,
        "Bool" => Type::Bool,
        "Text" => Type::Text,
        "Unit" => Type::Unit,
        "Html" => Type::Html,
        "Url" => Type::Url,
        "Response" => Type::Response,
        "DataError" => Type::DataError,
        _ if records.contains_key(name) => Type::Record(name.into()),
        _ if enums.contains_key(name) => Type::Enum(name.into()),
        _ => {
            return resolve(
                aliases.get(name).ok_or_else(|| {
                    Error::diagnostic("D103", location, format!("unknown type {name}"))
                })?,
                aliases,
                records,
                enums,
                depth + 1,
                location,
            );
        }
    })
}

pub(crate) fn resolve_type(
    ty: &TypeName,
    program: &CheckedProgram,
    location: Location,
) -> Result<Type> {
    match ty {
        TypeName::Named(name) if program.aliases.contains_key(name) => {
            Ok(program.aliases[name].clone())
        }
        TypeName::Generic(name, args) if name == "Action" && args.len() == 2 => Ok(Type::Action(
            Box::new(resolve_type(&args[0], program, location)?),
            Box::new(resolve_type(&args[1], program, location)?),
        )),
        TypeName::Generic(name, args)
            if name == "Result"
                && args.len() == 2
                && args[1] != TypeName::Named("DataError".into()) =>
        {
            let error = resolve_type(&args[1], program, location)?;
            if !matches!(error, Type::Enum(_)) {
                return Err(Error::diagnostic(
                    "D103",
                    location,
                    "application Result errors must be enums",
                ));
            }
            Ok(Type::AppResult(
                Box::new(resolve_type(&args[0], program, location)?),
                Box::new(error),
            ))
        }
        TypeName::Generic(name, args) if name == "Option" && args.len() == 1 => Ok(Type::Option(
            Box::new(resolve_type(&args[0], program, location)?),
        )),
        TypeName::Generic(name, args) if name == "List" && args.len() == 1 => Ok(Type::List(
            Box::new(resolve_type(&args[0], program, location)?),
        )),
        TypeName::Generic(name, args)
            if name == "Result"
                && args.len() == 2
                && args[1] == TypeName::Named("DataError".into()) =>
        {
            Ok(Type::Result(Box::new(resolve_type(
                &args[0], program, location,
            )?)))
        }
        _ => resolve(
            ty,
            &BTreeMap::new(),
            &program.records,
            &program.enums,
            0,
            location,
        ),
    }
}

fn resolve_records(
    source: &syntax::Program,
    program: &mut CheckedProgram,
    aliases: &BTreeMap<String, TypeName>,
) -> Result<()> {
    for declaration in &source.declarations {
        if let Declaration::Record(record) = declaration {
            let mut fields = BTreeMap::new();
            for field in &record.fields {
                let ty = resolve(
                    &field.ty,
                    aliases,
                    &program.records,
                    &program.enums,
                    0,
                    field.location,
                )?;
                let default = field.default.as_ref().map(constant).transpose()?;
                if default.as_ref().is_some_and(|value| !value.fits(&ty)) {
                    return Err(Error::diagnostic(
                        "D102",
                        field.location,
                        "field default does not satisfy its declared type",
                    ));
                }
                if field.created_by && (ty != Type::Id("user".into()) || default.is_some()) {
                    return Err(Error::diagnostic(
                        "D102",
                        field.location,
                        "created_by_principal requires Id<\"user\"> with no other default",
                    ));
                }
                if fields
                    .insert(
                        field.name.clone(),
                        hir::Field {
                            ty,
                            default,
                            created_by: field.created_by,
                        },
                    )
                    .is_some()
                {
                    return Err(duplicate(field.location, &field.name));
                }
            }
            program
                .records
                .insert(record.name.clone(), hir::Record { fields });
        }
    }
    Ok(())
}

fn resolve_enums(source: &syntax::Program, program: &mut CheckedProgram) -> Result<()> {
    let mut constructors: BTreeSet<_> = program
        .records
        .keys()
        .chain(program.aliases.keys())
        .cloned()
        .collect();
    constructors.extend(
        [
            "Ok",
            "Err",
            "Some",
            "None",
            "route_url",
            "len",
            "error_message",
            "bad_request",
        ]
        .map(str::to_owned),
    );
    constructors.extend(crate::action::EMPTY.map(str::to_owned));
    constructors.extend(["ActionSucceeded".into(), "ActionRejected".into()]);
    for declaration in &source.declarations {
        if let Declaration::Enum(enumeration) = declaration {
            if enumeration.variants.is_empty() {
                return Err(Error::diagnostic(
                    "D122",
                    enumeration.location,
                    "an enum needs at least one variant",
                ));
            }
            let mut variants = BTreeMap::new();
            for (name, fields) in &enumeration.variants {
                if !(name.chars().next().is_some_and(char::is_uppercase)
                    || name.starts_with("__diamond_variant_"))
                    || !constructors.insert(name.clone())
                {
                    return Err(duplicate(enumeration.location, name));
                }
                let mut names = BTreeSet::new();
                let mut resolved = Vec::new();
                for (field, ty) in fields {
                    if !names.insert(field.clone()) {
                        return Err(duplicate(enumeration.location, field));
                    }
                    resolved.push((
                        field.clone(),
                        resolve_type(ty, program, enumeration.location)?,
                    ));
                }
                variants.insert(name.clone(), resolved);
            }
            program
                .enums
                .insert(enumeration.name.clone(), hir::Enum { variants });
        }
    }
    Ok(())
}

fn resolve_resources(source: &syntax::Program, program: &mut CheckedProgram) -> Result<()> {
    let mut repository_names = BTreeSet::new();
    for declaration in &source.declarations {
        if let Declaration::Resource(name, record, location) = declaration {
            let definition = program.records.get(record).ok_or_else(|| {
                Error::diagnostic(
                    "D103",
                    *location,
                    format!("unknown resource record {record}"),
                )
            })?;
            if !definition
                .fields
                .get("id")
                .is_some_and(|field| matches!(field.ty, Type::Id(_)) && !field.created_by)
            {
                return Err(Error::diagnostic(
                    "D103",
                    *location,
                    "a resource needs a generated id field with an Id type and no server-derived annotation",
                ));
            }
            let mut columns = BTreeSet::new();
            for (name, field) in &definition.fields {
                if !columns.insert(name.to_ascii_lowercase()) {
                    return Err(Error::diagnostic(
                        "D103",
                        *location,
                        "SQLite resource fields must have distinct names ignoring ASCII case",
                    ));
                }
                if !(scalar_input_type(&field.ty)
                    || program.storage.is_some() && crate::relational::scalar(&field.ty, program))
                {
                    return Err(Error::unsupported(
                        *location,
                        format!("database field {name} of type {}", field.ty),
                    ));
                }
            }
            if program
                .resources
                .insert(name.clone(), record.clone())
                .is_some()
                || !repository_names.insert(name.to_ascii_lowercase())
            {
                return Err(duplicate(*location, name));
            }
        }
    }
    Ok(())
}

fn resolve_functions(source: &syntax::Program, program: &mut CheckedProgram) -> Result<()> {
    for declaration in &source.declarations {
        if let Declaration::Function(function) = declaration {
            if crate::standard::is_builtin(&function.name)
                || crate::action::constructor(&function.name)
                || program.records.contains_key(&function.name)
                || program.aliases.contains_key(&function.name)
                || program
                    .enums
                    .values()
                    .any(|enumeration| enumeration.variants.contains_key(&function.name))
                || [
                    "Ok",
                    "Err",
                    "Some",
                    "None",
                    "route_url",
                    "len",
                    "error_message",
                    "bad_request",
                ]
                .contains(&function.name.as_str())
            {
                return Err(duplicate(function.location, &function.name));
            }
            let parameters = function
                .parameters
                .iter()
                .map(|(name, ty)| Ok((name.clone(), resolve_type(ty, program, function.location)?)))
                .collect::<Result<Vec<_>>>()?;
            let result = resolve_type(&function.result, program, function.location)?;
            let checked = hir::Function {
                parameters,
                result,
                body: function.body.clone(),
                effects: Effects::new(),
                opens_transaction: false,
                location: function.location,
            };
            if program
                .functions
                .insert(function.name.clone(), checked)
                .is_some()
            {
                return Err(duplicate(function.location, &function.name));
            }
        }
    }
    Ok(())
}

fn check_functions(program: &mut CheckedProgram) -> Result<()> {
    let mut dependencies = BTreeMap::new();
    for (name, function) in &program.functions {
        let checker = Checker::function(program, function)?;
        dependencies.insert(
            name.clone(),
            (checker.effects, checker.calls, checker.opens_transaction),
        );
    }
    for (name, (effects, _, opens_transaction)) in &dependencies {
        let function = program.functions.get_mut(name).ok_or(Error::Denied)?;
        function.effects.clone_from(effects);
        function.opens_transaction = *opens_transaction;
    }

    let mut callers: BTreeMap<String, BTreeSet<String>> = program
        .functions
        .keys()
        .map(|name| (name.clone(), BTreeSet::new()))
        .collect();
    for (caller, (_, calls, _)) in &dependencies {
        for called in calls {
            callers
                .get_mut(called)
                .ok_or(Error::Denied)?
                .insert(caller.clone());
        }
    }
    let mut queue = VecDeque::new();
    let mut queued = BTreeSet::new();
    for (name, function) in &program.functions {
        if (!function.effects.is_empty() || function.opens_transaction)
            && queued.insert(name.clone())
        {
            queue.push_back(name.clone());
        }
    }
    while let Some(called) = queue.pop_front() {
        queued.remove(&called);
        let called_function = program.functions.get(&called).ok_or(Error::Denied)?;
        let effects = called_function.effects.clone();
        let opens_transaction = called_function.opens_transaction;
        for caller in callers.get(&called).ok_or(Error::Denied)? {
            let function = program.functions.get_mut(caller).ok_or(Error::Denied)?;
            let previous = (function.effects.len(), function.opens_transaction);
            function.effects.extend(effects.iter().cloned());
            function.opens_transaction |= opens_transaction;
            if previous != (function.effects.len(), function.opens_transaction)
                && queued.insert(caller.clone())
            {
                queue.push_back(caller.clone());
            }
        }
    }
    for function in program.functions.values() {
        // Calls now have complete transitive transaction requirements, including cycles.
        Checker::function(program, function)?;
        check_budget(program, &function.effects, function.location)?;
    }
    Ok(())
}

fn scalar_input_type(ty: &Type) -> bool {
    let base = if let Type::Option(inner) = ty {
        inner.as_ref()
    } else {
        ty
    };
    matches!(
        base,
        Type::Int | Type::Bool | Type::Text | Type::BoundedText(_, _) | Type::Id(_)
    )
}

fn check_budget(program: &CheckedProgram, effects: &Effects, location: Location) -> Result<()> {
    if !effects.is_subset(&program.grants) {
        return Err(Error::diagnostic(
            "D301",
            location,
            format!("effects {effects:?} exceed the application's declared authority"),
        ));
    }
    if effects
        .iter()
        .any(|effect| matches!(effect, Effect::Read(_) | Effect::Write(_)))
        && !program.app.as_ref().is_some_and(|app| app.database)
    {
        return Err(Error::diagnostic(
            "D301",
            location,
            "database effects require an explicit database declaration",
        ));
    }
    Ok(())
}

pub(crate) fn route_segments(
    path: &str,
    program: &CheckedProgram,
    location: Location,
) -> Result<Vec<Segment>> {
    if !path.starts_with('/') || path.contains(['?', '#', '\\', '%']) || path.contains("//") {
        return Err(Error::diagnostic(
            "D402",
            location,
            "route paths must be unambiguous internal paths",
        ));
    }
    let mut names = BTreeSet::new();
    path.trim_start_matches('/')
        .split('/')
        .map(|segment| {
            if let Some(parameter) = segment
                .strip_prefix('{')
                .and_then(|value| value.strip_suffix('}'))
            {
                let (name, ty) = parameter.split_once(':').ok_or_else(|| {
                    Error::diagnostic("D402", location, "route parameters require {name:Type}")
                })?;
                if !crate::auth::valid_id(name) || !names.insert(name.to_string()) {
                    return Err(Error::diagnostic(
                        "D403",
                        location,
                        "invalid or duplicate route parameter",
                    ));
                }
                let ty = resolve_type(&TypeName::Named(ty.into()), program, location)?;
                if !matches!(ty, Type::Int | Type::Id(_)) {
                    return Err(Error::unsupported(
                        location,
                        "route parameters other than Int or Id",
                    ));
                }
                Ok(Segment::Parameter(name.into(), ty))
            } else if segment.contains(['{', '}'])
                || segment.chars().any(|c| c.is_control() || c.is_whitespace())
            {
                Err(Error::diagnostic("D402", location, "invalid route segment"))
            } else {
                Ok(Segment::Literal(segment.into()))
            }
        })
        .collect()
}

fn add_route(program: &mut CheckedProgram, route: &syntax::Route) -> Result<()> {
    if route.path == "/_diamond" || route.path.starts_with("/_diamond/") {
        return Err(Error::diagnostic(
            "D405",
            route.location,
            "the /_diamond namespace belongs to runtime authentication endpoints",
        ));
    }
    if !route.public && !program.app.as_ref().is_some_and(|app| app.sessions) {
        return Err(Error::diagnostic(
            "D401",
            route.location,
            "private routes require a configured session provider",
        ));
    }
    let segments = route_segments(&route.path, program, route.location)?;
    for other in &program.routes {
        let overlap = other.method == route.method
            && other.segments.len() == segments.len()
            && other.segments.iter().zip(&segments).all(
                |(a, b)| !matches!((a, b), (Segment::Literal(a), Segment::Literal(b)) if a != b),
            );
        if overlap
            || route
                .name
                .as_ref()
                .is_some_and(|name| other.name.as_ref() == Some(name))
        {
            return Err(Error::diagnostic(
                "D402",
                route.location,
                "ambiguous route pattern or duplicate route handle",
            ));
        }
    }
    program.routes.push(hir::Route {
        public: route.public,
        method: route.method.clone(),
        path: route.path.clone(),
        name: route.name.clone(),
        segments,
        body: route.body.clone(),
        effects: Effects::new(),
        location: route.location,
    });
    Ok(())
}

fn check_routes(program: &mut CheckedProgram) -> Result<()> {
    let routes = program.routes.clone();
    for (index, route) in routes.iter().enumerate() {
        let mut checker = Checker::new(program, true, true);
        for segment in &route.segments {
            if let Segment::Parameter(name, ty) = segment {
                checker.locals.insert(name.clone(), ty.clone());
            }
        }
        let result = checker.block(&route.body)?;
        if !matches!(
            result,
            Type::Text
                | Type::BoundedText(_, _)
                | Type::Html
                | Type::Url
                | Type::Response
                | Type::Unit
                | Type::Int
                | Type::Bool
        ) {
            return Err(Error::diagnostic(
                "D403",
                route.location,
                "route result must be explicit text, HTML, a redirect, or a scalar",
            ));
        }
        if ["GET", "HEAD"].contains(&route.method.as_str())
            && checker
                .effects
                .iter()
                .any(|effect| matches!(effect, Effect::Write(_)))
        {
            return Err(Error::diagnostic(
                "D302",
                route.location,
                "GET and HEAD cannot perform database writes, including through function calls",
            ));
        }
        check_budget(program, &checker.effects, route.location)?;
        let effects = checker.effects;
        program.routes[index].effects = effects;
    }
    Ok(())
}

fn mismatch(location: Location, expected: &Type, actual: &Type) -> Error {
    Error::diagnostic(
        "D110",
        location,
        format!("expected {expected}, found {actual}"),
    )
}

fn common_type(a: &Type, b: &Type, location: Location) -> Result<Type> {
    match (a, b) {
        (Type::Action(a, e), Type::Action(b, f)) => {
            return Ok(Type::Action(
                Box::new(common_type(a, b, location)?),
                Box::new(common_type(e, f, location)?),
            ));
        }
        (Type::Success(a), Type::AppResult(b, e)) | (Type::AppResult(b, e), Type::Success(a)) => {
            return Ok(Type::AppResult(
                Box::new(common_type(a, b, location)?),
                e.clone(),
            ));
        }
        (Type::Success(a), Type::Result(b)) | (Type::Result(b), Type::Success(a)) => {
            return Ok(Type::Result(Box::new(common_type(a, b, location)?)));
        }
        _ => {}
    }
    if a.accepts(b) {
        Ok(a.clone())
    } else if b.accepts(a) {
        Ok(b.clone())
    } else if matches!(a, Type::BoundedText(_, _)) && matches!(b, Type::BoundedText(_, _)) {
        Ok(Type::Text)
    } else {
        Err(mismatch(location, a, b))
    }
}

pub(crate) fn constant(expression: &Expr) -> Result<Value> {
    match &expression.kind {
        ExprKind::Int(value) => Ok(Value(ValueKind::Int(*value))),
        ExprKind::Bool(value) => Ok(Value(ValueKind::Bool(*value))),
        ExprKind::Text(value) => Ok(Value(ValueKind::Text(value.clone()))),
        ExprKind::Name(name) if name == "None" => Ok(Value(ValueKind::None)),
        ExprKind::Call {
            callee,
            arguments,
            types,
        } if matches!(&callee.kind, ExprKind::Name(name) if name == "Some")
            && types.is_empty()
            && arguments.len() == 1
            && arguments[0].0.is_none() =>
        {
            Ok(Value(ValueKind::Some(Box::new(constant(&arguments[0].1)?))))
        }
        ExprKind::Unary(Unary::Negate, value) => {
            let ValueKind::Int(value) = constant(value)?.0 else {
                return Err(Error::unsupported(
                    expression.location,
                    "non-integer negative default",
                ));
            };
            Ok(Value(ValueKind::Int(value.checked_neg().ok_or_else(
                || Error::Input("integer overflow in default".into()),
            )?)))
        }
        _ => Err(Error::unsupported(
            expression.location,
            "non-literal field defaults",
        )),
    }
}

enum TransactionContext {
    Outside,
    Inside,
}

struct Checker<'a> {
    program: &'a CheckedProgram,
    locals: BTreeMap<String, Type>,
    effects: Effects,
    calls: BTreeSet<String>,
    opens_transaction: bool,
    transaction_context: TransactionContext,
    route: bool,
    fallible: bool,
    application_error: Option<Type>,
    branch_depth: usize,
}

impl<'a> Checker<'a> {
    fn new(program: &'a CheckedProgram, route: bool, fallible: bool) -> Self {
        Self {
            program,
            locals: BTreeMap::new(),
            effects: Effects::new(),
            calls: BTreeSet::new(),
            opens_transaction: false,
            transaction_context: TransactionContext::Outside,
            route,
            fallible,
            application_error: None,
            branch_depth: 0,
        }
    }

    fn function(program: &'a CheckedProgram, function: &hir::Function) -> Result<Self> {
        let mut checker = Self::new(
            program,
            false,
            matches!(function.result, Type::Result(_) | Type::AppResult(_, _)),
        );
        if let Type::AppResult(_, error) = &function.result {
            checker.application_error = Some((**error).clone());
        }
        for (parameter, ty) in &function.parameters {
            if checker
                .locals
                .insert(parameter.clone(), ty.clone())
                .is_some()
            {
                return Err(duplicate(function.location, parameter));
            }
        }
        let actual = checker.block(&function.body)?;
        if !function.result.accepts(&actual) {
            let location = function
                .body
                .last()
                .map_or(function.location, |statement| statement.location);
            return Err(mismatch(location, &function.result, &actual));
        }
        Ok(checker)
    }

    fn block(&mut self, statements: &[Statement]) -> Result<Type> {
        let mut result = Type::Unit;
        for (index, statement) in statements.iter().enumerate() {
            result = match &statement.kind {
                StatementKind::Let(name, value) => {
                    let ty = self.expr(value)?;
                    if self.locals.insert(name.clone(), ty).is_some() {
                        return Err(duplicate(statement.location, name));
                    }
                    Type::Unit
                }
                StatementKind::Expression(value) => self.expr(value)?,
                StatementKind::Return(value) => {
                    if self.branch_depth != 0 {
                        return Err(Error::unsupported(
                            statement.location,
                            "early return from a nested block; use its final expression",
                        ));
                    }
                    if index + 1 != statements.len() {
                        return Err(Error::diagnostic(
                            "D111",
                            statement.location,
                            "unreachable statements after return",
                        ));
                    }
                    self.expr(value)?
                }
                StatementKind::Redirect(value) => {
                    if !self.route {
                        return Err(Error::diagnostic(
                            "D111",
                            statement.location,
                            "redirect is a route operation",
                        ));
                    }
                    let ty = self.expr(value)?;
                    if ty == Type::Text {
                        if let ExprKind::Text(text) = &value.kind {
                            crate::template::validate_internal_url(text, statement.location)?;
                        } else {
                            return Err(Error::diagnostic(
                                "D111",
                                statement.location,
                                "dynamic redirects require a typed route_url",
                            ));
                        }
                    } else if ty != Type::Url {
                        return Err(mismatch(statement.location, &Type::Url, &ty));
                    }
                    Type::Url
                }
                StatementKind::Transaction(body) => {
                    if !self.program.app.as_ref().is_some_and(|app| app.database) {
                        return Err(Error::diagnostic(
                            "D301",
                            statement.location,
                            "transaction blocks require an explicit database declaration",
                        ));
                    }
                    if matches!(self.transaction_context, TransactionContext::Inside) {
                        return Err(Error::unsupported(
                            statement.location,
                            "nested transaction blocks",
                        ));
                    }
                    self.opens_transaction = true;
                    self.transaction_context = TransactionContext::Inside;
                    let saved = self.locals.clone();
                    let result = self.branch(body);
                    self.locals = saved;
                    self.transaction_context = TransactionContext::Outside;
                    result?
                }
            };
        }
        Ok(result)
    }

    fn expr(&mut self, expression: &Expr) -> Result<Type> {
        Ok(match &expression.kind {
            ExprKind::Int(_) => Type::Int,
            ExprKind::Text(_) => Type::Text,
            ExprKind::Bool(_) => Type::Bool,
            ExprKind::Name(name) if name == "None" => Type::Option(Box::new(Type::Never)),
            ExprKind::Name(name) => self.name_type(name, expression.location)?,
            ExprKind::Role(_) => {
                return Err(Error::diagnostic(
                    "D112",
                    expression.location,
                    "role literals are restricted to policy predicates",
                ));
            }
            ExprKind::Unary(operator, value) => {
                let actual = self.expr(value)?;
                let expected = if *operator == Unary::Not {
                    Type::Bool
                } else {
                    Type::Int
                };
                if actual != expected {
                    return Err(mismatch(expression.location, &expected, &actual));
                }
                expected
            }
            ExprKind::Binary(a, operator, b) => self.binary(a, *operator, b)?,
            ExprKind::Field(value, field) => self.field_type(value, field, expression.location)?,
            ExprKind::Call {
                callee,
                arguments,
                types,
            } => self.call(callee, arguments, types, expression.location)?,
            ExprKind::Try(value) => {
                if !self.fallible {
                    return Err(Error::diagnostic(
                        "D114",
                        expression.location,
                        "? requires a Result-returning function or a route",
                    ));
                }
                let actual = self.expr(value)?;
                if let Type::Success(inner) = &actual {
                    return Ok((**inner).clone());
                }
                if let Type::AppResult(inner, error) = &actual {
                    if self.application_error.as_ref() != Some(error.as_ref()) {
                        return Err(Error::diagnostic(
                            "D114",
                            expression.location,
                            "? must preserve the declared application error type",
                        ));
                    }
                    return Ok((**inner).clone());
                }
                if self.application_error.is_some() {
                    return Err(Error::diagnostic(
                        "D114",
                        expression.location,
                        "DataError cannot propagate as an application error",
                    ));
                }
                let Type::Result(inner) = actual else {
                    return Err(Error::diagnostic(
                        "D114",
                        expression.location,
                        "? requires Result",
                    ));
                };
                *inner
            }
            ExprKind::Html(nodes) => {
                self.html(nodes)?;
                Type::Html
            }
            ExprKind::List(values) => {
                if values.len() > 1000 {
                    return Err(Error::Limit("list length"));
                }
                let mut ty = Type::Never;
                for value in values {
                    ty = common_type(&ty, &self.expr(value)?, value.location)?;
                }
                Type::List(Box::new(ty))
            }
            ExprKind::Match(value, arms) => self.match_expression(value, arms)?,
            ExprKind::For(name, values, body) => {
                let Type::List(inner) = self.expr(values)? else {
                    return Err(Error::diagnostic(
                        "D124",
                        expression.location,
                        "for requires a List",
                    ));
                };
                let saved = self.locals.clone();
                self.bind(name, *inner, expression.location)?;
                let result = self.branch(body);
                self.locals = saved;
                Type::List(Box::new(result?))
            }
        })
    }

    fn name_type(&self, name: &str, location: Location) -> Result<Type> {
        Ok({
            if let Some(ty) = self.locals.get(name) {
                ty.clone()
            } else if let Some(ty) = crate::action::signature(name, &[]) {
                ty
            } else if let Some((enumeration, fields)) = self.program.variant(name) {
                if !fields.is_empty() {
                    return Err(Error::diagnostic(
                        "D122",
                        location,
                        "payload variants require constructor arguments",
                    ));
                }
                Type::Enum(enumeration.into())
            } else {
                return Err(Error::diagnostic(
                    "D112",
                    location,
                    format!(
                        "unknown value {name}; authority and functions cannot be captured as ordinary values in this subset"
                    ),
                ));
            }
        })
    }

    fn field_type(&mut self, value: &Expr, field: &str, location: Location) -> Result<Type> {
        if matches!(&value.kind, ExprKind::Name(name) if name == "request")
            && self.route
            && !self.locals.contains_key("request")
        {
            match field {
                "authenticated" => return Ok(Type::Bool),
                "user_id" => return Ok(Type::Id("user".into())),
                _ => {}
            }
        }
        let ty = self.expr(value)?;
        let Type::Record(record) = ty else {
            return Err(Error::diagnostic(
                "D113",
                location,
                "field access needs a record",
            ));
        };
        self.program
            .records
            .get(&record)
            .and_then(|record| record.fields.get(field))
            .map(|field| field.ty.clone())
            .ok_or_else(|| Error::diagnostic("D113", location, format!("unknown field {field}")))
    }

    fn bind(&mut self, name: &str, ty: Type, location: Location) -> Result<()> {
        if name != "_" && self.locals.insert(name.into(), ty).is_some() {
            return Err(duplicate(location, name));
        }
        Ok(())
    }

    fn branch(&mut self, body: &[Statement]) -> Result<Type> {
        self.branch_depth += 1;
        let result = self.block(body);
        self.branch_depth -= 1;
        result
    }

    fn match_expression(&mut self, value: &Expr, arms: &[MatchArm]) -> Result<Type> {
        let ty = self.expr(value)?;
        let cases: BTreeMap<String, Vec<Type>> = match &ty {
            Type::Action(success, error) => crate::action::cases(success, error),
            Type::Enum(name) => self.program.enums[name]
                .variants
                .iter()
                .map(|(name, fields)| {
                    (
                        name.clone(),
                        fields.iter().map(|(_, ty)| ty.clone()).collect(),
                    )
                })
                .collect(),
            Type::Bool => [("true".into(), Vec::new()), ("false".into(), Vec::new())].into(),
            Type::AppResult(inner, error) => [
                ("Ok".into(), vec![(**inner).clone()]),
                ("Err".into(), vec![(**error).clone()]),
            ]
            .into(),
            Type::Result(inner) | Type::Success(inner) => [
                ("Ok".into(), vec![(**inner).clone()]),
                ("Err".into(), vec![Type::DataError]),
            ]
            .into(),
            Type::Option(inner) => [
                ("Some".into(), vec![(**inner).clone()]),
                ("None".into(), Vec::new()),
            ]
            .into(),
            _ => BTreeMap::new(),
        };
        let saved = self.locals.clone();
        let mut seen = BTreeSet::new();
        let mut wildcard = false;
        let mut result = Type::Never;
        for arm in arms {
            if wildcard {
                return Err(Error::diagnostic(
                    "D123",
                    arm.location,
                    "unreachable match arm",
                ));
            }
            self.locals.clone_from(&saved);
            match &arm.pattern {
                Pattern::Binding(name) => {
                    self.bind(name, ty.clone(), arm.location)?;
                    wildcard = true;
                }
                Pattern::Bool(value) => {
                    let name = value.to_string();
                    if ty != Type::Bool || !seen.insert(name) {
                        return Err(Error::diagnostic(
                            "D123",
                            arm.location,
                            "invalid or duplicate Bool pattern",
                        ));
                    }
                }
                Pattern::Constructor(name, bindings) => {
                    let fields = cases.get(name).ok_or_else(|| {
                        Error::diagnostic(
                            "D123",
                            arm.location,
                            "pattern is not a variant of the matched type",
                        )
                    })?;
                    if !seen.insert(name.clone()) || fields.len() != bindings.len() {
                        return Err(Error::diagnostic(
                            "D123",
                            arm.location,
                            "duplicate case or incorrect payload binding count",
                        ));
                    }
                    for (name, ty) in bindings.iter().zip(fields) {
                        self.bind(name, ty.clone(), arm.location)?;
                    }
                }
            }
            result = common_type(&result, &self.branch(&arm.body)?, arm.location)?;
        }
        self.locals = saved;
        if arms.is_empty() || (!wildcard && (cases.is_empty() || seen.len() != cases.len())) {
            return Err(Error::diagnostic(
                "D123",
                value.location,
                "match is not exhaustive",
            ));
        }
        Ok(result)
    }

    fn binary(&mut self, a: &Expr, operator: Binary, b: &Expr) -> Result<Type> {
        let left = self.expr(a)?;
        let right = self.expr(b)?;
        if !left.accepts(&right) && !right.accepts(&left) {
            return Err(mismatch(b.location, &left, &right));
        }
        match operator {
            Binary::And | Binary::Or if left == Type::Bool => Ok(Type::Bool),
            Binary::Equal | Binary::NotEqual
                if matches!(
                    left,
                    Type::Int | Type::Text | Type::BoundedText(_, _) | Type::Bool | Type::Id(_)
                ) =>
            {
                Ok(Type::Bool)
            }
            Binary::Less | Binary::LessEqual | Binary::Greater | Binary::GreaterEqual
                if matches!(left, Type::Int | Type::Text | Type::BoundedText(_, _)) =>
            {
                Ok(Type::Bool)
            }
            Binary::Add if matches!(left, Type::Text | Type::BoundedText(_, _)) => Ok(Type::Text),
            Binary::Add | Binary::Subtract | Binary::Multiply | Binary::Divide
                if left == Type::Int =>
            {
                Ok(Type::Int)
            }
            _ => Err(Error::diagnostic(
                "D115",
                a.location,
                "operator is not defined for these types",
            )),
        }
    }

    fn call(
        &mut self,
        callee: &Expr,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        if let ExprKind::Name(name) = &callee.kind {
            return self.named_call(name, args, types, location);
        }
        if let ExprKind::Field(base, method) = &callee.kind
            && let ExprKind::Name(name) = &base.kind
        {
            if name == "host" && !self.locals.contains_key(name) {
                return self.host_call(method, args, types, location);
            }
            if name == "request"
                && method == "is_user"
                && self.route
                && types.is_empty()
                && !self.locals.contains_key(name)
            {
                let actual = self.expr(one(args, location)?)?;
                let expected = Type::Id("user".into());
                if actual != expected {
                    return Err(mismatch(location, &expected, &actual));
                }
                return Ok(Type::Bool);
            }
            if name == "request"
                && !self.locals.contains_key(name)
                && method == "form"
                && self.route
                && args.is_empty()
                && types.len() == 1
            {
                let ty = resolve_type(&types[0], self.program, location)?;
                let Type::Record(record) = &ty else {
                    return Err(Error::diagnostic(
                        "D117",
                        location,
                        "typed forms require a record type",
                    ));
                };
                self.check_form(record, location)?;
                return Ok(Type::Result(Box::new(ty)));
            }
            if let Some((resource, record)) = self
                .program
                .resources
                .iter()
                .find(|(resource, _)| resource.to_ascii_lowercase() == *name)
            {
                if !types.is_empty() {
                    return Err(Error::unsupported(location, "repository generic calls"));
                }
                return self.repository(resource, record, method, args, location);
            }
        }
        Err(Error::unsupported(
            location,
            "dynamic calls, closures, or this method; callable authority cannot be erased",
        ))
    }

    fn host_call(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        if !self.program.host_operations {
            return Err(Error::unsupported(
                location,
                "host operations require a server project module",
            ));
        }
        if matches!(method, "secret" | "http_get" | "http_post") {
            return self.integration_call(method, args, types, location);
        }
        if matches!(
            method,
            "delivery_schedule" | "delivery_query" | "delivery_cancel" | "delivery_command"
        ) {
            return self.delivery_call(method, args, types, location);
        }
        if matches!(method, "ops_command" | "ops_query" | "ops_rate") {
            return self.operations_call(method, args, types, location);
        }
        if matches!(
            method,
            "git_create"
                | "git_fork"
                | "git_merge"
                | "git_integrate"
                | "git_query"
                | "git_review_gate"
                | "git_protect"
                | "git_unprotect"
                | "git_gate"
                | "git_deploy_key"
                | "git_revoke_deploy_key"
                | "git_certificate"
                | "git_revoke_certificate"
                | "git_events"
                | "git_ack_event"
        ) {
            return self.git_call(method, args, types, location);
        }
        if [
            "job_submit",
            "job_status",
            "job_cancel",
            "event_emit",
            "job_key",
            "workflow_submit",
            "workflow_status",
            "workflow_state",
            "workflow_cancel",
        ]
        .contains(&method)
        {
            if method.starts_with("workflow_") {
                return self.workflow_call(method, args, types, location);
            }
            return self.durable_call(method, args, types, location);
        }
        if ["mail_submit", "mail_status", "mail_cancel", "mail_suppress"].contains(&method) {
            return self.mail_call(method, args, types, location);
        }
        let operation = crate::host::operation(method).ok_or_else(|| {
            Error::diagnostic("D310", location, "unknown registered host operation")
        })?;
        if !types.is_empty()
            || args.len() != operation.arguments.len()
            || args.iter().any(|(name, _)| name.is_some())
        {
            return Err(Error::diagnostic(
                "D310",
                location,
                "host arguments must match the registered positional signature",
            ));
        }
        if matches!(self.transaction_context, TransactionContext::Inside) {
            return Err(Error::unsupported(
                location,
                "this host operation cannot execute inside a database transaction",
            ));
        }
        for ((_, expression), expected) in args.iter().zip(&operation.arguments) {
            let actual = self.expr(expression)?;
            if !expected.accepts(&actual) {
                return Err(mismatch(expression.location, expected, &actual));
            }
        }
        self.effects
            .insert(Effect::Host(operation.capability.into()));
        Ok(operation.result)
    }

    fn project_intrinsic(
        &mut self,
        name: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Option<Type>> {
        if name == "id_text" {
            let ty = self.expr(one(args, location)?)?;
            if !matches!(ty, Type::Id(_)) {
                return Err(Error::diagnostic(
                    "D117",
                    location,
                    "id_text requires a nominal Id",
                ));
            }
            return Ok(Some(Type::Text));
        }
        if name == "join_text" {
            if args.len() != 2 || args.iter().any(|(name, _)| name.is_some()) {
                return Err(Error::diagnostic(
                    "D117",
                    location,
                    "join_text requires List<Text> and Text",
                ));
            }
            let values = self.expr(&args[0].1)?;
            let separator = self.expr(&args[1].1)?;
            if !Type::List(Box::new(Type::Text)).accepts(&values) || !Type::Text.accepts(&separator)
            {
                return Err(Error::diagnostic(
                    "D117",
                    location,
                    "join_text requires List<Text> and Text",
                ));
            }
            return Ok(Some(Type::Text));
        }
        Ok(None)
    }

    fn named_call(
        &mut self,
        name: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        if !types.is_empty() {
            return Err(Error::unsupported(location, "generic function calls"));
        }
        if name == "stream" {
            return self.stream_call(args, location);
        }
        if crate::action::constructor(name) {
            return self.action_constructor(name, args, location);
        }
        if crate::standard::is_builtin(name) {
            return self.standard_call(name, args, location);
        }
        if self.program.host_operations
            && let Some(ty) = self.project_intrinsic(name, args, location)?
        {
            return Ok(ty);
        }
        if ["route_url", "None", "len", "error_message", "bad_request"].contains(&name)
            || self.program.variant(name).is_some()
        {
            return self.constructor_call(name, args, location);
        }
        if self.program.records.contains_key(name) {
            return self.record(name, args, location);
        }
        if let Some(Type::Id(tag)) = self.program.aliases.get(name) {
            let argument = one(args, location)?;
            if self.program.host_operations && self.expr(argument)? == Type::Text {
                return Ok(Type::Id(tag.clone()));
            }
            if let ExprKind::Text(value) = &argument.kind {
                if !crate::auth::valid_id(value) {
                    return Err(Error::diagnostic(
                        "D117",
                        location,
                        "invalid Id literal; use ASCII letters, digits, underscores, or hyphens",
                    ));
                }
                return Ok(Type::Id(tag.clone()));
            }
            return Err(Error::unsupported(
                location,
                "dynamic Id construction outside typed input parsers",
            ));
        }
        if ["Ok", "Err", "Some"].contains(&name) {
            let ty = self.expr(one(args, location)?)?;
            return match name {
                "Ok" => Ok(Type::Success(Box::new(ty))),
                "Some" => Ok(Type::Option(Box::new(ty))),
                _ if matches!(ty, Type::Text | Type::DataError) => {
                    Ok(Type::Result(Box::new(Type::Never)))
                }
                _ if matches!(ty, Type::Enum(_)) => {
                    Ok(Type::AppResult(Box::new(Type::Never), Box::new(ty)))
                }
                _ => Err(mismatch(location, &Type::Text, &ty)),
            };
        }
        let function = self.program.functions.get(name).ok_or_else(|| {
            Error::diagnostic("D116", location, format!("unknown function {name}"))
        })?;
        if matches!(self.transaction_context, TransactionContext::Inside)
            && (function.opens_transaction
                || function
                    .effects
                    .iter()
                    .any(|e| matches!(e, Effect::Host(_))))
        {
            return Err(Error::unsupported(
                location,
                format!("call to {name} can open a nested transaction block"),
            ));
        }
        if args.len() != function.parameters.len() || args.iter().any(|(name, _)| name.is_some()) {
            return Err(Error::diagnostic(
                "D116",
                location,
                "function arguments must match the declared positional parameters",
            ));
        }
        for ((_, value), (_, expected)) in args.iter().zip(&function.parameters) {
            let actual = self.expr(value)?;
            if !expected.accepts(&actual) {
                return Err(mismatch(value.location, expected, &actual));
            }
        }
        self.effects.extend(function.effects.iter().cloned());
        self.opens_transaction |= function.opens_transaction;
        self.calls.insert(name.into());
        Ok(function.result.clone())
    }

    fn stream_call(&mut self, args: &[(Option<String>, Expr)], location: Location) -> Result<Type> {
        let [(_, channel), (_, parent)] = args else {
            return Err(Error::diagnostic(
                "D117",
                location,
                "stream requires a static channel and parent Text",
            ));
        };
        let ExprKind::Text(channel) = &channel.kind else {
            return Err(Error::diagnostic(
                "D117",
                location,
                "stream channel must be a static name",
            ));
        };
        if args.iter().any(|(name, _)| name.is_some())
            || !crate::project::resolve::identifier(channel)
            || !Type::Text.accepts(&self.expr(parent)?)
        {
            return Err(Error::diagnostic(
                "D117",
                location,
                "stream requires a static channel and parent Text",
            ));
        }
        Ok(Type::Stream(channel.clone()))
    }

    fn standard_call(
        &mut self,
        name: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Type> {
        let types = args
            .iter()
            .map(|(named, expr)| {
                if named.is_some() {
                    return Err(Error::diagnostic(
                        "D117",
                        location,
                        "utilities take positional arguments",
                    ));
                }
                self.expr(expr)
            })
            .collect::<Result<Vec<_>>>()?;
        crate::standard::signature(name, &types).ok_or_else(|| {
            Error::diagnostic("D117", location, "utility argument types do not match")
        })
    }

    fn check_form(&self, name: &str, location: Location) -> Result<()> {
        for (name, field) in &self.program.records[name].fields {
            if name == "_diamond_csrf" || field.created_by {
                return Err(Error::diagnostic(
                    "D117",
                    location,
                    "forms cannot contain reserved CSRF or server-derived fields",
                ));
            }
            if crate::scalar_input::Scalar::checked(&field.ty, self.program).is_none() {
                return Err(Error::unsupported(
                    location,
                    format!("form field {name} of type {}", field.ty),
                ));
            }
        }
        Ok(())
    }

    fn constructor_call(
        &mut self,
        name: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Type> {
        if name == "route_url" {
            return self.route_url(args, location);
        }
        if name == "None" && args.is_empty() {
            return Ok(Type::Option(Box::new(Type::Never)));
        }
        if name == "len" {
            let actual = self.expr(one(args, location)?)?;
            if !matches!(actual, Type::List(_) | Type::Text | Type::BoundedText(_, _)) {
                return Err(Error::diagnostic(
                    "D124",
                    location,
                    "len requires List or Text",
                ));
            }
            return Ok(Type::Int);
        }
        if name == "error_message" {
            let actual = self.expr(one(args, location)?)?;
            if actual != Type::DataError {
                return Err(mismatch(location, &Type::DataError, &actual));
            }
            return Ok(Type::Text);
        }
        if name == "bad_request" {
            let actual = self.expr(one(args, location)?)?;
            if actual != Type::Html {
                return Err(mismatch(location, &Type::Html, &actual));
            }
            return Ok(Type::Response);
        }
        if let Some((enumeration, fields)) = self.program.variant(name) {
            if fields.len() != args.len() || args.iter().any(|(name, _)| name.is_some()) {
                return Err(Error::diagnostic(
                    "D122",
                    location,
                    "variant constructor needs its positional payloads",
                ));
            }
            for ((_, value), (_, expected)) in args.iter().zip(fields) {
                let actual = self.expr(value)?;
                if !expected.accepts(&actual)
                    && !constant(value).is_ok_and(|value| value.fits(expected))
                {
                    return Err(mismatch(value.location, expected, &actual));
                }
            }
            return Ok(Type::Enum(enumeration.into()));
        }
        Err(Error::diagnostic(
            "D116",
            location,
            "invalid constructor arguments",
        ))
    }

    fn action_constructor(
        &mut self,
        name: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Type> {
        if args.iter().any(|(name, _)| name.is_some()) {
            return Err(Error::diagnostic(
                "D122",
                location,
                "action variants use positional payloads",
            ));
        }
        let types = args
            .iter()
            .map(|(_, value)| self.expr(value))
            .collect::<Result<Vec<_>>>()?;
        crate::action::signature(name, &types)
            .ok_or_else(|| Error::diagnostic("D122", location, "invalid action variant payload"))
    }

    fn record(
        &mut self,
        name: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Type> {
        let record = &self.program.records[name];
        let mut seen = BTreeSet::new();
        for (field, value) in args {
            let field = field.as_ref().ok_or_else(|| {
                Error::diagnostic("D118", location, "record constructors use named fields")
            })?;
            let expected = record.fields.get(field).ok_or_else(|| {
                Error::diagnostic("D118", location, format!("unknown field {field}"))
            })?;
            if expected.created_by || !seen.insert(field.clone()) {
                return Err(Error::diagnostic(
                    "D118",
                    location,
                    "duplicate or server-derived field in record constructor",
                ));
            }
            let actual = self.expr(value)?;
            let literal_fits = constant(value).is_ok_and(|value| value.fits(&expected.ty));
            if !expected.ty.accepts(&actual) && !literal_fits {
                return Err(mismatch(value.location, &expected.ty, &actual));
            }
        }
        for (name, field) in &record.fields {
            if !seen.contains(name) && field.default.is_none() {
                return Err(Error::diagnostic(
                    "D118",
                    location,
                    format!("missing constructor field {name}"),
                ));
            }
        }
        Ok(Type::Record(name.into()))
    }

    fn repository(
        &mut self,
        resource: &str,
        record: &str,
        method: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Type> {
        if self.program.storage.is_some()
            && ["select", "page", "count", "sum", "join", "cursor"].contains(&method)
        {
            let shape = crate::relational::query::checked(self.program, resource, method, args)?;
            for ((_, expr), expected) in args.iter().zip(&shape.arguments) {
                let actual = self.expr(expr)?;
                if !expected.accepts(&actual) && !constant(expr).is_ok_and(|v| v.fits(expected)) {
                    return Err(mismatch(expr.location, expected, &actual));
                }
            }
            for resource in shape.reads {
                self.effects.insert(Effect::Read(resource));
            }
            return Ok(Type::Result(Box::new(shape.result)));
        }
        if method == "select" {
            return self.selection(resource, record, args, location);
        }
        let action = match method {
            "get" => Action::Read,
            "create" => Action::Create,
            "update" => Action::Update,
            "delete" => Action::Delete,
            _ => {
                return Err(Error::unsupported(
                    location,
                    format!("repository operation {method}"),
                ));
            }
        };
        let policy = self.program.policies.get(resource).ok_or_else(|| {
            Error::diagnostic(
                "D204",
                location,
                format!("protected resource {resource} has no policy"),
            )
        })?;
        if !policy.has(action) {
            return Err(Error::diagnostic(
                "D204",
                location,
                format!("{resource}.{method} requires its authorization policy"),
            ));
        }
        let expected_len = if action == Action::Update { 2 } else { 1 };
        if args.len() != expected_len || args.iter().any(|(name, _)| name.is_some()) {
            return Err(Error::diagnostic(
                "D119",
                location,
                "repository argument count or shape does not match the operation",
            ));
        }
        let id = &self.program.records[record].fields["id"].ty;
        if action != Action::Create {
            let actual = self.expr(&args[0].1)?;
            if !id.accepts(&actual) {
                return Err(mismatch(location, id, &actual));
            }
        }
        if matches!(action, Action::Create | Action::Update) {
            let index = usize::from(action == Action::Update);
            let actual = self.expr(&args[index].1)?;
            let Type::Record(input) = actual else {
                return Err(Error::diagnostic(
                    "D119",
                    location,
                    "repository input must be a typed record",
                ));
            };
            self.check_input(record, &input, action, location)?;
            if !policy.has(Action::Read) {
                return Err(Error::diagnostic(
                    "D204",
                    location,
                    "returning a created or updated record also requires its read policy",
                ));
            }
        }
        if action == Action::Read || matches!(action, Action::Create | Action::Update) {
            self.effects.insert(Effect::Read(resource.into()));
        }
        if action != Action::Read {
            self.effects.insert(Effect::Write(resource.into()));
        }
        Ok(Type::Result(Box::new(if action == Action::Delete {
            Type::Unit
        } else {
            Type::Record(record.into())
        })))
    }

    fn selection(
        &mut self,
        resource: &str,
        record: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<Type> {
        if !self
            .program
            .policies
            .get(resource)
            .is_some_and(|policy| policy.has(Action::Read))
        {
            return Err(Error::diagnostic(
                "D204",
                location,
                "selection requires the resource read policy",
            ));
        }
        let mut seen = BTreeSet::new();
        for (name, value) in args {
            let name = name.as_ref().ok_or_else(|| {
                Error::diagnostic(
                    "D125",
                    location,
                    "selection uses named equality filters, limit, and offset",
                )
            })?;
            if !seen.insert(name.clone()) {
                return Err(duplicate(location, name));
            }
            let expected = if ["limit", "offset"].contains(&name.as_str()) {
                &Type::Int
            } else {
                &self.program.records[record]
                    .fields
                    .get(name)
                    .ok_or_else(|| {
                        Error::diagnostic(
                            "D125",
                            location,
                            "unknown selection field or unsupported query shape",
                        )
                    })?
                    .ty
            };
            if !matches!(
                expected,
                Type::Int
                    | Type::Bool
                    | Type::Text
                    | Type::BoundedText(_, _)
                    | Type::Id(_)
                    | Type::Option(_)
            ) {
                return Err(Error::unsupported(location, "non-scalar selection filter"));
            }
            let actual = self.expr(value)?;
            if !expected.accepts(&actual)
                && !constant(value).is_ok_and(|value| value.fits(expected))
            {
                return Err(mismatch(value.location, expected, &actual));
            }
        }
        if !seen.contains("limit") {
            return Err(Error::diagnostic(
                "D125",
                location,
                "selection requires an explicit limit (1..100)",
            ));
        }
        self.effects.insert(Effect::Read(resource.into()));
        Ok(Type::Result(Box::new(Type::List(Box::new(Type::Record(
            record.into(),
        ))))))
    }

    fn check_input(
        &self,
        record: &str,
        input: &str,
        action: Action,
        location: Location,
    ) -> Result<()> {
        let target = &self.program.records[record];
        let input = &self.program.records[input];
        for (name, field) in &input.fields {
            let expected = target.fields.get(name).ok_or_else(|| {
                Error::diagnostic(
                    "D119",
                    location,
                    format!("unknown repository input field {name}"),
                )
            })?;
            if name == "id" || expected.created_by {
                return Err(Error::diagnostic(
                    "D119",
                    location,
                    "repository input cannot supply id or server-derived fields",
                ));
            }
            if !expected.ty.accepts(&field.ty) {
                return Err(mismatch(location, &expected.ty, &field.ty));
            }
        }
        if action == Action::Create {
            for (name, field) in &target.fields {
                if name != "id"
                    && !field.created_by
                    && field.default.is_none()
                    && !input.fields.contains_key(name)
                {
                    return Err(Error::diagnostic(
                        "D119",
                        location,
                        format!("create input is missing {name}"),
                    ));
                }
            }
        }
        Ok(())
    }

    fn route_url(&mut self, args: &[(Option<String>, Expr)], location: Location) -> Result<Type> {
        let Some((None, handle)) = args.first() else {
            return Err(Error::diagnostic(
                "D120",
                location,
                "route_url needs a named route handle",
            ));
        };
        let ExprKind::Name(name) = &handle.kind else {
            return Err(Error::diagnostic(
                "D120",
                location,
                "route handle must be a declaration name",
            ));
        };
        let route = self
            .program
            .routes
            .iter()
            .find(|route| route.name.as_ref() == Some(name))
            .ok_or_else(|| {
                Error::diagnostic("D120", location, format!("unknown route handle {name}"))
            })?;
        let parameters: Vec<_> = route
            .segments
            .iter()
            .filter_map(|segment| {
                if let Segment::Parameter(_, ty) = segment {
                    Some(ty)
                } else {
                    None
                }
            })
            .collect();
        if args.len() != parameters.len() + 1 || args.iter().any(|(name, _)| name.is_some()) {
            return Err(Error::diagnostic(
                "D120",
                location,
                "route_url parameters do not match its route",
            ));
        }
        for ((_, value), expected) in args[1..].iter().zip(parameters) {
            let actual = self.expr(value)?;
            if !expected.accepts(&actual) {
                return Err(mismatch(value.location, expected, &actual));
            }
        }
        Ok(Type::Url)
    }

    fn html(&mut self, nodes: &[HtmlNode]) -> Result<()> {
        for node in nodes {
            match node {
                HtmlNode::Text(_) => {}
                HtmlNode::Expression(value) => {
                    let ty = self.expr(value)?;
                    if !matches!(
                        ty,
                        Type::Text
                            | Type::BoundedText(_, _)
                            | Type::Int
                            | Type::Bool
                            | Type::Id(_)
                            | Type::Html
                    ) && !matches!(&ty, Type::List(inner) if **inner == Type::Html)
                    {
                        return Err(Error::diagnostic(
                            "D121",
                            value.location,
                            "HTML content requires text, a scalar, or typed HTML",
                        ));
                    }
                }
                HtmlNode::Element {
                    attributes,
                    children,
                    ..
                } => {
                    for (name, attribute) in attributes {
                        if let HtmlAttribute::Expression(value) = attribute {
                            let ty = self.expr(value)?;
                            let valid = if ["href", "action"].contains(&name.as_str()) {
                                ty == Type::Url
                            } else {
                                matches!(
                                    ty,
                                    Type::Text
                                        | Type::BoundedText(_, _)
                                        | Type::Int
                                        | Type::Bool
                                        | Type::Id(_)
                                )
                            };
                            if !valid {
                                return Err(Error::diagnostic(
                                    "D121",
                                    value.location,
                                    format!("unsafe value for attribute {name}"),
                                ));
                            }
                        }
                    }
                    self.html(children)?;
                }
            }
        }
        Ok(())
    }
}

fn one(args: &[(Option<String>, Expr)], location: Location) -> Result<&Expr> {
    if args.len() == 1 && args[0].0.is_none() {
        Ok(&args[0].1)
    } else {
        Err(Error::diagnostic(
            "D116",
            location,
            "expected one positional argument",
        ))
    }
}

pub(crate) fn expression_type(
    program: &CheckedProgram,
    locals: BTreeMap<String, Type>,
    expression: &Expr,
) -> Result<Type> {
    let mut checker = Checker::new(program, false, false);
    checker.locals = locals;
    checker.expr(expression)
}
