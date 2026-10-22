//! Bounded reference evaluation of sealed checked programs.

use crate::authority::Scope;
use crate::data::Database;
use crate::error::{Error, Result};
use crate::hir::{CheckedProgram, Segment};
use crate::syntax::{
    Binary, Expr, ExprKind, HtmlAttribute, HtmlNode, Pattern, Statement, StatementKind, Unary,
};
use crate::value::{Failure, Type, VALUE_NODE_WEIGHT, Value, ValueBudget, ValueKind};
use std::collections::BTreeMap;

fn pattern_matches(pattern: &Pattern, value: &Value) -> bool {
    match (pattern, &value.0) {
        (Pattern::Binding(_), _) => true,
        (Pattern::Bool(expected), ValueKind::Bool(actual)) => expected == actual,
        (Pattern::Constructor(name, names), ValueKind::Variant(_, tag, values)) => {
            name == tag && names.len() == values.len()
        }
        (Pattern::Constructor(name, names), ValueKind::Ok(_) | ValueKind::Some(_)) => {
            names.len() == 1
                && matches!(
                    (name.as_str(), &value.0),
                    ("Ok", ValueKind::Ok(_)) | ("Some", ValueKind::Some(_))
                )
        }
        (Pattern::Constructor(name, names), ValueKind::Err(_) | ValueKind::AppErr(_)) => {
            name == "Err" && names.len() == 1
        }
        (Pattern::Constructor(name, names), ValueKind::None) => name == "None" && names.is_empty(),
        _ => false,
    }
}

fn pattern_bindings(pattern: &Pattern, value: Value) -> Option<BTreeMap<String, Value>> {
    if let Pattern::Binding(name) = pattern {
        return Some(if name == "_" {
            BTreeMap::new()
        } else {
            [(name.clone(), value)].into()
        });
    }
    let (names, values) = match (pattern, value.0) {
        (Pattern::Bool(expected), ValueKind::Bool(actual)) if *expected == actual => {
            return Some(BTreeMap::new());
        }
        (Pattern::Constructor(name, names), ValueKind::Variant(_, tag, values)) if *name == tag => {
            (names, values)
        }
        (Pattern::Constructor(name, names), ValueKind::Ok(value)) if name == "Ok" => {
            (names, vec![*value])
        }
        (Pattern::Constructor(name, names), ValueKind::AppErr(value)) if name == "Err" => {
            (names, vec![*value])
        }
        (Pattern::Constructor(name, names), ValueKind::Err(failure)) if name == "Err" => {
            (names, vec![Value(ValueKind::DataError(failure))])
        }
        (Pattern::Constructor(name, names), ValueKind::Some(value)) if name == "Some" => {
            (names, vec![*value])
        }
        (Pattern::Constructor(name, names), ValueKind::None) if name == "None" => {
            (names, Vec::new())
        }
        _ => return None,
    };
    if names.len() != values.len() {
        return None;
    }
    Some(
        names
            .iter()
            .cloned()
            .zip(values)
            .filter(|(name, _)| name != "_")
            .collect(),
    )
}

pub(crate) struct Request<'a> {
    pub body: &'a [u8],
    pub content_type: Option<&'a str>,
    pub csrf: Option<&'a str>,
}

pub(crate) struct Interpreter<'a, 'scope> {
    host: Option<&'a mut crate::host::HostRuntime>,
    postgres: Option<&'a mut crate::project::storage::PostgresStore>,
    program: &'a CheckedProgram,
    scope: &'a Scope<'scope>,
    database: Option<&'a mut Database>,
    request: Option<Request<'a>>,
    fuel: usize,
    call_depth: usize,
    values: ValueBudget,
}

impl<'a, 'scope> Interpreter<'a, 'scope> {
    pub fn new(
        program: &'a CheckedProgram,
        scope: &'a Scope<'scope>,
        database: Option<&'a mut Database>,
        request: Option<Request<'a>>,
    ) -> Self {
        Self {
            host: None,
            postgres: None,
            program,
            scope,
            database,
            request,
            fuel: 100_000,
            call_depth: 0,
            values: ValueBudget::new(),
        }
    }

