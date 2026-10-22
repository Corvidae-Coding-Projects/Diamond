//! Scalar browser IR with resolved symbols, explicit types, and source locations.

use crate::checker::expression_type;
use crate::error::{Error, Location, Result};
use crate::hir::CheckedProgram;
use crate::syntax::{
    self, Binary, Expr, ExprKind, HtmlAttribute, HtmlNode, Statement, StatementKind, Unary,
};
use crate::value::Type;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
pub(super) enum Scalar {
    Int,
    Bool,
    Text,
}

impl Scalar {
    fn from_type(ty: &Type, location: Location) -> Result<Self> {
        match ty {
            Type::Int => Ok(Self::Int),
            Type::Bool => Ok(Self::Bool),
            Type::Text | Type::BoundedText(_, _) => Ok(Self::Text),
            _ => Err(Error::unsupported(
                location,
                "W1 browser values support Int, Bool and Text",
            )),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub(super) struct Expression {
    pub ty: Scalar,
    pub line: usize,
    pub column: usize,
    pub kind: Kind,
}

#[derive(Clone, Debug, Serialize)]
pub(super) enum Kind {
    Int(String),
    Text(String),
    Bool(bool),
    Name(String),
    Unary(String, Box<Expression>),
    Binary(Box<Expression>, String, Box<Expression>),
    Call(String, Vec<Expression>),
    Remote(String, Vec<Expression>, Vec<Scalar>),
}

#[derive(Clone, Debug, Serialize)]
pub(super) enum Instruction {
    Let(String, Expression),
    Value(Expression),
    Return(Expression),
}

#[derive(Clone, Debug, Serialize)]
pub(super) struct Function {
    pub parameters: Vec<(String, Scalar)>,
    pub result: Scalar,
    pub body: Vec<Instruction>,
}

#[derive(Clone, Debug, Serialize)]
pub(super) enum Node {
    Text(String),
    Value(Expression),
    Element {
        tag: String,
        attributes: Vec<(String, Attribute)>,
        children: Vec<Self>,
    },
}

#[derive(Clone, Debug, Serialize)]
pub(super) enum Attribute {
    Text(String),
    Value(Expression),
    Event(String),
    Binding(String),
}

#[derive(Clone, Debug, Serialize)]
pub(super) struct BrowserProgram {
    pub component: String,
    pub state: Vec<(String, Expression)>,
    pub events: BTreeMap<String, Vec<(String, Expression)>>,
    pub template: Vec<Node>,
    pub functions: BTreeMap<String, Function>,
}

pub(super) fn lower(
    program: &CheckedProgram,
    component: &syntax::Component,
    rpc: &BTreeMap<String, super::rpc::Operation>,
) -> Result<BrowserProgram> {
    let mut lower = Lower {
        program,
        calls: BTreeSet::new(),
    };
    let mut locals = BTreeMap::new();
    let mut state = Vec::new();
    for (name, ty, value) in &component.states {
        let ty = crate::checker::resolve_type(ty, program, value.location)?;
        let scalar = Scalar::from_type(&ty, value.location)?;
        let value = lower.expression(value, &BTreeMap::new())?;
        if value.ty != scalar {
            return Err(error(
                component.location,
                "state initializer does not match its declared type",
            ));
        }
        locals.insert(name.clone(), ty);
        state.push((name.clone(), value));
    }
    let mut events = BTreeMap::new();
    for (name, assignments) in &component.events {
        let mut event = Vec::new();
        for (target, value) in assignments {
            let ty = locals.get(target).ok_or_else(|| {
                error(
                    value.location,
                    "event assignment must target declared component state",
                )
            })?;
            let expected = Scalar::from_type(ty, value.location)?;
            let value = lower.event_expression(value, &locals, rpc)?;
            if value.ty != expected {
                return Err(error(
                    component.location,
                    "event assignment changes a state's type",
                ));
            }
            event.push((target.clone(), value));
        }
        events.insert(name.clone(), event);
    }
    let template = lower.nodes(&component.template, &locals, &events)?;
    let mut functions = BTreeMap::new();
    while let Some(name) = lower
        .calls
        .iter()
        .find(|name| !functions.contains_key(*name))
        .cloned()
    {
        let function = program
            .functions
            .get(&name)
            .ok_or_else(|| error(component.location, "unresolved browser call"))?;
        if !function.effects.is_empty() {
            return Err(error(
                function.location,
                "browser functions cannot carry host authority",
            ));
        }
        let mut locals = BTreeMap::new();
        let mut parameters = Vec::new();
        for (name, ty) in &function.parameters {
            parameters.push((name.clone(), Scalar::from_type(ty, function.location)?));
            locals.insert(name.clone(), ty.clone());
        }
        let result = Scalar::from_type(&function.result, function.location)?;
        let body = lower.block(&function.body, &mut locals)?;
        functions.insert(
            name,
            Function {
                parameters,
                result,
                body,
            },
        );
    }
    Ok(BrowserProgram {
        component: component.name.clone(),
        state,
        events,
        template,
        functions,
    })
}

struct Lower<'a> {
    program: &'a CheckedProgram,
    calls: BTreeSet<String>,
}

impl Lower<'_> {
    fn event_expression(
        &mut self,
        expression: &Expr,
        locals: &BTreeMap<String, Type>,
        operations: &BTreeMap<String, super::rpc::Operation>,
    ) -> Result<Expression> {
        if let ExprKind::Call {
            callee,
            arguments,
            types,
        } = &expression.kind
            && let ExprKind::Field(base, name) = &callee.kind
            && let ExprKind::Name(namespace) = &base.kind
            && ["rpc", "session"].contains(&namespace.as_str())
        {
            let (parameters, result, operation) = if namespace == "rpc" {
                let operation = operations.get(name).ok_or_else(|| {
                    error(expression.location, "RPC is not declared in the manifest")
                })?;
                (
                    operation.arguments.clone(),
                    operation.result.clone(),
                    format!("rpc/{name}"),
                )
            } else {
                let parameters = match name.as_str() {
                    "login" => vec![Type::Text, Type::Text],
                    "logout" | "user" => vec![],
                    _ => return Err(error(expression.location, "unknown session operation")),
                };
                (parameters, Type::Text, format!("session/{name}"))
            };
            if !types.is_empty()
                || arguments.len() != parameters.len()
                || arguments.iter().any(|(name, _)| name.is_some())
            {
                return Err(error(
                    expression.location,
                    "remote arguments must match the declared positional signature",
                ));
            }
            let mut values = Vec::new();
            let mut scalars = Vec::new();
            for ((_, value), ty) in arguments.iter().zip(&parameters) {
                let expected = Scalar::from_type(ty, value.location)?;
                let value = self.expression(value, locals)?;
                if value.ty != expected {
                    return Err(error(expression.location, "remote argument type mismatch"));
                }
                values.push(value);
                scalars.push(expected);
            }
            return Ok(Expression {
                ty: Scalar::from_type(&result, expression.location)?,
                line: expression.location.line,
                column: expression.location.column,
                kind: Kind::Remote(operation, values, scalars),
            });
        }
        self.expression(expression, locals)
    }

    fn expression(
        &mut self,
        expression: &Expr,
        locals: &BTreeMap<String, Type>,
    ) -> Result<Expression> {
        let ty = Scalar::from_type(
            &expression_type(self.program, locals.clone(), expression)?,
            expression.location,
        )?;
        let kind = match &expression.kind {
            ExprKind::Int(value) => Kind::Int(value.to_string()),
            ExprKind::Text(value) => Kind::Text(value.clone()),
            ExprKind::Bool(value) => Kind::Bool(*value),
            ExprKind::Name(name) => Kind::Name(name.clone()),
            ExprKind::Unary(operator, value) => Kind::Unary(
                match operator {
                    Unary::Not => "not",
                    Unary::Negate => "negate",
                }
                .into(),
                Box::new(self.expression(value, locals)?),
            ),
            ExprKind::Binary(a, operator, b) => Kind::Binary(
                Box::new(self.expression(a, locals)?),
                binary(*operator).into(),
                Box::new(self.expression(b, locals)?),
            ),
            ExprKind::Call {
                callee,
                arguments,
                types,
            } if types.is_empty() => {
                let ExprKind::Name(name) = &callee.kind else {
                    return Err(error(
                        expression.location,
                        "browser calls require a resolved pure function",
                    ));
                };
                if name != "len" {
                    self.calls.insert(name.clone());
                }
                Kind::Call(
                    name.clone(),
                    arguments
                        .iter()
                        .map(|(_, value)| self.expression(value, locals))
                        .collect::<Result<_>>()?,
                )
            }
            _ => {
                return Err(Error::unsupported(
                    expression.location,
                    "this expression is outside the W1 scalar browser subset",
                ));
            }
        };
        Ok(Expression {
            ty,
            line: expression.location.line,
            column: expression.location.column,
            kind,
        })
    }

    fn block(
        &mut self,
        body: &[Statement],
        locals: &mut BTreeMap<String, Type>,
    ) -> Result<Vec<Instruction>> {
        let mut output = Vec::new();
        for statement in body {
            output.push(match &statement.kind {
                StatementKind::Let(name, value) => {
                    let ty = expression_type(self.program, locals.clone(), value)?;
                    let value = self.expression(value, locals)?;
                    locals.insert(name.clone(), ty);
                    Instruction::Let(name.clone(), value)
                }
                StatementKind::Expression(value) => {
                    Instruction::Value(self.expression(value, locals)?)
                }
                StatementKind::Return(value) => {
                    Instruction::Return(self.expression(value, locals)?)
                }
                _ => {
                    return Err(Error::unsupported(
                        statement.location,
                        "this statement is outside the W1 browser subset",
                    ));
                }
            });
        }
        Ok(output)
    }

    fn nodes(
        &mut self,
        nodes: &[HtmlNode],
        locals: &BTreeMap<String, Type>,
        events: &BTreeMap<String, Vec<(String, Expression)>>,
    ) -> Result<Vec<Node>> {
        nodes.iter().map(|node|match node {
            HtmlNode::Text(text)=>Ok(Node::Text(text.clone())),
            HtmlNode::Expression(value)=>Ok(Node::Value(self.expression(value,locals)?)),
            HtmlNode::Element {tag,attributes,children}=> {
                if ["html","head","body","title","form","textarea"].contains(&tag.as_str()) {
                    return Err(Error::unsupported(Location::default(),"W1 components render content elements and click buttons; document elements/forms arrive in W3"));
                }
                let mut checked=Vec::new();
                for (name,attribute) in attributes {
                    let attribute=match (name.as_str(),attribute) {
                        ("bind-value",HtmlAttribute::Expression(Expr {kind:ExprKind::Name(state),..})) if tag=="input" && locals.get(state)==Some(&Type::Text)=>Attribute::Binding(state.clone()),
                        ("bind-value",_)=>return Err(error(Location::default(),"bind-value requires Text state on an input")),
                        ("on-click",HtmlAttribute::Expression(Expr {kind:ExprKind::Name(event),..})) if tag=="button" && events.contains_key(event)=>Attribute::Event(event.clone()),
                        ("on-click",_)=>return Err(error(Location::default(),"on-click requires a declared event name on a button")),
                        (_,HtmlAttribute::Literal(text))=>Attribute::Text(text.clone()),
                        ("href"|"action",HtmlAttribute::Expression(_))=>return Err(Error::unsupported(Location::default(),"dynamic component URLs are outside W1")),
                        (_,HtmlAttribute::Expression(value))=>Attribute::Value(self.expression(value,locals)?),
                    };
                    checked.push((name.clone(),attribute));
                }
                if tag=="input" && (!checked.iter().any(|(name,_)| name=="bind-value") || checked.iter().any(|(name,value)| name=="value" || name=="type" && !matches!(value,Attribute::Text(v) if ["text","password"].contains(&v.as_str())))) {
                    return Err(error(Location::default(),"W2 inputs require bind-value and a static text/password type"));
                }
                Ok(Node::Element {tag:tag.clone(),attributes:checked,children:self.nodes(children,locals,events)?})
            }
        }).collect()
    }
}

fn binary(operator: Binary) -> &'static str {
    match operator {
        Binary::Add => "add",
        Binary::Subtract => "subtract",
        Binary::Multiply => "multiply",
        Binary::Divide => "divide",
        Binary::Equal => "equal",
        Binary::NotEqual => "not_equal",
        Binary::Less => "less",
        Binary::LessEqual => "less_equal",
        Binary::Greater => "greater",
        Binary::GreaterEqual => "greater_equal",
        Binary::And => "and",
        Binary::Or => "or",
    }
}

fn error(location: Location, message: &str) -> Error {
    Error::diagnostic("D330", location, message)
}
