//! Restricted, typed policy predicates with reference evaluation and SQL lowering.

use crate::authority::Principal;
use crate::error::{Error, Result};
use crate::hir::{CheckedProgram, Record};

mod postgres;
use crate::syntax::{Binary, Expr, ExprKind, Policy as SyntaxPolicy, PolicyAction, Unary};
use crate::value::{Type, Value, ValueKind};
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Action {
    Read,
    Create,
    Update,
    Delete,
}

impl Action {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "read" => Some(Self::Read),
            "create" => Some(Self::Create),
            "update" => Some(Self::Update),
            "delete" => Some(Self::Delete),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Policy {
    rules: BTreeMap<Action, Predicate>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Operand {
    Constant(Value),
    Field(usize, String, Type),
}

#[derive(Clone, Debug)]
enum Predicate {
    Truth(Operand),
    Authenticated,
    IsUser(Operand),
    HasRole(String),
    Lookup(String, Operand, Option<String>),
    Not(Box<Self>),
    And(Box<Self>, Box<Self>),
    Or(Box<Self>, Box<Self>),
    Compare(Operand, Binary, Operand),
}

pub(crate) fn compile(
    source: &SyntaxPolicy,
    record: &Record,
    program: &CheckedProgram,
) -> Result<Policy> {
    let mut rules = BTreeMap::new();
    for action in &source.actions {
        let kind = Action::parse(&action.name).ok_or_else(|| {
            Error::unsupported(action.location, format!("policy action {}", action.name))
        })?;
        let arity = if kind == Action::Update { 2 } else { 1 };
        if action.bindings.len() != arity {
            return Err(Error::diagnostic(
                "D200",
                action.location,
                format!("{} needs {arity} record bindings", action.name),
            ));
        }
        let mut names = action.bindings.clone();
        names.push(action.principal.clone());
        names.sort();
        names.dedup();
        if names.len() != arity + 1 {
            return Err(Error::diagnostic(
                "D200",
                action.location,
                "policy bindings must be distinct",
            ));
        }
        let rule = predicate(&action.expression, action, record, program)?;
        if rules.insert(kind, rule).is_some() {
            return Err(Error::diagnostic(
                "D200",
                action.location,
                "duplicate policy action",
            ));
        }
    }
    Ok(Policy { rules })
}

impl Policy {
    pub fn has(&self, action: Action) -> bool {
        self.rules.contains_key(&action)
    }

    pub fn check(
        &self,
        action: Action,
        rows: &[&BTreeMap<String, Value>],
        principal: &Principal,
    ) -> Result<()> {
        self.check_with(action, rows, principal, &mut |_, _, _| Err(Error::Denied))
    }

    pub(crate) fn check_with(
        &self,
        action: Action,
        rows: &[&BTreeMap<String, Value>],
        principal: &Principal,
        lookup: &mut LookupEvaluator<'_>,
    ) -> Result<()> {
        let rule = self.rules.get(&action).ok_or(Error::Denied)?;
        if rule.evaluate(rows, principal, lookup)? {
            Ok(())
        } else {
            Err(Error::Denied)
        }
    }

    pub fn read_sql(&self, principal: &Principal) -> Result<(String, Vec<rusqlite::types::Value>)> {
        let rule = self.rules.get(&Action::Read).ok_or(Error::Denied)?;
        let mut parameters = Vec::new();
        let sql = rule.sql(principal, &mut parameters)?;
        Ok((sql, parameters))
    }
}

fn operand(
    expression: &Expr,
    action: &PolicyAction,
    record: &Record,
    program: &CheckedProgram,
) -> Result<(Operand, Type)> {
    let constant = match &expression.kind {
        ExprKind::Int(value) => Some((Value(ValueKind::Int(*value)), Type::Int)),
        ExprKind::Text(value) => Some((Value(ValueKind::Text(value.clone())), Type::Text)),
        ExprKind::Bool(value) => Some((Value(ValueKind::Bool(*value)), Type::Bool)),
        ExprKind::Name(name) if name == "None" && program.storage.is_some() => {
            Some((Value(ValueKind::None), Type::Option(Box::new(Type::Never))))
        }
        _ => None,
    };
    if let Some((value, ty)) = constant {
        return Ok((Operand::Constant(value), ty));
    }
    if let ExprKind::Field(base, field) = &expression.kind
        && let ExprKind::Name(binding) = &base.kind
        && let Some(index) = action.bindings.iter().position(|name| name == binding)
    {
        let ty = record
            .fields
            .get(field)
            .ok_or_else(|| {
                Error::diagnostic(
                    "D201",
                    expression.location,
                    format!("unknown policy field {field}"),
                )
            })?
            .ty
            .clone();
        if !(matches!(
            ty,
            Type::Int | Type::Bool | Type::Text | Type::BoundedText(_, _) | Type::Id(_)
        ) || program.storage.is_some() && crate::relational::scalar(&ty, program))
        {
            return Err(Error::unsupported(
                expression.location,
                "non-scalar policy operands",
            ));
        }
        return Ok((Operand::Field(index, field.clone(), ty.clone()), ty));
    }
    Err(Error::unsupported(
        expression.location,
        "policy operands must be constants or declared record fields; arbitrary application calls are excluded",
    ))
}

fn predicate(
    expression: &Expr,
    action: &PolicyAction,
    record: &Record,
    program: &CheckedProgram,
) -> Result<Predicate> {
    let result = match &expression.kind {
        ExprKind::Unary(Unary::Not, value) => {
            Predicate::Not(Box::new(predicate(value, action, record, program)?))
        }
        ExprKind::Binary(left, Binary::And, right) => Predicate::And(
            Box::new(predicate(left, action, record, program)?),
            Box::new(predicate(right, action, record, program)?),
        ),
        ExprKind::Binary(left, Binary::Or, right) => Predicate::Or(
            Box::new(predicate(left, action, record, program)?),
            Box::new(predicate(right, action, record, program)?),
        ),
        ExprKind::Binary(left, operator, right)
            if matches!(
                operator,
                Binary::Equal
                    | Binary::NotEqual
                    | Binary::Less
                    | Binary::LessEqual
                    | Binary::Greater
                    | Binary::GreaterEqual
            ) =>
        {
            comparison(left, *operator, right, action, record, program)?
        }
        ExprKind::Field(base, field)
            if matches!(&base.kind, ExprKind::Name(name) if name == &action.principal)
                && field == "authenticated" =>
        {
            Predicate::Authenticated
        }
        ExprKind::Call {
            callee,
            arguments,
            types,
        } if types.is_empty() && arguments.iter().all(|(name, _)| name.is_none()) => {
            let ExprKind::Field(base, method) = &callee.kind else {
                return Err(Error::unsupported(
                    expression.location,
                    "application calls in policies",
                ));
            };
            if !matches!(&base.kind, ExprKind::Name(name) if name == &action.principal) {
                return Err(Error::unsupported(
                    expression.location,
                    "only built-in principal predicates may be called in policies",
                ));
            }
            match method.as_str() {
                "is_user" if arguments.len() == 1 => {
                    let (value, ty) = operand(&arguments[0].1, action, record, program)?;
                    if ty != Type::Id("user".into()) {
                        return Err(Error::diagnostic(
                            "D201",
                            expression.location,
                            "principal.is_user requires Id<\"user\">",
                        ));
                    }
                    Predicate::IsUser(value)
                }
                "has_role" if arguments.len() == 1 => match &arguments[0].1.kind {
                    ExprKind::Role(role) => Predicate::HasRole(role.clone()),
                    _ => {
                        return Err(Error::diagnostic(
                            "D201",
                            expression.location,
                            "has_role requires a role literal such as .admin",
                        ));
                    }
                },
                "is_related" | "has_role_in"
                    if arguments.len() == if method == "is_related" { 2 } else { 3 } =>
                {
                    membership(expression, method, arguments, action, record, program)?
                }
                _ => {
                    return Err(Error::unsupported(
                        expression.location,
                        format!("principal predicate {method}"),
                    ));
                }
            }
        }
        _ => {
            let (value, ty) = operand(expression, action, record, program)?;
            if ty != Type::Bool {
                return Err(Error::diagnostic(
                    "D201",
                    expression.location,
                    "policy result must be Bool",
                ));
            }
            Predicate::Truth(value)
        }
    };
    Ok(result)
}

fn comparison(
    left: &Expr,
    operator: Binary,
    right: &Expr,
    action: &PolicyAction,
    record: &Record,
    program: &CheckedProgram,
) -> Result<Predicate> {
    let (left_value, a) = operand(left, action, record, program)?;
    let (right_value, b) = operand(right, action, record, program)?;
    if !a.accepts(&b) && !b.accepts(&a) {
        return Err(Error::diagnostic(
            "D201",
            left.location,
            "policy comparison requires matching scalar types",
        ));
    }
    if !matches!(operator, Binary::Equal | Binary::NotEqual)
        && !matches!(
            a,
            Type::Int
                | Type::Decimal
                | Type::Timestamp
                | Type::Duration
                | Type::Text
                | Type::BoundedText(_, _)
        )
    {
        return Err(Error::diagnostic(
            "D201",
            left.location,
            "only Int and Text have policy ordering",
        ));
    }
    // Diamond scalar equality is two-valued, including Option absence. Fold
    // identical operands before SQL lowering so a tautology cannot distort
    // PostgreSQL's selectivity estimates and defeat bounded index traversal.
    if left_value == right_value {
        return Ok(Predicate::Truth(Operand::Constant(Value(ValueKind::Bool(
            matches!(
                operator,
                Binary::Equal | Binary::LessEqual | Binary::GreaterEqual
            ),
        )))));
    }
    Ok(Predicate::Compare(left_value, operator, right_value))
}

impl Operand {
    fn value(&self, rows: &[&BTreeMap<String, Value>]) -> Result<Value> {
        match self {
            Self::Constant(value) => Ok(value.clone()),
            Self::Field(index, field, ty) => {
                let value = rows
                    .get(*index)
                    .and_then(|row| row.get(field))
                    .ok_or(Error::Denied)?;
                if !value.fits(ty) {
                    return Err(Error::Denied);
                }
                Ok(value.clone())
            }
        }
    }
    fn sql(&self, parameters: &mut Vec<rusqlite::types::Value>) -> Result<String> {
        match self {
            Self::Constant(value) => {
                parameters.push(sql_value(value)?);
                Ok("?".into())
            }
            Self::Field(0, field, _) => Ok(format!("\"{field}\"")),
            Self::Field(_, _, _) => Err(Error::unsupported(
                crate::Location::default(),
                "post-state SQL policy translation",
            )),
        }
    }
}

type LookupEvaluator<'a> = dyn FnMut(&str, &Value, Option<&str>) -> Result<bool> + 'a;

impl Predicate {
    fn evaluate(
        &self,
        rows: &[&BTreeMap<String, Value>],
        principal: &Principal,
        lookup: &mut LookupEvaluator<'_>,
    ) -> Result<bool> {
        match self {
            Self::Truth(value) => value.value(rows)?.boolean(),
            Self::Authenticated => Ok(principal.id().is_some()),
            Self::IsUser(value) => match value.value(rows)?.0 {
                ValueKind::Id(_, id) => Ok(principal.id() == Some(id.as_str())),
                _ => Err(Error::Denied),
            },
            Self::HasRole(role) => Ok(principal.has_role(role)),
            Self::Lookup(name, scope, role) => lookup(name, &scope.value(rows)?, role.as_deref()),
            Self::Not(value) => Ok(!value.evaluate(rows, principal, lookup)?),
            Self::And(a, b) => {
                Ok(a.evaluate(rows, principal, lookup)? && b.evaluate(rows, principal, lookup)?)
            }
            Self::Or(a, b) => {
                Ok(a.evaluate(rows, principal, lookup)? || b.evaluate(rows, principal, lookup)?)
            }
            Self::Compare(a, op, b) => compare(&a.value(rows)?, *op, &b.value(rows)?),
        }
    }
    fn sql(
        &self,
        principal: &Principal,
        parameters: &mut Vec<rusqlite::types::Value>,
    ) -> Result<String> {
        match self {
            Self::Lookup(..) => Err(Error::Denied),
            Self::Truth(value) => Ok(format!("({} = 1)", value.sql(parameters)?)),
            Self::Authenticated => Ok(if principal.id().is_some() { "1" } else { "0" }.into()),
            Self::HasRole(role) => Ok(if principal.has_role(role) { "1" } else { "0" }.into()),
            Self::IsUser(value) => {
                let Some(id) = principal.id() else {
                    return Ok("0".into());
                };
                let field = value.sql(parameters)?;
                parameters.push(rusqlite::types::Value::Text(id.into()));
                Ok(format!("({field} = ?)"))
            }
            Self::Not(value) => Ok(format!("(NOT {})", value.sql(principal, parameters)?)),
            Self::And(a, b) | Self::Or(a, b) => {
                let a = a.sql(principal, parameters)?;
                let b = b.sql(principal, parameters)?;
                Ok(format!(
                    "({a} {} {b})",
                    if matches!(self, Self::And(_, _)) {
                        "AND"
                    } else {
                        "OR"
                    }
                ))
            }
            Self::Compare(a, op, b) => Ok(format!(
                "({} {} {})",
                a.sql(parameters)?,
                operator_sql(*op)?,
                b.sql(parameters)?
            )),
        }
    }
}

fn operator_sql(operator: Binary) -> Result<&'static str> {
    match operator {
        Binary::Equal => Ok("="),
        Binary::NotEqual => Ok("<>"),
        Binary::Less => Ok("<"),
        Binary::LessEqual => Ok("<="),
        Binary::Greater => Ok(">"),
        Binary::GreaterEqual => Ok(">="),
        _ => Err(Error::Denied),
    }
}

pub(crate) fn compare(a: &Value, operator: Binary, b: &Value) -> Result<bool> {
    if matches!(operator, Binary::Equal | Binary::NotEqual) {
        return Ok(if operator == Binary::Equal {
            a == b
        } else {
            a != b
        });
    }
    let ordering = match (&a.0, &b.0) {
        (ValueKind::Int(a), ValueKind::Int(b))
        | (ValueKind::Decimal(a), ValueKind::Decimal(b))
        | (ValueKind::Timestamp(a), ValueKind::Timestamp(b))
        | (ValueKind::Duration(a), ValueKind::Duration(b)) => a.cmp(b),
        (ValueKind::Text(a), ValueKind::Text(b)) => a.cmp(b),
        _ => return Err(Error::Input("incompatible ordering operands".into())),
    };
    Ok(match operator {
        Binary::Less => ordering.is_lt(),
        Binary::LessEqual => ordering.is_le(),
        Binary::Greater => ordering.is_gt(),
        Binary::GreaterEqual => ordering.is_ge(),
        _ => false,
    })
}

pub(crate) fn sql_value(value: &Value) -> Result<rusqlite::types::Value> {
    Ok(match &value.0 {
        ValueKind::Int(value) => rusqlite::types::Value::Integer(*value),
        ValueKind::Bool(value) => rusqlite::types::Value::Integer(i64::from(*value)),
        ValueKind::Text(value) | ValueKind::Id(_, value) => {
            rusqlite::types::Value::Text(value.clone())
        }
        ValueKind::None => rusqlite::types::Value::Null,
        ValueKind::Some(value) => return sql_value(value),
        _ => return Err(Error::Input("non-scalar database value".into())),
    })
}

fn membership(
    expression: &Expr,
    method: &str,
    arguments: &[(Option<String>, Expr)],
    action: &PolicyAction,
    record: &Record,
    program: &CheckedProgram,
) -> Result<Predicate> {
    let storage = program.storage.as_ref().ok_or_else(|| {
        Error::unsupported(
            expression.location,
            "relational principal predicates require relational-v1",
        )
    })?;
    let ExprKind::Text(name) = &arguments[0].1.kind else {
        return Err(Error::diagnostic(
            "D201",
            expression.location,
            "lookup name must be a Text literal",
        ));
    };
    let spec = storage.lookups.get(name).ok_or_else(|| {
        Error::diagnostic(
            "D201",
            expression.location,
            "unknown terminal membership lookup",
        )
    })?;
    let (scope, ty) = operand(&arguments[1].1, action, record, program)?;
    if ty != program.records[&program.resources[&spec.resource]].fields[&spec.scope].ty {
        return Err(Error::diagnostic(
            "D201",
            expression.location,
            "lookup scope type mismatch",
        ));
    }
    let role = if method == "has_role_in" {
        let field = spec.role.as_ref().ok_or_else(|| {
            Error::diagnostic("D201", expression.location, "lookup has no role field")
        })?;
        let role = match &arguments[2].1.kind {
            ExprKind::Role(s) | ExprKind::Text(s) => s.clone(),
            _ => {
                return Err(Error::diagnostic(
                    "D201",
                    expression.location,
                    "lookup role must be a literal",
                ));
            }
        };
        let ty = &program.records[&program.resources[&spec.resource]].fields[field].ty;
        let valid = match ty {
            Type::Enum(n) => program.enums[n]
                .variants
                .keys()
                .any(|t| crate::project::resolve::public_name(t) == role),
            Type::BoundedText(min, max) => (*min..=*max).contains(&role.chars().count()),
            _ => false,
        };
        if !valid {
            return Err(Error::diagnostic(
                "D201",
                expression.location,
                "lookup role is outside its declared type",
            ));
        }
        Some(role)
    } else {
        None
    };
    Ok(Predicate::Lookup(name.clone(), scope, role))
}