    pub fn with_host(mut self, host: &'a mut crate::host::HostRuntime) -> Self {
        self.host = Some(host);
        self
    }

    pub(crate) fn with_postgres(
        mut self,
        store: &'a mut crate::project::storage::PostgresStore,
    ) -> Self {
        self.postgres = Some(store);
        self
    }

    fn step(&mut self) -> Result<()> {
        if let Some(store) = self.postgres.as_deref() {
            store.check_deadline()?;
        }
        self.fuel = self
            .fuel
            .checked_sub(1)
            .ok_or(Error::Limit("evaluation steps"))?;
        Ok(())
    }

    pub fn main(&mut self) -> Result<Value> {
        self.function("main", &[])
    }

    pub(crate) fn function(&mut self, name: &str, arguments: &[Value]) -> Result<Value> {
        self.step()?;
        if self.call_depth >= 64 {
            return Err(Error::Limit("call depth"));
        }
        let program = self.program;
        let function = program
            .functions
            .get(name)
            .ok_or_else(|| Error::Input(format!("missing function {name}")))?;
        if function.parameters.len() != arguments.len() {
            return Err(Error::Input("function argument count mismatch".into()));
        }
        for effect in &function.effects {
            self.scope.require(effect)?;
        }
        let mut locals = BTreeMap::new();
        for ((name, ty), value) in function.parameters.iter().zip(arguments) {
            if !value.fits(ty) {
                return Err(Error::Input(
                    "function argument violates type bounds".into(),
                ));
            }
            locals.insert(name.clone(), self.values.copy(value)?);
        }
        self.call_depth += 1;
        let result = self.block(&function.body, &mut locals);
        self.call_depth -= 1;
        let result = match result {
            Err(Error::Application(value)) if matches!(function.result, Type::AppResult(_, _)) => {
                Ok(Value(ValueKind::AppErr(Box::new(value))))
            }
            Err(error) if matches!(function.result, Type::Result(_)) => {
                Ok(Value(ValueKind::Err(Failure::from_error(error)?)))
            }
            other => other,
        }?;
        if !result.fits(&function.result) {
            return Err(Error::Input(
                "function result violates its declared type".into(),
            ));
        }
        Ok(result)
    }

    pub fn block(
        &mut self,
        statements: &[Statement],
        locals: &mut BTreeMap<String, Value>,
    ) -> Result<Value> {
        let mut result = Value(ValueKind::Unit);
        for statement in statements {
            self.step()?;
            result = match &statement.kind {
                StatementKind::Let(name, value) => {
                    let value = self.expr(value, locals)?;
                    locals.insert(name.clone(), value);
                    Value(ValueKind::Unit)
                }
                StatementKind::Expression(value) => self.expr(value, locals)?,
                StatementKind::Return(value) => return self.expr(value, locals),
                StatementKind::Redirect(value) => {
                    let value = self.expr(value, locals)?.display_text()?;
                    crate::template::validate_internal_url(&value, statement.location)?;
                    Value(ValueKind::Url(value))
                }
                StatementKind::Transaction(body) => {
                    let mut nested = self.values.copy_fields(locals)?;
                    if let Some(store) = self.postgres.as_deref_mut() {
                        store.begin()?;
                    } else {
                        self.database.as_deref_mut().ok_or(Error::Denied)?.begin()?;
                    }
                    let result = self
                        .block(body, &mut nested)
                        .and_then(|value| match value.0 {
                            ValueKind::Err(failure) => Err(failure.into_error()),
                            ValueKind::AppErr(value) => Err(Error::Application(*value)),
                            _ => Ok(value),
                        });
                    if let Some(store) = self.postgres.as_deref_mut() {
                        store.finish(result)?
                    } else {
                        self.database
                            .as_deref_mut()
                            .ok_or(Error::Denied)?
                            .finish(result)?
                    }
                }
            };
        }
        Ok(result)
    }

