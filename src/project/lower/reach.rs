//! Retain only declarations reachable from the checked browser component graph.
use super::{Component, EventValue, Expression, Node};
use crate::hir::CheckedProgram;
use crate::syntax::{Expr, ExprKind, Statement, StatementKind};
use crate::value::Type;
use std::collections::{BTreeMap, BTreeSet};
#[derive(Default)]
pub(super) struct Usage {
    pub functions: BTreeSet<String>,
    pub records: BTreeSet<String>,
    pub enums: BTreeSet<String>,
    pub aliases: BTreeSet<String>,
}
struct Reach<'a> {
    program: &'a CheckedProgram,
    used: Usage,
}
pub(super) fn used(program: &CheckedProgram, components: &BTreeMap<String, Component>) -> Usage {
    let mut reach = Reach {
        program,
        used: Usage::default(),
    };
    for c in components.values() {
        for (_, ty) in &c.properties {
            reach.ty(ty);
        }
        for (_, e) in c.state.iter().chain(&c.derived) {
            reach.expression(e);
        }
        for assignments in c.events.values() {
            for (_, e) in assignments {
                reach.event(e);
            }
        }
        reach.nodes(&c.template);
    }
    let mut done = BTreeSet::new();
    while let Some(name) = reach
        .used
        .functions
        .iter()
        .find(|n| !done.contains(*n))
        .cloned()
    {
        done.insert(name.clone());
        let f = &program.functions[&name];
        for (_, ty) in &f.parameters {
            reach.ty(ty);
        }
        reach.ty(&f.result);
        reach.block(&f.body);
    }
    reach.used
}
impl Reach<'_> {
    fn ty(&mut self, ty: &Type) {
        match ty {
            Type::Record(n) => {
                if self.used.records.insert(n.clone()) {
                    for f in self.program.records[n].fields.values() {
                        self.ty(&f.ty);
                    }
                }
            }
            Type::Enum(n) => {
                if self.used.enums.insert(n.clone()) {
                    for fs in self.program.enums[n].variants.values() {
                        for (_, t) in fs {
                            self.ty(t);
                        }
                    }
                }
            }
            Type::List(t) | Type::Option(t) | Type::Result(t) | Type::Success(t) => self.ty(t),
            Type::AppResult(t, e) => {
                self.ty(t);
                self.ty(e);
            }
            _ => {}
        }
    }
    fn expression(&mut self, e: &Expression) {
        self.ty(&e.ty);
        self.expr(&e.ast);
    }
    fn expr(&mut self, e: &Expr) {
        match &e.kind {
            ExprKind::Name(n) => {
                if self.program.functions.contains_key(n) {
                    self.used.functions.insert(n.clone());
                }
                if self.program.records.contains_key(n) {
                    self.ty(&Type::Record(n.clone()));
                }
                if let Some(ty) = self.program.aliases.get(n) {
                    self.used.aliases.insert(n.clone());
                    self.ty(ty);
                }
                if let Some((n, _)) = self.program.variant(n) {
                    self.ty(&Type::Enum(n.into()));
                }
            }
            ExprKind::Unary(_, e) | ExprKind::Field(e, _) | ExprKind::Try(e) => self.expr(e),
            ExprKind::Binary(a, _, b) => {
                self.expr(a);
                self.expr(b);
            }
            ExprKind::Call {
                callee, arguments, ..
            } => {
                self.expr(callee);
                for (_, e) in arguments {
                    self.expr(e);
                }
            }
            ExprKind::List(es) => {
                for e in es {
                    self.expr(e);
                }
            }
            ExprKind::For(_, values, body) => {
                self.expr(values);
                self.block(body);
            }
            ExprKind::Match(value, arms) => {
                self.expr(value);
                for arm in arms {
                    self.block(&arm.body);
                }
            }
            _ => {}
        }
    }
    fn block(&mut self, body: &[Statement]) {
        for s in body {
            match &s.kind {
                StatementKind::Let(_, e)
                | StatementKind::Expression(e)
                | StatementKind::Return(e)
                | StatementKind::Redirect(e) => self.expr(e),
                StatementKind::Transaction(body) => self.block(body),
            }
        }
    }
    fn event(&mut self, e: &EventValue) {
        match e {
            EventValue::Pure(e) => self.expression(e),
            EventValue::Async {
                expression,
                remotes,
            } => {
                self.expression(expression);
                for e in remotes.values() {
                    self.event(e);
                }
            }
            EventValue::Remote {
                arguments,
                types,
                result,
                ..
            } => {
                for e in arguments {
                    self.expression(e);
                }
                for t in types {
                    self.ty(t);
                }
                self.ty(result);
            }
        }
    }
    fn nodes(&mut self, nodes: &[Node]) {
        for n in nodes {
            match n {
                Node::Value(e) => self.expression(e),
                Node::When(e, children) => {
                    self.expression(e);
                    self.nodes(children);
                }
                Node::Each {
                    items,
                    key,
                    children,
                    ..
                } => {
                    self.expression(items);
                    self.expression(key);
                    self.nodes(children);
                }
                Node::Use { properties, .. } => {
                    for (_, e) in properties {
                        self.expression(e);
                    }
                }
                Node::Element {
                    attributes,
                    children,
                    ..
                } => {
                    for (_, a) in attributes {
                        if let super::Attribute::Value(e) = a {
                            self.expression(e);
                        }
                    }
                    self.nodes(children);
                }
                Node::Route(_, children) => self.nodes(children),
                Node::Text(_) => {}
            }
        }
    }
}
