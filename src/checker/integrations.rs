//! Static signatures for the narrow W6 HTTP adapter family.
use super::{
    Checker, Effect, Error, Expr, ExprKind, Location, Result, TransactionContext, Type, TypeName,
    resolve_type,
};
impl Checker<'_> {
    pub(super) fn integration_call(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        if matches!(self.transaction_context, TransactionContext::Inside) {
            return Err(Error::unsupported(
                location,
                "external HTTP in a transaction",
            ));
        }
        let Some((
            None,
            Expr {
                kind: ExprKind::Text(name),
                ..
            },
        )) = args.first()
        else {
            return Err(Error::diagnostic(
                "D310",
                location,
                "destination requires a static name",
            ));
        };
        if !crate::project::resolve::identifier(name) || args.iter().any(|(n, _)| n.is_some()) {
            return Err(Error::Denied);
        }
        let secret = Type::Secret(name.clone());
        let expected_types = match method {
            "secret" => 0,
            "http_get" => 1,
            _ => 2,
        };
        let count = match method {
            "secret" => 1,
            "http_get" => 3,
            _ => 4,
        };
        if types.len() != expected_types || args.len() != count {
            return Err(Error::diagnostic(
                "D310",
                location,
                "invalid registered HTTP signature",
            ));
        }
        if method != "secret" {
            if !Type::Text.accepts(&self.expr(&args[1].1)?) || self.expr(&args[2].1)? != secret {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "path or destination secret type mismatch",
                ));
            }
            for ty in types {
                let ty = resolve_type(ty, self.program, location)?;
                if !crate::project::wire::safe_type(&ty, self.program) {
                    return Err(Error::diagnostic(
                        "D310",
                        location,
                        "HTTP requires finite wire-safe types",
                    ));
                }
            }
            if method == "http_post" {
                let expected = resolve_type(&types[0], self.program, location)?;
                if !expected.accepts(&self.expr(&args[3].1)?) {
                    return Err(Error::diagnostic(
                        "D310",
                        location,
                        "HTTP body type mismatch",
                    ));
                }
            }
        }
        self.effects.insert(Effect::Host(format!("Http<{name}>")));
        if method == "secret" {
            Ok(secret)
        } else {
            Ok(Type::Result(Box::new(resolve_type(
                types.last().ok_or(Error::Denied)?,
                self.program,
                location,
            )?)))
        }
    }
}
