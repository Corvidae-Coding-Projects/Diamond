//! Unchecked syntax. Only the checker can produce an executable program.

use crate::error::Location;

#[derive(Clone, Debug, Default)]
pub(crate) struct Program {
    pub declarations: Vec<Declaration>,
}

#[derive(Clone, Debug)]
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

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum TypeName {
    Named(String),
    Generic(String, Vec<Self>),
    Id(String),
    BoundedText(usize, usize),
}

#[derive(Clone, Debug)]
pub(crate) struct Record {
    pub name: String,
    pub fields: Vec<Field>,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct Field {
    pub name: String,
    pub ty: TypeName,
    pub default: Option<Expr>,
    pub created_by: bool,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct Enum {
    pub name: String,
    pub variants: Vec<(String, Vec<(String, TypeName)>)>,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct Function {
    pub name: String,
    pub parameters: Vec<(String, TypeName)>,
    pub result: TypeName,
    pub body: Vec<Statement>,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct Policy {
    pub resource: String,
    pub actions: Vec<PolicyAction>,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct PolicyAction {
    pub name: String,
    pub bindings: Vec<String>,
    pub principal: String,
    pub expression: Expr,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct Route {
    pub public: bool,
    pub method: String,
    pub path: String,
    pub name: Option<String>,
    pub body: Vec<Statement>,
    pub location: Location,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct App {
    pub name: String,
    pub database: bool,
    pub sessions: bool,
    pub grants: Vec<(String, String)>,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) struct Statement {
    pub kind: StatementKind,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) enum StatementKind {
    Let(String, Expr),
    Expression(Expr),
    Return(Expr),
    Redirect(Expr),
    Transaction(Vec<Statement>),
}

#[derive(Clone, Debug)]
pub(crate) struct Expr {
    pub kind: ExprKind,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) enum ExprKind {
    Int(i64),
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

#[derive(Clone, Debug)]
pub(crate) struct MatchArm {
    pub pattern: Pattern,
    pub body: Vec<Statement>,
    pub location: Location,
}

#[derive(Clone, Debug)]
pub(crate) enum Pattern {
    Binding(String),
    Constructor(String, Vec<String>),
    Bool(bool),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Unary {
    Not,
    Negate,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
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

#[derive(Clone, Debug)]
pub(crate) enum HtmlNode {
    Text(String),
    Expression(Expr),
    Element {
        tag: String,
        attributes: Vec<(String, HtmlAttribute)>,
        children: Vec<Self>,
    },
}

#[derive(Clone, Debug)]
pub(crate) enum HtmlAttribute {
    Literal(String),
    Expression(Expr),
}