    pub(crate) fn expr(
        &mut self,
        expression: &Expr,
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        self.step()?;
        let result = match &expression.kind {
            ExprKind::Int(value) => Value(ValueKind::Int(*value)),
            ExprKind::Bool(value) => Value(ValueKind::Bool(*value)),
            ExprKind::Text(value) => Value(ValueKind::Text(value.clone())),
            ExprKind::Name(name) if name == "None" => Value(ValueKind::None),
            ExprKind::Name(name) => {
                if let Some(value) = locals.get(name) {
                    self.values.copy(value)?
                } else if let Some((enumeration, fields)) = self.program.variant(name) {
                    if !fields.is_empty() {
                        return Err(Error::Denied);
                    }
                    Value(ValueKind::Variant(
                        enumeration.into(),
                        name.clone(),
                        Vec::new(),
                    ))
                } else {
                    return Err(Error::Input(format!("missing runtime value {name}")));
                }
            }
            ExprKind::Role(_) => return Err(Error::Denied),
            ExprKind::Unary(operator, value) => match (operator, self.expr(value, locals)?.0) {
                (Unary::Not, ValueKind::Bool(value)) => Value(ValueKind::Bool(!value)),
                (Unary::Negate, ValueKind::Int(value)) => Value(ValueKind::Int(
                    value
                        .checked_neg()
                        .ok_or_else(|| Error::Input("integer overflow".into()))?,
                )),
                _ => return Err(Error::Input("invalid unary operand".into())),
            },
            ExprKind::Binary(a, operator, b) => self.binary(a, *operator, b, locals)?,
            ExprKind::Field(base, field) => {
                if matches!(&base.kind, ExprKind::Name(name) if name == "request")
                    && !locals.contains_key("request")
                {
                    match field.as_str() {
                        "authenticated" => {
                            return Ok(Value(ValueKind::Bool(
                                self.scope.principal().id().is_some(),
                            )));
                        }
                        "user_id" => {
                            return Ok(Value(ValueKind::Id(
                                "user".into(),
                                self.scope.principal().id().ok_or(Error::Denied)?.into(),
                            )));
                        }
                        _ => {}
                    }
                }
                let ValueKind::Record(_, fields) = self.expr(base, locals)?.0 else {
                    return Err(Error::Denied);
                };
                self.values.copy(fields.get(field).ok_or(Error::Denied)?)?
            }
            ExprKind::Call {
                callee,
                arguments,
                types,
            } => self.call(callee, arguments, types, locals)?,
            ExprKind::Try(value) => match self.expr(value, locals)?.0 {
                ValueKind::Ok(value) => *value,
                ValueKind::Err(failure) => return Err(failure.into_error()),
                ValueKind::AppErr(value) => return Err(Error::Application(*value)),
                _ => return Err(Error::Denied),
            },
            ExprKind::Html(nodes) => {
                let mut output = String::new();
                self.html(nodes, locals, &mut output)?;
                Value(ValueKind::Html(output))
            }
            ExprKind::List(values) => {
                let mut output = Vec::with_capacity(values.len());
                let mut weight = VALUE_NODE_WEIGHT;
                for value in values {
                    let value = self.expr(value, locals)?;
                    weight = weight.saturating_add(value.weight()?);
                    ValueBudget::check_weight(weight)?;
                    output.push(value);
                }
                Value(ValueKind::List(output))
            }
            ExprKind::Match(value, arms) => self.match_value(value, arms, locals)?,
            ExprKind::For(name, values, body) => self.iterate(name, values, body, locals)?,
        };
        if matches!(&result.0, ValueKind::Text(value) | ValueKind::Html(value) if value.len() > 1_048_576)
        {
            return Err(Error::Limit("managed text/output size"));
        }
        self.values.charge(&result)?;
        Ok(result)
    }

