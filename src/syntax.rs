//! Unchecked syntax. Only the checker can produce an executable program.

use crate::error::Location;
use serde::Serialize;
use std::collections::BTreeSet;

#[derive(Serialize, Clone, Debug, Default)]
pub(crate) struct ProjectModule {
    pub program: Program,
    pub components: Vec<Component>,
    pub exports: BTreeSet<String>,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Component {
    pub name: String,
    pub properties: Vec<(String, TypeName)>,
    pub derived: Vec<(String, TypeName, Expr)>,
    pub states: Vec<(String, TypeName, Expr)>,
    pub events: Vec<(String, Vec<(String, Expr)>)>,
    pub template: Vec<HtmlNode>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug, Default)]
pub(crate) struct Program {
    pub declarations: Vec<Declaration>,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) enum Declaration {
    Alias(String, TypeName, Location),
    Record(Record),
    Enum(Enum),
    Resource(String, String, Location),
    Function(Function),
    Policy(Policy),
    Route(Route),
    App(App),
}

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub(crate) enum TypeName {
    Named(String),
    Generic(String, Vec<Self>),
    Id(String),
    Secret(String),
    BoundedText(usize, usize),
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Record {
    pub name: String,
    pub fields: Vec<Field>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Field {
    pub name: String,
    pub ty: TypeName,
    pub default: Option<Expr>,
    pub created_by: bool,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Enum {
    pub name: String,
    pub variants: Vec<(String, Vec<(String, TypeName)>)>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Function {
    pub name: String,
    pub parameters: Vec<(String, TypeName)>,
    pub result: TypeName,
    pub body: Vec<Statement>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Policy {
    pub resource: String,
    pub actions: Vec<PolicyAction>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct PolicyAction {
    pub name: String,
    pub bindings: Vec<String>,
    pub principal: String,
    pub expression: Expr,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Route {
    pub public: bool,
    pub method: String,
    pub path: String,
    pub name: Option<String>,
    pub body: Vec<Statement>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug, Default)]
pub(crate) struct App {
    pub name: String,
    pub database: bool,
    pub sessions: bool,
    pub grants: Vec<(String, String)>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Statement {
    pub kind: StatementKind,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) enum StatementKind {
    Let(String, Expr),
    Expression(Expr),
    Return(Expr),
    Redirect(Expr),
    Transaction(Vec<Statement>),
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct Expr {
    pub kind: ExprKind,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) enum ExprKind {
    Int(#[serde(serialize_with = "serialize_integer")] i64),
    Text(String),
    Bool(bool),
    Name(String),
    Role(String),
    Unary(Unary, Box<Expr>),
    Binary(Box<Expr>, Binary, Box<Expr>),
    Field(Box<Expr>, String),
    Call {
        callee: Box<Expr>,
        arguments: Vec<(Option<String>, Expr)>,
        types: Vec<TypeName>,
    },
    Try(Box<Expr>),
    Html(Vec<HtmlNode>),
    List(Vec<Expr>),
    Match(Box<Expr>, Vec<MatchArm>),
    For(String, Box<Expr>, Vec<Statement>),
}

#[derive(Serialize, Clone, Debug)]
pub(crate) struct MatchArm {
    pub pattern: Pattern,
    pub body: Vec<Statement>,
    pub location: Location,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) enum Pattern {
    Binding(String),
    Constructor(String, Vec<String>),
    Bool(bool),
}

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Unary {
    Not,
    Negate,
}

#[derive(Serialize, Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Binary {
    Add,
    Subtract,
    Multiply,
    Divide,
    Equal,
    NotEqual,
    Less,
    LessEqual,
    Greater,
    GreaterEqual,
    And,
    Or,
}

#[derive(Serialize, Clone, Debug)]
pub(crate) enum HtmlNode {
    Text(String),
    Expression(Expr),
    Element {
        tag: String,
        attributes: Vec<(String, HtmlAttribute)>,
        children: Vec<Self>,
    },
}

#[derive(Serialize, Clone, Debug)]
pub(crate) enum HtmlAttribute {
    Literal(String),
    Expression(Expr),
}

fn serialize_integer<S: serde::Serializer, T: std::fmt::Display>(
    value: &T,
    serializer: S,
) -> std::result::Result<S::Ok, S::Error> {
    serializer.collect_str(value)
}
