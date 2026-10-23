//! Static signatures for the narrow W6 HTTP adapter family.
use super::{
    Checker, Effect, Error, Expr, ExprKind, Location, Result, TransactionContext, Type, TypeName,
    resolve_type,
};

const OPERATIONS_QUERIES: &[&str] = &[
    "search",
    "nodes",
    "shards",
    "placements",
    "tasks",
    "mirrors",
    "backups",
    "restores",
    "gc",
    "policies",
    "audit",
];
const OPERATIONS_COMMANDS: &[&str] = &[
    "node_drain",
    "shard_record",
    "placement_record",
    "mirror_schedule",
    "promotion_schedule",
    "search_schedule",
    "gc_schedule",
    "hold_set",
    "backup_record",
    "restore_record",
    "policy_set",
];
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
    pub(super) fn operations_call(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        if matches!(self.transaction_context, TransactionContext::Inside) {
            return Err(Error::unsupported(
                location,
                "operations adapter call in a transaction",
            ));
        }
        let Some(Expr {
            kind: ExprKind::Text(binding),
            ..
        }) = args.first().map(|(_, expression)| expression)
        else {
            return Err(Error::diagnostic(
                "D310",
                location,
                "operations binding requires a static name",
            ));
        };
        let (arguments, generic, text_arguments, integer, admin) = match method {
            "ops_command" => (7, 0, 6, 6, true),
            "ops_query" => (4, 1, 3, 3, false),
            "ops_rate" => (3, 0, 2, 2, false),
            _ => return Err(Error::Denied),
        };
        if !crate::project::resolve::identifier(binding)
            || args.len() != arguments
            || types.len() != generic
            || args.iter().any(|(name, _)| name.is_some())
        {
            return Err(Error::diagnostic(
                "D310",
                location,
                "invalid registered operations signature",
            ));
        }
        for (_, argument) in args.iter().take(text_arguments) {
            if !Type::Text.accepts(&self.expr(argument)?) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "operations text argument mismatch",
                ));
            }
        }
        if !Type::Int.accepts(&self.expr(&args[integer].1)?) {
            return Err(Error::diagnostic(
                "D310",
                location,
                "operations integer argument mismatch",
            ));
        }
        if method != "ops_rate" {
            let Some(Expr {
                kind: ExprKind::Text(operation),
                ..
            }) = args.get(1).map(|(_, expression)| expression)
            else {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "operations selector requires a static name",
                ));
            };
            let allowed = if method == "ops_query" {
                OPERATIONS_QUERIES
            } else {
                OPERATIONS_COMMANDS
            };
            if !allowed.contains(&operation.as_str()) {
                return Err(Error::Denied);
            }
        }
        self.effects.insert(Effect::Host(format!(
            "Ops{}<{binding}>",
            if admin { "Admin" } else { "Read" }
        )));
        let result = if method == "ops_query" {
            let result = resolve_type(types.first().ok_or(Error::Denied)?, self.program, location)?;
            if !crate::project::wire::safe_type(&result, self.program) {
                return Err(Error::Denied);
            }
            result
        } else {
            Type::Text
        };
        Ok(Type::Result(Box::new(result)))
    }

    pub(super) fn delivery_call(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        if matches!(self.transaction_context, TransactionContext::Inside) {
            return Err(Error::unsupported(
                location,
                "delivery operation in a transaction",
            ));
        }
        let Some(Expr {
            kind: ExprKind::Text(binding),
            ..
        }) = args.first().map(|(_, expression)| expression)
        else {
            return Err(Error::diagnostic(
                "D310",
                location,
                "delivery binding requires a static name",
            ));
        };
        let (arguments, generic, text_arguments, write) = match method {
            "delivery_schedule" => (5, 0, 5, true),
            "delivery_cancel" => (3, 0, 3, true),
            "delivery_query" => (4, 1, 3, false),
            _ => return Err(Error::Denied),
        };
        if !crate::project::resolve::identifier(binding)
            || args.len() != arguments
            || types.len() != generic
            || args.iter().any(|(name, _)| name.is_some())
        {
            return Err(Error::diagnostic(
                "D310",
                location,
                "invalid registered delivery signature",
            ));
        }
        for (_, argument) in args.iter().take(text_arguments) {
            if !Type::Text.accepts(&self.expr(argument)?) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "delivery arguments require Text values",
                ));
            }
        }
        if method == "delivery_query" && !Type::Int.accepts(&self.expr(&args[3].1)?) {
            return Err(Error::diagnostic(
                "D310",
                location,
                "delivery query limit requires Int",
            ));
        }
        self.effects.insert(Effect::Host(format!(
            "Delivery{}<{binding}>",
            if write { "Write" } else { "Read" }
        )));
        let result = if method == "delivery_query" {
            let result = resolve_type(types.first().ok_or(Error::Denied)?, self.program, location)?;
            if !crate::project::wire::safe_type(&result, self.program) {
                return Err(Error::Denied);
            }
            result
        } else {
            Type::Text
        };
        Ok(Type::Result(Box::new(result)))
    }

    fn check_git_specific(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<()> {
        if method == "git_query" {
            if !Type::Int.accepts(&self.expr(&args[5].1)?) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "Git query limit requires Int",
                ));
            }
            let Some(Expr {
                kind: ExprKind::Text(operation),
                ..
            }) = args.get(2).map(|(_, expression)| expression)
            else {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "Git query operation requires a static name",
                ));
            };
            if !["head", "tree", "blob", "history", "diff", "integration"]
                .contains(&operation.as_str())
            {
                return Err(Error::Denied);
            }
        }
        let boolean_arguments: &[usize] = match method {
            "git_protect" => &[4, 5, 7, 8, 9, 10],
            "git_gate" => &[5, 6],
            "git_review_gate" => &[4, 5],
            "git_deploy_key" => &[4],
            "git_certificate" => &[3],
            _ => &[],
        };
        for index in boolean_arguments {
            if !Type::Bool.accepts(&self.expr(&args[*index].1)?) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "Git policy flag requires Bool",
                ));
            }
        }
        let integer_arguments: &[usize] = match method {
            "git_protect" => &[6],
            "git_gate" => &[4, 7],
            "git_certificate" => &[4],
            "git_events" => &[1],
            _ => &[],
        };
        for index in integer_arguments {
            if !Type::Int.accepts(&self.expr(&args[*index].1)?) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "Git policy count or lifetime requires Int",
                ));
            }
        }
        Ok(())
    }

    pub(super) fn git_call(
        &mut self,
        method: &str,
        args: &[(Option<String>, Expr)],
        types: &[TypeName],
        location: Location,
    ) -> Result<Type> {
        let Some((
            None,
            Expr {
                kind: ExprKind::Text(binding),
                ..
            },
        )) = args.first()
        else {
            return Err(Error::diagnostic(
                "D310",
                location,
                "Git binding requires a static name",
            ));
        };
        if !crate::project::resolve::identifier(binding)
            || args.iter().any(|(name, _)| name.is_some())
        {
            return Err(Error::Denied);
        }
        let (expected_arguments, expected_types, text_arguments) = match method {
            "git_create" | "git_ack_event" => (2, 0, 2),
            "git_fork" | "git_unprotect" | "git_revoke_deploy_key" | "git_revoke_certificate" => {
                (3, 0, 3)
            }
            "git_merge" => (6, 0, 6),
            "git_integrate" => (9, 0, 9),
            "git_query" => (6, 1, 5),
            "git_protect" => (11, 0, 4),
            "git_gate" => (8, 0, 4),
            "git_review_gate" => (6, 0, 4),
            "git_deploy_key" => (5, 0, 4),
            "git_certificate" => (5, 0, 3),
            "git_events" => (2, 0, 1),
            _ => return Err(Error::Denied),
        };
        if args.len() != expected_arguments || types.len() != expected_types {
            return Err(Error::diagnostic(
                "D310",
                location,
                "invalid registered Git signature",
            ));
        }
        for (_, argument) in args.iter().take(text_arguments) {
            if !Type::Text.accepts(&self.expr(argument)?) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "Git arguments require Text values",
                ));
            }
        }
        self.check_git_specific(method, args, location)?;
        let write = matches!(
            method,
            "git_create" | "git_fork" | "git_merge" | "git_integrate"
        );
        let review = method == "git_review_gate";
        let admin = !matches!(
            method,
            "git_create"
                | "git_fork"
                | "git_merge"
                | "git_integrate"
                | "git_query"
                | "git_review_gate"
        );
        let capability = if admin {
            "Admin"
        } else if write {
            "Write"
        } else if review {
            "Review"
        } else {
            "Read"
        };
        self.effects
            .insert(Effect::Host(format!("Git{capability}<{binding}>")));
        let result = if write || review || admin {
            Type::Text
        } else {
            let result = resolve_type(types.first().ok_or(Error::Denied)?, self.program, location)?;
            if !crate::project::wire::safe_type(&result, self.program) {
                return Err(Error::diagnostic(
                    "D310",
                    location,
                    "Git query requires a finite wire-safe result type",
                ));
            }
            result
        };
        Ok(Type::Result(Box::new(result)))
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