    fn iterate(
        &mut self,
        name: &str,
        values: &Expr,
        body: &[Statement],
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        Ok({
            let ValueKind::List(values) = self.expr(values, locals)?.0 else {
                return Err(Error::Denied);
            };
            if values.len() > 1000 {
                return Err(Error::Limit("iteration length"));
            }
            let mut output = Vec::with_capacity(values.len());
            let mut weight = VALUE_NODE_WEIGHT;
            for value in values {
                self.step()?;
                let mut nested = self.values.copy_fields(locals)?;
                if name != "_" {
                    nested.insert(name.into(), value);
                }
                let value = self.block(body, &mut nested)?;
                weight = weight.saturating_add(value.weight()?);
                ValueBudget::check_weight(weight)?;
                output.push(value);
            }
            Value(ValueKind::List(output))
        })
    }

    fn match_value(
        &mut self,
        value: &Expr,
        arms: &[crate::syntax::MatchArm],
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        Ok({
            let value = self.expr(value, locals)?;
            let arm = arms
                .iter()
                .find(|arm| pattern_matches(&arm.pattern, &value))
                .ok_or(Error::Denied)?;
            let bindings = pattern_bindings(&arm.pattern, value).ok_or(Error::Denied)?;
            let mut nested = self.values.copy_fields(locals)?;
            nested.extend(bindings);
            self.block(&arm.body, &mut nested)?
        })
    }

    fn binary(
        &mut self,
        a: &Expr,
        operator: Binary,
        b: &Expr,
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        let left = self.expr(a, locals)?;
        if operator == Binary::And && !left.boolean()? {
            return Ok(Value(ValueKind::Bool(false)));
        }
        if operator == Binary::Or && left.boolean()? {
            return Ok(Value(ValueKind::Bool(true)));
        }
        let right = self.expr(b, locals)?;
        let result = match (&left.0, operator, &right.0) {
            (ValueKind::Bool(_), Binary::And | Binary::Or, ValueKind::Bool(value)) => {
                ValueKind::Bool(*value)
            }
            (ValueKind::Text(a), Binary::Add, ValueKind::Text(b)) => {
                if a.len().saturating_add(b.len()) > 1_048_576 {
                    return Err(Error::Limit("text concatenation"));
                }
                let mut value = String::with_capacity(a.len() + b.len());
                value.push_str(a);
                value.push_str(b);
                ValueKind::Text(value)
            }
            (
                _,
                Binary::Equal
                | Binary::NotEqual
                | Binary::Less
                | Binary::LessEqual
                | Binary::Greater
                | Binary::GreaterEqual,
                _,
            ) => ValueKind::Bool(crate::policy::compare(&left, operator, &right)?),
            (ValueKind::Int(a), _, ValueKind::Int(b)) => ValueKind::Int(
                match operator {
                    Binary::Add => a.checked_add(*b),
                    Binary::Subtract => a.checked_sub(*b),
                    Binary::Multiply => a.checked_mul(*b),
                    Binary::Divide => a.checked_div(*b),
                    _ => None,
                }
                .ok_or_else(|| Error::Input("integer overflow or division by zero".into()))?,
            ),
            _ => return Err(Error::Denied),
        };
        Ok(Value(result))
    }

