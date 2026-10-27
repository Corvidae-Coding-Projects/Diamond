//! A small recursive-descent declaration parser and bounded Pratt expressions.

use crate::error::{Error, Location, Result};
use crate::lexer::{Kind, Token, lex};
use crate::syntax::{
    App, Binary, Declaration, Expr, ExprKind, Field, Function, MatchArm, Pattern, Policy,
    PolicyAction, Program, Record, Route, Statement, StatementKind, TypeName, Unary,
};

pub(crate) fn parse(source: &str) -> Result<Program> {
    Parser {
        tokens: lex(source)?,
        cursor: 0,
        expression_depth: 0,
    }
    .program()
}

pub(crate) fn project_module(source: &str) -> Result<crate::syntax::ProjectModule> {
    Parser {
        tokens: lex(source)?,
        cursor: 0,
        expression_depth: 0,
    }
    .module()
}

pub(crate) fn expression(source: &str, location: Location) -> Result<Expr> {
    let mut tokens = lex(source)?;
    for token in &mut tokens {
        let first_line = token.location.line == 1;
        token.location.line += location.line.saturating_sub(1);
        if first_line {
            token.location.column += location.column.saturating_sub(1);
        }
    }
    let mut parser = Parser {
        tokens,
        cursor: 0,
        expression_depth: 0,
    };
    let result = parser.expr(0)?;
    parser.newline()?;
    if !parser.at(&Kind::End) {
        return Err(parser.error("unexpected tokens after expression"));
    }
    Ok(result)
}

struct Parser {
    tokens: Vec<Token>,
    cursor: usize,
    expression_depth: usize,
}

impl Parser {
    fn module(&mut self) -> Result<crate::syntax::ProjectModule> {
        let mut module = crate::syntax::ProjectModule::default();
        let mut names = std::collections::BTreeSet::new();
        while !self.at(&Kind::End) {
            let location = self.token().location;
            let exported = self.is_name("export");
            if exported {
                self.take();
            }
            let keyword = self.name()?;
            let name = match keyword.as_str() {
                "fn" | "action" => {
                    let function = self.function(location)?;
                    let name = function.name.clone();
                    if keyword == "action" {
                        module.actions.insert(name.clone());
                    }
                    module
                        .program
                        .declarations
                        .push(Declaration::Function(function));
                    name
                }
                "route" | "form" => self.reactive_schema(&mut module, &keyword, location)?,
                "component" => {
                    let component = self.component(location)?;
                    let name = component.name.clone();
                    module.components.push(component);
                    name
                }
                "type" | "enum" | "resource" | "policy" => {
                    let declaration = match keyword.as_str() {
                        "type" => self.record_or_alias(location)?,
                        "policy" => Declaration::Policy(self.policy(location)?),
                        "enum" => Declaration::Enum(self.enumeration(location)?),
                        _ => {
                            let resource = self.name()?;
                            self.symbol(":")?;
                            let record = self.name()?;
                            self.newline()?;
                            Declaration::Resource(resource, record, location)
                        }
                    };
                    let declared = match &declaration {
                        Declaration::Alias(name, _, _) => Some(name.clone()),
                        Declaration::Record(record) => Some(record.name.clone()),
                        Declaration::Enum(enumeration) => Some(enumeration.name.clone()),
                        _ => None,
                    };
                    if let Some(name) = declared {
                        if !names.insert(name.clone()) {
                            return Err(self.error("duplicate module declaration"));
                        }
                        if exported {
                            module.exports.insert(name);
                        }
                    } else if exported {
                        return Err(self.error("resources and policies are private"));
                    }
                    module.program.declarations.push(declaration);
                    if module.program.declarations.len() > 4096 {
                        return Err(Error::Limit("module declarations"));
                    }
                    continue;
                }
                _ => {
                    return Err(Error::unsupported(
                        location,
                        "project modules support functions/components and private server type/resource/policy declarations",
                    ));
                }
            };
            if !names.insert(name.clone()) {
                return Err(self.error("duplicate module declaration"));
            }
            if exported {
                module.exports.insert(name);
            }
            if names.len() > 4096 {
                return Err(Error::Limit("module declarations"));
            }
        }
        if names.is_empty() {
            return Err(self.error("project module is empty"));
        }
        Ok(module)
    }

