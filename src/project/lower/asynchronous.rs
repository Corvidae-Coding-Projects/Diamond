//! Type remote expressions using private typed placeholders, preserving branch order.
use super::{EventValue, Lower, error};
use crate::error::Result;
use crate::syntax::{Expr, ExprKind, Pattern, Statement, StatementKind};
use crate::value::Type;
use std::collections::BTreeMap;

struct Rewrite<'a, 'p> {
    lower: &'a Lower<'p>,
    rpc: &'a BTreeMap<String, crate::project::rpc::Operation>,
    remotes: BTreeMap<String, EventValue>,
    types: BTreeMap<String, Type>,
}
pub(super) fn lower(
    lower: &Lower<'_>,
    expression: &Expr,
    locals: &BTreeMap<String, Type>,
    rpc: &BTreeMap<String, crate::project::rpc::Operation>,
) -> Result<EventValue> {
    let mut rewrite = Rewrite {
        lower,
        rpc,
        remotes: BTreeMap::new(),
        types: BTreeMap::new(),
    };
    let ast = rewrite.expr(expression, locals)?;
    let value = lower.expression(&ast, &rewrite.scope(locals))?;
    if rewrite.remotes.is_empty() {
        Ok(EventValue::Pure(value))
    } else {
        Ok(EventValue::Async {
            expression: value,
            remotes: rewrite.remotes,
        })
    }
}
impl Rewrite<'_, '_> {
    fn scope(&self, locals: &BTreeMap<String, Type>) -> BTreeMap<String, Type> {
        let mut scope = locals.clone();
        scope.extend(self.types.clone());
        scope
    }
    fn ty(&self, e: &Expr, locals: &BTreeMap<String, Type>) -> Result<Type> {
        Ok(self.lower.expression(e, &self.scope(locals))?.ty)
    }
    fn expr(&mut self, e: &Expr, locals: &BTreeMap<String, Type>) -> Result<Expr> {
        let mut out = e.clone();
        match &mut out.kind {
            ExprKind::Call {
                callee, arguments, ..
            } => {
                for (_, value) in arguments {
                    *value = self.expr(value, locals)?;
                }
                if matches!(&callee.kind,ExprKind::Field(base,_) if matches!(&base.kind,ExprKind::Name(n) if ["rpc","session","files"].contains(&n.as_str())))
                {
                    let value = self.lower.remote(&out, &self.scope(locals), self.rpc)?;
                    let EventValue::Remote { result, .. } = &value else {
                        return Err(error(e.location, "invalid remote expression"));
                    };
                    if self.remotes.len() >= 256 {
                        return Err(error(e.location, "event remote expression limit"));
                    }
                    let name = format!("__diamond_async_{}", self.remotes.len());
                    self.types.insert(name.clone(), result.clone());
                    self.remotes.insert(name.clone(), value);
                    out.kind = ExprKind::Name(name);
                }
            }
            ExprKind::Unary(_, value) | ExprKind::Field(value, _) | ExprKind::Try(value) => {
                **value = self.expr(value, locals)?;
            }
            ExprKind::Binary(a, _, b) => {
                **a = self.expr(a, locals)?;
                **b = self.expr(b, locals)?;
            }
            ExprKind::List(values) => {
                for value in values {
                    *value = self.expr(value, locals)?;
                }
            }
            ExprKind::For(name, values, body) => {
                **values = self.expr(values, locals)?;
                let Type::List(inner) = self.ty(values, locals)? else {
                    return Err(error(e.location, "for requires a List"));
                };
                let mut nested = locals.clone();
                nested.insert(name.clone(), *inner);
                self.block(body, &mut nested)?;
            }
            ExprKind::Match(value, arms) => {
                **value = self.expr(value, locals)?;
                let ty = self.ty(value, locals)?;
                for arm in arms {
                    let mut nested = locals.clone();
                    match &arm.pattern {
                        Pattern::Binding(n) => {
                            if n != "_" {
                                nested.insert(n.clone(), ty.clone());
                            }
                        }
                        Pattern::Constructor(name, names) => {
                            let fields = match (&ty, name.as_str()) {
                                (Type::Option(inner), "Some")
                                | (
                                    Type::Result(inner)
                                    | Type::Success(inner)
                                    | Type::AppResult(inner, _),
                                    "Ok",
                                ) => vec![(**inner).clone()],
                                (Type::Result(_), "Err") => vec![Type::DataError],
                                (Type::AppResult(_, error), "Err") => vec![(**error).clone()],
                                (Type::Option(_), "None") => vec![],
                                _ => self
                                    .lower
                                    .program
                                    .variant(name)
                                    .map(|(_, fs)| fs.iter().map(|(_, t)| t.clone()).collect())
                                    .ok_or_else(|| {
                                        error(arm.location, "invalid match constructor")
                                    })?,
                            };
                            for (n, t) in names.iter().zip(fields) {
                                if n != "_" {
                                    nested.insert(n.clone(), t);
                                }
                            }
                        }
                        Pattern::Bool(_) => {}
                    }
                    self.block(&mut arm.body, &mut nested)?;
                }
            }
            _ => {}
        }
        Ok(out)
    }
    fn block(&mut self, body: &mut [Statement], locals: &mut BTreeMap<String, Type>) -> Result<()> {
        for statement in body {
            match &mut statement.kind {
                StatementKind::Let(n, e) => {
                    *e = self.expr(e, locals)?;
                    locals.insert(n.clone(), self.ty(e, locals)?);
                }
                StatementKind::Expression(e) | StatementKind::Return(e) => {
                    *e = self.expr(e, locals)?;
                }
                _ => {
                    return Err(error(
                        statement.location,
                        "events cannot open transactions or redirect responses",
                    ));
                }
            }
        }
        Ok(())
    }
}