    fn call(
        &mut self,
        callee: &Expr,
        arguments: &[(Option<String>, Expr)],
        types: &[crate::syntax::TypeName],
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        if let ExprKind::Name(name) = &callee.kind {
            return self.named_call(name, arguments, locals);
        }
        if let ExprKind::Field(base, method) = &callee.kind
            && let ExprKind::Name(name) = &base.kind
        {
            if name == "host" && !locals.contains_key(name) {
                let operation = crate::host::operation(method).ok_or(Error::Denied)?;
                self.scope
                    .require(&crate::authority::Effect::Host(operation.capability.into()))?;
                let values = arguments
                    .iter()
                    .map(|(_, value)| self.expr(value, locals))
                    .collect::<Result<Vec<_>>>()?;
                return self
                    .host
                    .as_deref_mut()
                    .ok_or(Error::Denied)?
                    .call(method, &values);
            }
            if name == "request" && method == "is_user" && !locals.contains_key(name) {
                let value = self.expr(&arguments.first().ok_or(Error::Denied)?.1, locals)?;
                let ValueKind::Id(_, id) = value.0 else {
                    return Err(Error::Denied);
                };
                return Ok(Value(ValueKind::Bool(
                    self.scope.principal().id() == Some(id.as_str()),
                )));
            }
            if name == "request" && method == "form" && !locals.contains_key(name) {
                let ty = crate::checker::resolve_type(
                    types.first().ok_or(Error::Denied)?,
                    self.program,
                    callee.location,
                )?;
                let Type::Record(record) = ty else {
                    return Err(Error::Denied);
                };
                let request = self.request.as_ref().ok_or(Error::Denied)?;
                if !crate::forms::is_form(request.content_type) {
                    return Value::operation(Err(Error::Input(
                        "typed forms require application/x-www-form-urlencoded".into(),
                    )));
                }
                return Value::operation(crate::forms::parse(
                    request.body,
                    &record,
                    &self.program.records[&record],
                ));
            }
            return self.repository_call(name, method, arguments, locals);
        }
        Err(Error::Denied)
    }

    fn repository_call(
        &mut self,
        name: &str,
        method: &str,
        arguments: &[(Option<String>, Expr)],
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        let program = self.program;
        let (resource, _) = program
            .resources
            .iter()
            .find(|(resource, _)| resource.to_ascii_lowercase() == *name)
            .ok_or(Error::Denied)?;
        let values = arguments
            .iter()
            .map(|(_, value)| self.expr(value, locals))
            .collect::<Result<Vec<_>>>()?;
        if let Some(store) = self.postgres.as_deref_mut() {
            let result = store.operation(
                self.scope,
                resource,
                method,
                arguments,
                &values,
                &mut self.values,
            );
            if let Err(error) = &result {
                store.mark_failed(error);
            }
            return Value::operation(result);
        }
        let database = self.database.as_deref_mut().ok_or(Error::Denied)?;
        if method == "select" {
            let named = arguments
                .iter()
                .zip(values)
                .map(|((name, _), value)| Ok((name.clone().ok_or(Error::Denied)?, value)))
                .collect::<Result<BTreeMap<_, _>>>()?;
            let result = database.select(self.scope, resource, &named, &mut self.values);
            if result.is_err() {
                database.mark_failed();
            }
            return Value::operation(result);
        }
        let first = values.first().ok_or(Error::Denied)?;
        let result = match method {
            "get" => database.get(self.scope, resource, first, &mut self.values),
            "create" => database.create(self.scope, resource, first, &mut self.values),
            "update" => database.update(
                self.scope,
                resource,
                first,
                values.get(1).ok_or(Error::Denied)?,
                &mut self.values,
            ),
            "delete" => database.delete(self.scope, resource, first, &mut self.values),
            _ => return Err(Error::Denied),
        };
        if result.is_err() {
            database.mark_failed();
        }
        Value::operation(result)
    }

    fn join_text(&mut self, values: &[Value]) -> Result<Value> {
        let (Some(Value(ValueKind::List(parts))), Some(Value(ValueKind::Text(separator)))) =
            (values.first(), values.get(1))
        else {
            return Err(Error::Denied);
        };
        let mut text = String::new();
        for (i, part) in parts.iter().enumerate() {
            let ValueKind::Text(part) = &part.0 else {
                return Err(Error::Denied);
            };
            if i > 0 {
                text.push_str(separator);
            }
            text.push_str(part);
            if text.len() > crate::MAX_SOURCE_BYTES {
                return Err(Error::Limit("joined text"));
            }
        }
        self.values.spend(text.len())?;
        Ok(Value(ValueKind::Text(text)))
    }