    fn component(&mut self, location: Location) -> Result<crate::syntax::Component> {
        let mut component = crate::syntax::Component {
            name: self.name()?,
            properties: Vec::new(),
            derived: Vec::new(),
            states: Vec::new(),
            events: Vec::new(),
            outputs: Vec::new(),
            template: Vec::new(),
            location,
        };
        if self.is_symbol("(") {
            self.take();
            while !self.is_symbol(")") {
                let name = self.name()?;
                self.symbol(":")?;
                let ty = self.ty(0)?;
                component.properties.push((name, ty));
                if !self.is_symbol(",") {
                    break;
                }
                self.take();
            }
            self.symbol(")")?;
        }
        self.block_start()?;
        let mut has_template = false;
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            if has_template {
                return Err(self.error("html must end a component"));
            }
            if let Kind::Html(source) = self.token().kind.clone() {
                let here = self.take().location;
                component.template = crate::template::parse_browser(&source, here)?;
                has_template = true;
                self.newline()?;
            } else if self.is_name("state") || self.is_name("derived") {
                if !component.events.is_empty() {
                    return Err(self.error("state declarations precede event handlers"));
                }
                let derived = self.is_name("derived");
                self.take();
                let name = self.name()?;
                self.symbol(":")?;
                let ty = self.ty(0)?;
                self.symbol("=")?;
                let value = self.expr(0)?;
                self.statement_end()?;
                if derived {
                    component.derived.push((name, ty, value));
                } else {
                    component.states.push((name, ty, value));
                }
            } else if self.is_name("output") {
                let location = self.take().location;
                let name = self.name()?;
                let parameters = self.parameters()?;
                self.newline()?;
                component.outputs.push(crate::syntax::Output {
                    name,
                    parameters,
                    location,
                });
                if component.outputs.len() > 128 {
                    return Err(Error::Limit("component outputs"));
                }
            } else if self.is_name("on") {
                component.events.push(self.component_event()?);
            } else {
                return Err(self.error("expected state, on event, or html in component"));
            }
            if component.states.len() + component.derived.len() + component.properties.len() > 128
                || component.events.len() > 128
            {
                return Err(Error::Limit("component state/events"));
            }
        }
        self.expect(&Kind::Dedent)?;
        if !has_template {
            return Err(self.error("component requires a typed html template"));
        }
        Ok(component)
    }

    fn reactive_schema(
        &mut self,
        module: &mut crate::syntax::ProjectModule,
        keyword: &str,
        location: Location,
    ) -> Result<String> {
        let name = self.name()?;
        self.symbol("(")?;
        let mut fields = Vec::new();
        while !self.is_symbol(")") {
            let field_location = self.token().location;
            let field = self.name()?;
            self.symbol(":")?;
            let ty = self.ty(0)?;
            let default = if self.is_symbol("=") {
                self.take();
                Some(self.expr(0)?)
            } else if matches!(&ty, TypeName::Generic(name, _) if name == "Option") {
                Some(Expr {
                    kind: ExprKind::Name("None".into()),
                    location: field_location,
                })
            } else {
                None
            };
            fields.push(Field {
                name: field,
                ty,
                default,
                created_by: false,
                location: field_location,
            });
            if fields.len() > 32 {
                return Err(Error::Limit("reactive schema fields"));
            }
            if !self.is_symbol(",") {
                break;
            }
            self.take();
        }
        self.symbol(")")?;
        if fields.is_empty() {
            return Err(self.error("reactive schemas require at least one field"));
        }
        if keyword == "route" {
            self.symbol("=")?;
            let Kind::Text(path) = self.take().kind else {
                return Err(self.error("named routes require a literal path"));
            };
            module.navigation.push(crate::syntax::ReactiveRoute {
                name: name.clone(),
                path,
                location,
            });
        } else {
            module.forms.push((name.clone(), location));
        }
        self.newline()?;
        module
            .program
            .declarations
            .push(Declaration::Record(Record {
                name: name.clone(),
                fields,
                location,
            }));
        Ok(name)
    }

    fn component_event(&mut self) -> Result<crate::syntax::Event> {
        let location = self.token().location;
        self.take();
        let name = self.name()?;
        let parameters = self.parameters()?;
        self.block_start()?;
        let mut assignments = Vec::new();
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let step = if self.is_name("emit") {
                let location = self.take().location;
                let expression = self.expr(0)?;
                let ExprKind::Call {
                    callee,
                    arguments,
                    types,
                } = expression.kind
                else {
                    return Err(self.error("emit requires a declared output call"));
                };
                let ExprKind::Name(name) = callee.kind else {
                    return Err(self.error("emit requires a named output"));
                };
                if !types.is_empty() || arguments.iter().any(|(name, _)| name.is_some()) {
                    return Err(self.error("output arguments are positional"));
                }
                crate::syntax::EventStep::Emit(
                    name,
                    arguments.into_iter().map(|(_, e)| e).collect(),
                    location,
                )
            } else {
                let state = self.name()?;
                self.symbol("=")?;
                crate::syntax::EventStep::Assign(state, self.expr(0)?)
            };
            assignments.push(step);
            self.statement_end()?;
            if assignments.len() > 256 {
                return Err(Error::Limit("event assignments"));
            }
        }
        self.expect(&Kind::Dedent)?;
        if assignments.is_empty() {
            return Err(self.error("event handler requires an assignment or emission"));
        }
        Ok(crate::syntax::Event {
            name,
            parameters,
            body: assignments,
            location,
        })
    }

    fn token(&self) -> &Token {
        &self.tokens[self.cursor.min(self.tokens.len() - 1)]
    }

    fn parameters(&mut self) -> Result<Vec<(String, TypeName)>> {
        let mut parameters = Vec::new();
        if self.is_symbol("(") {
            self.take();
            while !self.is_symbol(")") {
                let name = self.name()?;
                self.symbol(":")?;
                parameters.push((name, self.ty(0)?));
                if parameters.len() > 128 {
                    return Err(Error::Limit("parameter count"));
                }
                if !self.is_symbol(",") {
                    break;
                }
                self.take();
            }
            self.symbol(")")?;
        }
        Ok(parameters)
    }
    fn at(&self, kind: &Kind) -> bool {
        &self.token().kind == kind
    }
    fn is_name(&self, name: &str) -> bool {
        self.at(&Kind::Name(name.into()))
    }
    fn is_symbol(&self, symbol: &str) -> bool {
        self.at(&Kind::Symbol(symbol.into()))
    }
    fn take(&mut self) -> Token {
        let result = self.token().clone();
        if self.cursor + 1 < self.tokens.len() {
            self.cursor += 1;
        }
        result
    }
    fn error(&self, message: impl Into<String>) -> Error {
        Error::diagnostic("D010", self.token().location, message)
    }
    fn expect(&mut self, kind: &Kind) -> Result<()> {
        if self.at(kind) {
            self.take();
            Ok(())
        } else {
            Err(self.error(format!("expected {kind:?}, found {:?}", self.token().kind)))
        }
    }
    fn symbol(&mut self, value: &str) -> Result<()> {
        self.expect(&Kind::Symbol(value.into()))
    }
    fn keyword(&mut self, value: &str) -> Result<()> {
        self.expect(&Kind::Name(value.into()))
    }
    fn name(&mut self) -> Result<String> {
        match self.take() {
            Token {
                kind: Kind::Name(value),
                ..
            } => Ok(value),
            token => Err(Error::diagnostic(
                "D010",
                token.location,
                "expected an identifier",
            )),
        }
    }
    fn text(&mut self) -> Result<String> {
        match self.take() {
            Token {
                kind: Kind::Text(value),
                ..
            } => Ok(value),
            token => Err(Error::diagnostic(
                "D010",
                token.location,
                "expected a quoted string",
            )),
        }
    }
    fn newline(&mut self) -> Result<()> {
        self.expect(&Kind::Newline)
    }
    fn block_start(&mut self) -> Result<()> {
        self.symbol(":")?;
        self.newline()?;
        self.expect(&Kind::Indent)
    }

    fn program(&mut self) -> Result<Program> {
        if self.at(&Kind::End) {
            return Err(self.error("expected a declaration; source is empty"));
        }
        let mut declarations = Vec::new();
        while !self.at(&Kind::End) {
            let location = self.token().location;
            let keyword = self.name()?;
            let declaration = match keyword.as_str() {
                "type" => self.record_or_alias(location)?,
                "enum" => Declaration::Enum(self.enumeration(location)?),
                "resource" => {
                    let resource = self.name()?;
                    self.symbol(":")?;
                    let record = self.name()?;
                    self.newline()?;
                    Declaration::Resource(resource, record, location)
                }
                "fn" => Declaration::Function(self.function(location)?),
                "app" => Declaration::App(self.app(location)?),
                "policy" => Declaration::Policy(self.policy(location)?),
                "route" => Declaration::Route(self.route(false, location)?),
                "public" => {
                    self.keyword("route")?;
                    Declaration::Route(self.route(true, location)?)
                }
                _ => {
                    return Err(Error::unsupported(
                        location,
                        format!(
                            "top-level declaration {keyword}; supported declarations are type, enum, resource, fn, app, policy, and route"
                        ),
                    ));
                }
            };
            declarations.push(declaration);
            if declarations.len() > 4096 {
                return Err(Error::Limit("declarations"));
            }
        }
        Ok(Program { declarations })
    }

    fn record_or_alias(&mut self, location: Location) -> Result<Declaration> {
        let name = self.name()?;
        if self.is_symbol("=") {
            self.take();
            let ty = self.ty(0)?;
            self.newline()?;
            return Ok(Declaration::Alias(name, ty, location));
        }
        self.block_start()?;
        let mut fields = Vec::new();
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let location = self.token().location;
            let name = self.name()?;
            self.symbol(":")?;
            let ty = self.ty(0)?;
            let default = if self.is_symbol("=") {
                self.take();
                Some(self.expr(0)?)
            } else {
                None
            };
            let created_by = if self.is_symbol("@") {
                self.take();
                self.keyword("created_by_principal")?;
                true
            } else {
                false
            };
            self.newline()?;
            fields.push(Field {
                name,
                ty,
                default,
                created_by,
                location,
            });
        }
        self.expect(&Kind::Dedent)?;
        Ok(Declaration::Record(Record {
            name,
            fields,
            location,
        }))
    }

    fn ty(&mut self, depth: usize) -> Result<TypeName> {
        if depth >= 32 {
            return Err(Error::Limit("type nesting"));
        }
        let name = self.name()?;
        if !self.is_symbol("<") {
            return Ok(TypeName::Named(name));
        }
        self.take();
        let result = if name == "Secret" {
            TypeName::Secret(self.text()?)
        } else if name == "Blob" {
            TypeName::Blob(self.text()?)
        } else if name == "Stream" {
            TypeName::Stream(self.text()?)
        } else if name == "Id" {
            TypeName::Id(self.text()?)
        } else if name == "Text" && self.is_name("length") {
            self.take();
            self.symbol(":")?;
            let minimum = self.positive_integer()?;
            self.symbol("..")?;
            let maximum = self.positive_integer()?;
            if minimum > maximum {
                return Err(self.error("text length minimum exceeds maximum"));
            }
            TypeName::BoundedText(minimum, maximum)
        } else {
            let mut arguments = vec![self.ty(depth + 1)?];
            while self.is_symbol(",") {
                self.take();
                arguments.push(self.ty(depth + 1)?);
            }
            TypeName::Generic(name, arguments)
        };
        self.symbol(">")?;
        Ok(result)
    }

    fn enumeration(&mut self, location: Location) -> Result<crate::syntax::Enum> {
        let name = self.name()?;
        self.block_start()?;
        let mut variants = Vec::new();
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let variant = self.name()?;
            let mut fields = Vec::new();
            if self.is_symbol("(") {
                self.take();
                while !self.is_symbol(")") {
                    let field = self.name()?;
                    self.symbol(":")?;
                    fields.push((field, self.ty(0)?));
                    if !self.is_symbol(",") {
                        break;
                    }
                    self.take();
                }
                self.symbol(")")?;
            }
            self.newline()?;
            variants.push((variant, fields));
        }
        self.expect(&Kind::Dedent)?;
        Ok(crate::syntax::Enum {
            name,
            variants,
            location,
        })
    }

    fn positive_integer(&mut self) -> Result<usize> {
        match self.take() {
            Token {
                kind: Kind::Int(value),
                location,
            } => usize::try_from(value)
                .map_err(|_| Error::diagnostic("D010", location, "expected a nonnegative size")),
            token => Err(Error::diagnostic(
                "D010",
                token.location,
                "expected a nonnegative size",
            )),
        }
    }

    fn function(&mut self, location: Location) -> Result<Function> {
        let name = self.name()?;
        self.symbol("(")?;
        let mut parameters = Vec::new();
        while !self.is_symbol(")") {
            let parameter = self.name()?;
            self.symbol(":")?;
            parameters.push((parameter, self.ty(0)?));
            if !self.is_symbol(",") {
                break;
            }
            self.take();
        }
        self.symbol(")")?;
        if !self.is_symbol("->") {
            return Err(Error::unsupported(
                location,
                "function return-type inference; annotate this initial function with -> Type",
            ));
        }
        self.take();
        let result = self.ty(0)?;
        let body = self.block()?;
        Ok(Function {
            name,
            parameters,
            result,
            body,
            location,
        })
    }

    fn block(&mut self) -> Result<Vec<Statement>> {
        self.block_start()?;
        self.statements()
    }

    fn statements(&mut self) -> Result<Vec<Statement>> {
        let mut result = Vec::new();
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let location = self.token().location;
            let kind = if self.is_name("let") {
                self.take();
                let name = self.name()?;
                self.symbol("=")?;
                StatementKind::Let(name, self.expr(0)?)
            } else if self.is_name("return") {
                self.take();
                StatementKind::Return(self.expr(0)?)
            } else if self.is_name("redirect") {
                self.take();
                StatementKind::Redirect(self.expr(0)?)
            } else if self.is_name("transaction") {
                self.take();
                self.keyword("db")?;
                result.push(Statement {
                    kind: StatementKind::Transaction(self.block()?),
                    location,
                });
                continue;
            } else if ["var", "if", "spawn", "stream", "websocket", "unsafe"]
                .iter()
                .any(|name| self.is_name(name))
            {
                return Err(Error::unsupported(
                    location,
                    format!("statement {:?}", self.token().kind),
                ));
            } else {
                StatementKind::Expression(self.expr(0)?)
            };
            self.statement_end()?;
            result.push(Statement { kind, location });
        }
        self.expect(&Kind::Dedent)?;
        if result.is_empty() {
            return Err(self.error("empty block"));
        }
        Ok(result)
    }

    fn statement_end(&mut self) -> Result<()> {
        if self.cursor > 0 && self.tokens[self.cursor - 1].kind == Kind::Dedent {
            Ok(())
        } else {
            self.newline()
        }
    }

    fn match_expression(&mut self) -> Result<ExprKind> {
        let value = self.expr(0)?;
        self.block_start()?;
        let mut arms = Vec::new();
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let location = self.token().location;
            let name = self.name()?;
            let pattern = if ["true", "false"].contains(&name.as_str()) {
                Pattern::Bool(name == "true")
            } else if name.chars().next().is_some_and(char::is_uppercase) {
                let mut fields = Vec::new();
                if self.is_symbol("(") {
                    self.take();
                    while !self.is_symbol(")") {
                        fields.push(self.name()?);
                        if !self.is_symbol(",") {
                            break;
                        }
                        self.take();
                    }
                    self.symbol(")")?;
                }
                Pattern::Constructor(name, fields)
            } else {
                Pattern::Binding(name)
            };
            self.symbol("=>")?;
            let body = if self.at(&Kind::Newline) {
                self.newline()?;
                self.expect(&Kind::Indent)?;
                self.statements()?
            } else {
                let value = self.expr(0)?;
                self.statement_end()?;
                vec![Statement {
                    kind: StatementKind::Expression(value),
                    location,
                }]
            };
            arms.push(MatchArm {
                pattern,
                body,
                location,
            });
        }
        self.expect(&Kind::Dedent)?;
        Ok(ExprKind::Match(Box::new(value), arms))
    }

    fn app(&mut self, location: Location) -> Result<App> {
        let mut app = App {
            name: self.name()?,
            location,
            ..App::default()
        };
        self.block_start()?;
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let field = self.name()?;
            match field.as_str() {
                "database" => {
                    self.keyword("SQLite")?;
                    app.database = true;
                }
                "session" => {
                    self.keyword("SecureCookie")?;
                    app.sessions = true;
                }
                "provides" => loop {
                    let effect = self.name()?;
                    self.symbol("<")?;
                    let resource = self.name()?;
                    self.symbol(">")?;
                    app.grants.push((effect, resource));
                    if !self.is_symbol(",") {
                        break;
                    }
                    self.take();
                },
                _ => {
                    return Err(Error::unsupported(
                        self.token().location,
                        format!("application field {field}"),
                    ));
                }
            }
            self.newline()?;
        }
        self.expect(&Kind::Dedent)?;
        Ok(app)
    }

    fn policy(&mut self, location: Location) -> Result<Policy> {
        let resource = self.name()?;
        self.block_start()?;
        let mut actions = Vec::new();
        while !self.at(&Kind::Dedent) && !self.at(&Kind::End) {
            let location = self.token().location;
            let name = self.name()?;
            self.symbol("(")?;
            let mut bindings = Vec::new();
            while !self.is_name("by") {
                bindings.push(self.name()?);
                self.symbol(",")?;
            }
            self.keyword("by")?;
            let principal = self.name()?;
            self.symbol(")")?;
            self.block_start()?;
            let expression = self.expr(0)?;
            self.newline()?;
            self.expect(&Kind::Dedent)?;
            actions.push(PolicyAction {
                name,
                bindings,
                principal,
                expression,
                location,
            });
        }
        self.expect(&Kind::Dedent)?;
        Ok(Policy {
            resource,
            actions,
            location,
        })
    }

    fn route(&mut self, public: bool, location: Location) -> Result<Route> {
        let method = self.name()?;
        if !["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].contains(&method.as_str()) {
            return Err(Error::unsupported(
                location,
                format!("HTTP method {method}"),
            ));
        }
        let path = self.text()?;
        let name = if self.is_name("as") {
            self.take();
            Some(self.name()?)
        } else {
            None
        };
        let body = self.block()?;
        Ok(Route {
            public,
            method,
            path,
            name,
            body,
            location,
        })
    }

    fn expr(&mut self, minimum: u8) -> Result<Expr> {
        if self.expression_depth >= 64 {
            return Err(Error::Limit("expression nesting"));
        }
        self.expression_depth += 1;
        let start = self.cursor;
        let mut left = self.prefix()?;
        loop {
            if self.cursor - start > 512 {
                return Err(Error::Limit("expression size"));
            }
            if self.is_symbol(".") {
                self.take();
                let name = self.name()?;
                let location = left.location;
                left = Expr {
                    kind: ExprKind::Field(Box::new(left), name.clone()),
                    location,
                };
                if [
                    "form",
                    "http_get",
                    "http_post",
                    "job_submit",
                    "event_emit",
                    "workflow_submit",
                    "workflow_state",
                    "git_query",
                    "delivery_query",
                    "ops_query",
                ]
                .contains(&name.as_str())
                    && self.is_symbol("<")
                {
                    self.take();
                    let mut types = vec![self.ty(0)?];
                    while self.is_symbol(",") {
                        self.take();
                        types.push(self.ty(0)?);
                    }
                    self.symbol(">")?;
                    left = self.call(left, types)?;
                }
                continue;
            }
            if self.is_symbol("(") {
                left = self.call(left, Vec::new())?;
                continue;
            }
            if self.is_symbol("?") {
                self.take();
                let location = left.location;
                left = Expr {
                    kind: ExprKind::Try(Box::new(left)),
                    location,
                };
                continue;
            }
            let Some((precedence, operator)) = self.operator() else {
                break;
            };
            if precedence < minimum {
                break;
            }
            self.take();
            let right = self.expr(precedence + 1)?;
            let location = left.location;
            left = Expr {
                kind: ExprKind::Binary(Box::new(left), operator, Box::new(right)),
                location,
            };
        }
        self.expression_depth -= 1;
        Ok(left)
    }

    fn prefix(&mut self) -> Result<Expr> {
        let token = self.take();
        let kind = match token.kind {
            Kind::Int(value) => ExprKind::Int(value),
            Kind::Text(value) => {
                if value.contains("#{") {
                    return Err(Error::unsupported(
                        token.location,
                        "string interpolation; use typed HTML interpolation or explicit Text concatenation",
                    ));
                }
                ExprKind::Text(value)
            }
            Kind::Name(value) if value == "true" || value == "false" => {
                ExprKind::Bool(value == "true")
            }
            Kind::Name(value) if value == "not" => {
                ExprKind::Unary(Unary::Not, Box::new(self.expr(7)?))
            }
            Kind::Name(value) if value == "match" => self.match_expression()?,
            Kind::Name(value) if value == "for" => {
                let name = self.name()?;
                self.keyword("in")?;
                let values = self.expr(0)?;
                ExprKind::For(name, Box::new(values), self.block()?)
            }
            Kind::Name(value) => ExprKind::Name(value),
            Kind::Symbol(value) if value == "-" => {
                ExprKind::Unary(Unary::Negate, Box::new(self.expr(7)?))
            }
            Kind::Symbol(value) if value == "." => ExprKind::Role(self.name()?),
            Kind::Symbol(value) if value == "(" => {
                let result = self.expr(0)?;
                self.symbol(")")?;
                return Ok(result);
            }
            Kind::Symbol(value) if value == "[" => {
                let mut items = Vec::new();
                while !self.is_symbol("]") {
                    items.push(self.expr(0)?);
                    if !self.is_symbol(",") {
                        break;
                    }
                    self.take();
                }
                self.symbol("]")?;
                ExprKind::List(items)
            }
            Kind::Html(value) => ExprKind::Html(crate::template::parse(&value, token.location)?),
            other => {
                return Err(Error::diagnostic(
                    "D010",
                    token.location,
                    format!("expected an expression, found {other:?}"),
                ));
            }
        };
        Ok(Expr {
            kind,
            location: token.location,
        })
    }

    fn call(&mut self, callee: Expr, types: Vec<TypeName>) -> Result<Expr> {
        self.symbol("(")?;
        let mut arguments = Vec::new();
        while !self.is_symbol(")") {
            let named = matches!(self.token().kind, Kind::Name(_))
                && self
                    .tokens
                    .get(self.cursor + 1)
                    .is_some_and(|token| token.kind == Kind::Symbol(":".into()));
            let name = if named {
                let name = self.name()?;
                self.symbol(":")?;
                Some(name)
            } else {
                None
            };
            arguments.push((name, self.expr(0)?));
            if !self.is_symbol(",") {
                break;
            }
            self.take();
        }
        self.symbol(")")?;
        let location = callee.location;
        Ok(Expr {
            kind: ExprKind::Call {
                callee: Box::new(callee),
                arguments,
                types,
            },
            location,
        })
    }

    fn operator(&self) -> Option<(u8, Binary)> {
        let value = match &self.token().kind {
            Kind::Symbol(value) | Kind::Name(value) => value.as_str(),
            _ => return None,
        };
        Some(match value {
            "or" => (1, Binary::Or),
            "and" => (2, Binary::And),
            "==" => (3, Binary::Equal),
            "!=" => (3, Binary::NotEqual),
            "<" => (4, Binary::Less),
            "<=" => (4, Binary::LessEqual),
            ">" => (4, Binary::Greater),
            ">=" => (4, Binary::GreaterEqual),
            "+" => (5, Binary::Add),
            "-" => (5, Binary::Subtract),
            "*" => (6, Binary::Multiply),
            "/" => (6, Binary::Divide),
            _ => return None,
        })
    }
}
