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

impl Checker<'_> {
    pub(super) fn durable_call(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        let generic = matches!(method, "job_submit" | "event_emit");
        let count = match method {
            "job_submit" => 4,
            "event_emit" => 3,
            "job_key" => 0,
            _ => 2,
        };
        if args.len() != count
            || types.len() != usize::from(generic)
            || args.iter().any(|(n, _)| n.is_some())
        {
            return Err(Error::diagnostic(
                "D310",
                location,
                "invalid durable operation signature",
            ));
        }
        if count > 0
            && !matches!(&args[0].1.kind,ExprKind::Text(n) if crate::project::resolve::identifier(n))
        {
            return Err(Error::diagnostic(
                "D310",
                location,
                "durable binding requires a static identifier",
            ));
        }
        if let Some((
            _,
            Expr {
                kind: ExprKind::Text(name),
                ..
            },
        )) = args.first()
        {
            let namespace = if method == "event_emit" {
                "events"
            } else {
                "jobs"
            };
            let (resource, payload) = self
                .program
                .durable
                .get(&format!("{namespace}/{name}"))
                .cloned()
                .ok_or_else(|| Error::diagnostic("D310", location, "unknown durable binding"))?;
            self.effects.insert(Effect::Read(resource.clone()));
            if method != "job_status" {
                self.effects.insert(Effect::Write(resource));
            }
            if generic && payload != resolve_type(&types[0], self.program, location)? {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "payload type differs from declared binding",
                ));
            }
        }
        for (i, (_, arg)) in args.iter().enumerate() {
            let expected = if generic && i == 2 {
                resolve_type(&types[0], self.program, location)?
            } else {
                Type::Text
            };
            if !crate::project::wire::safe_type(&expected, self.program)
                || !expected.accepts(&self.expr(arg)?)
            {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "durable arguments require declared wire-safe types",
                ));
            }
        }
        self.effects.insert(Effect::Host(
            match method {
                "event_emit" => "Events",
                "job_key" => "JobKey",
                _ => "Jobs",
            }
            .into(),
        ));
        if method == "job_key" {
            Ok(Type::Text)
        } else {
            Ok(Type::Result(Box::new(Type::Text)))
        }
    }
}