    fn named_call(
        &mut self,
        name: &str,
        arguments: &[(Option<String>, Expr)],
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        if name == "route_url" {
            return self.route_url(arguments, locals);
        }
        let values = arguments
            .iter()
            .map(|(_, value)| self.expr(value, locals))
            .collect::<Result<Vec<_>>>()?;
        if let Some((enumeration, fields)) = self.program.variant(name) {
            if !fields
                .iter()
                .zip(&values)
                .all(|((_, ty), value)| value.fits(ty))
            {
                return Err(Error::Input(
                    "variant payload violates its type bounds".into(),
                ));
            }
            return Ok(Value(ValueKind::Variant(
                enumeration.into(),
                name.into(),
                values,
            )));
        }
        if self.program.records.contains_key(name) {
            return self.record_value(name, arguments, values);
        }
        if let Some(Type::Id(tag)) = self.program.aliases.get(name) {
            let value = values.first().ok_or(Error::Denied)?.display_text()?;
            if !crate::auth::valid_id(&value) {
                return Err(Error::Input("invalid Id literal".into()));
            }
            return Ok(Value(ValueKind::Id(tag.clone(), value)));
        }
        if crate::standard::is_builtin(name) {
            return crate::standard::evaluate(name, &values);
        }
        match name {
            "join_text" if self.program.host_operations => self.join_text(&values),
            "id_text" if self.program.host_operations => {
                let ValueKind::Id(_, value) = &values.first().ok_or(Error::Denied)?.0 else {
                    return Err(Error::Denied);
                };
                Ok(Value(ValueKind::Text(value.clone())))
            }
            "Ok" => Ok(Value(ValueKind::Ok(Box::new(
                values.into_iter().next().ok_or(Error::Denied)?,
            )))),
            "Some" => Ok(Value(ValueKind::Some(Box::new(
                values.into_iter().next().ok_or(Error::Denied)?,
            )))),
            "Err" if matches!(values.first(), Some(Value(ValueKind::Variant(..)))) => Ok(Value(
                ValueKind::AppErr(Box::new(values.into_iter().next().ok_or(Error::Denied)?)),
            )),
            "Err" => Ok(Value(ValueKind::Err(
                match values.into_iter().next().ok_or(Error::Denied)?.0 {
                    ValueKind::DataError(failure) => failure,
                    ValueKind::Text(message) => Failure::Message(message),
                    _ => return Err(Error::Denied),
                },
            ))),
            "None" => Ok(Value(ValueKind::None)),
            "bad_request" => {
                let ValueKind::Html(body) = values.into_iter().next().ok_or(Error::Denied)?.0
                else {
                    return Err(Error::Denied);
                };
                Ok(Value(ValueKind::Response(400, body)))
            }
            "len" => {
                let length = match &values.first().ok_or(Error::Denied)?.0 {
                    ValueKind::List(values) => values.len(),
                    ValueKind::Text(value) => value.chars().count(),
                    _ => return Err(Error::Denied),
                };
                Ok(Value(ValueKind::Int(
                    i64::try_from(length).map_err(|_| Error::Limit("length"))?,
                )))
            }
            "error_message" => {
                let ValueKind::DataError(failure) = &values.first().ok_or(Error::Denied)?.0 else {
                    return Err(Error::Denied);
                };
                Ok(Value(ValueKind::Text(failure.message().into())))
            }
            _ => self.function(name, &values),
        }
    }

    fn route_url(
        &mut self,
        arguments: &[(Option<String>, Expr)],
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        let ExprKind::Name(name) = &arguments.first().ok_or(Error::Denied)?.1.kind else {
            return Err(Error::Denied);
        };
        let program = self.program;
        let route = program
            .routes
            .iter()
            .find(|route| route.name.as_ref() == Some(name))
            .ok_or(Error::Denied)?;
        let mut arguments = arguments[1..].iter();
        let mut segments = Vec::new();
        for segment in &route.segments {
            segments.push(match segment {
                Segment::Literal(value) => value.clone(),
                Segment::Parameter(_, _) => self
                    .expr(&arguments.next().ok_or(Error::Denied)?.1, locals)?
                    .display_text()?,
            });
        }
        let url = format!("/{}", segments.join("/"));
        crate::template::validate_internal_url(&url, crate::Location::default())?;
        Ok(Value(ValueKind::Url(url)))
    }

    fn record_value(
        &mut self,
        name: &str,
        arguments: &[(Option<String>, Expr)],
        values: Vec<Value>,
    ) -> Result<Value> {
        let record = &self.program.records[name];
        let mut fields = BTreeMap::new();
        for (field, definition) in &record.fields {
            if let Some(value) = &definition.default {
                fields.insert(field.clone(), self.values.copy(value)?);
            }
        }
        for ((field, _), value) in arguments.iter().zip(values) {
            fields.insert(field.as_ref().ok_or(Error::Denied)?.clone(), value);
        }
        for (name, definition) in &record.fields {
            if !fields
                .get(name)
                .is_some_and(|value| value.fits(&definition.ty))
            {
                return Err(Error::Input("record value violates declared bounds".into()));
            }
        }
        Ok(Value(ValueKind::Record(name.into(), fields)))
    }

    fn html(
        &mut self,
        nodes: &[HtmlNode],
        locals: &BTreeMap<String, Value>,
        output: &mut String,
    ) -> Result<()> {
        use std::fmt::Write;
        for node in nodes {
            self.step()?;
            match node {
                HtmlNode::Text(value) => output.push_str(&crate::template::escape(value)),
                HtmlNode::Expression(value) => {
                    let value = self.expr(value, locals)?;
                    match value.0 {
                        ValueKind::Html(value) => output.push_str(&value),
                        ValueKind::List(values) => {
                            for value in values {
                                let ValueKind::Html(value) = value.0 else {
                                    return Err(Error::Denied);
                                };
                                if output.len().saturating_add(value.len()) > 1_048_576 {
                                    return Err(Error::Limit("HTML output"));
                                }
                                output.push_str(&value);
                            }
                        }
                        _ => output.push_str(&crate::template::escape(&value.display_text()?)),
                    }
                }
                HtmlNode::Element {
                    tag,
                    attributes,
                    children,
                } => {
                    write!(output, "<{tag}").map_err(|_| Error::Limit("HTML output"))?;
                    for (name, value) in attributes {
                        let value = match value {
                            HtmlAttribute::Literal(value) => value.clone(),
                            HtmlAttribute::Expression(value) => {
                                self.expr(value, locals)?.display_text()?
                            }
                        };
                        write!(output, " {name}=\"{}\"", crate::template::escape(&value))
                            .map_err(|_| Error::Limit("HTML output"))?;
                    }
                    output.push('>');
                    if tag == "form" && attributes.iter().any(|(name, value)| name == "method" && matches!(value, HtmlAttribute::Literal(value) if value.eq_ignore_ascii_case("POST"))) {
                        let csrf = self.request.as_ref().and_then(|request| request.csrf).ok_or_else(|| Error::unsupported(crate::Location::default(), "rendering POST forms requires a session-bound CSRF context; configure --auth for this session-enabled application"))?;
                        output.push_str(&crate::forms::csrf_hidden(csrf));
                    }
                    self.html(children, locals, output)?;
                    if !["input", "br", "hr"].contains(&tag.as_str()) {
                        write!(output, "</{tag}>").map_err(|_| Error::Limit("HTML output"))?;
                    }
                }
            }
            if output.len() > 1_048_576 {
                return Err(Error::Limit("HTML output"));
            }
        }
        Ok(())
    }
}
