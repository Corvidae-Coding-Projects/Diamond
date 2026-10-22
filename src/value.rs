//! Managed interpreter values. Authority is deliberately not a value variant.

use std::collections::BTreeMap;
use std::fmt;

use crate::error::{Error, Result};

#[derive(serde::Serialize, Clone, Debug, PartialEq, Eq)]
pub(crate) enum Type {
    Int,
    Decimal,
    Timestamp,
    Duration,
    Bool,
    Text,
    BoundedText(usize, usize),
    Unit,
    Html,
    Url,
    Response,
    Id(String),
    Record(String),
    Enum(String),
    List(Box<Self>),
    DataError,
    Result(Box<Self>),
    AppResult(Box<Self>, Box<Self>),
    Success(Box<Self>),
    Option(Box<Self>),
    Never,
}

impl Type {
    pub fn accepts(&self, other: &Self) -> bool {
        self == other
            || matches!(
                (self, other),
                (Self::Response, Self::Html | Self::Url | Self::Text)
            )
            || matches!(other, Self::Never)
            || matches!((self, other), (Self::Text, Self::BoundedText(_, _)))
            || match (self, other) {
                (Self::AppResult(a, e), Self::AppResult(b, f)) => a.accepts(b) && e.accepts(f),
                (Self::AppResult(a, _) | Self::Result(a) | Self::Success(a), Self::Success(b))
                | (Self::Result(a), Self::Result(b))
                | (Self::Option(a), Self::Option(b))
                | (Self::List(a), Self::List(b)) => a.accepts(b),
                _ => false,
            }
    }
}

impl fmt::Display for Type {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Int => f.write_str("Int"),
            Self::Decimal => f.write_str("Decimal"),
            Self::Timestamp => f.write_str("Timestamp"),
            Self::Duration => f.write_str("Duration"),
            Self::AppResult(a, e) => write!(f, "Result<{a}, {e}>"),
            Self::Success(a) => write!(f, "Result<{a}, inferred error>"),
            Self::Bool => f.write_str("Bool"),
            Self::Text => f.write_str("Text"),
            Self::BoundedText(a, b) => write!(f, "Text<length: {a}..{b}>"),
            Self::Unit => f.write_str("Unit"),
            Self::Html => f.write_str("Html"),
            Self::Url => f.write_str("Url"),
            Self::Response => f.write_str("Response"),
            Self::Id(tag) => write!(f, "Id<{tag:?}>"),
            Self::Record(name) | Self::Enum(name) => f.write_str(name),
            Self::List(ty) => write!(f, "List<{ty}>"),
            Self::DataError => f.write_str("DataError"),
            Self::Result(ty) => write!(f, "Result<{ty}, DataError>"),
            Self::Option(ty) => write!(f, "Option<{ty}>"),
            Self::Never => f.write_str("Never"),
        }
    }
}

/// An immutable result returned by a checked Diamond program.
///
/// ```
/// let program = diamond::compile("fn main() -> Int:\n  6 * 7\n")?;
/// assert_eq!(program.evaluate()?.display_text()?, "42");
/// # Ok::<(), diamond::Error>(())
/// ```
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Value(pub(crate) ValueKind);

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum ValueKind {
    Int(i64),
    Decimal(i64),
    Timestamp(i64),
    Duration(i64),
    Bool(bool),
    Text(String),
    Unit,
    Html(String),
    Url(String),
    Response(u16, String),
    Id(String, String),
    Record(String, BTreeMap<String, Value>),
    Variant(String, String, Vec<Value>),
    List(Vec<Value>),
    DataError(Failure),
    Ok(Box<Value>),
    AppErr(Box<Value>),
    Err(Failure),
    Some(Box<Value>),
    None,
}

/// Expected failures retain their safe transport class while used in Diamond.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Failure {
    Denied,
    InvalidInput,
    Aborted,
    Conflict,
    Message(String),
}

impl Failure {
    pub fn from_error(error: Error) -> Result<Self> {
        match error {
            Error::Denied => Ok(Self::Denied),
            Error::Input(_) => Ok(Self::InvalidInput),
            Error::Aborted => Ok(Self::Aborted),
            Error::Conflict => Ok(Self::Conflict),
            Error::Propagation(message) => Ok(Self::Message(message)),
            other => Err(other),
        }
    }

    pub fn into_error(self) -> Error {
        match self {
            Self::Denied => Error::Denied,
            Self::InvalidInput => {
                Error::Input("input does not satisfy its declared shape or bounds".into())
            }
            Self::Aborted => Error::Aborted,
            Self::Conflict => Error::Conflict,
            Self::Message(message) => Error::Propagation(message),
        }
    }

    pub fn message(&self) -> &str {
        match self {
            Self::Denied => "Access denied or resource not found.",
            Self::InvalidInput => "Input does not satisfy its declared shape or bounds.",
            Self::Aborted => "Transaction aborted.",
            Self::Conflict => "Transaction aborted by a concurrency conflict. Retry explicitly.",
            Self::Message(message) => message,
        }
    }
}

// Weight units include conservative per-node/map overhead and owned bytes. These
// are interpreter work limits, not an allocator accounting or host-memory quota.
pub(crate) const VALUE_NODE_WEIGHT: usize = 128;
const MAX_VALUE_WEIGHT: usize = 4 * 1_048_576;
const MAX_VALUE_DEPTH: usize = 64;
const EVALUATION_VALUE_WORK: usize = 64 * 1_048_576;

#[derive(Debug)]
pub(crate) struct ValueBudget {
    remaining: usize,
}

impl ValueBudget {
    pub fn new() -> Self {
        Self {
            remaining: EVALUATION_VALUE_WORK,
        }
    }

    pub fn check_weight(weight: usize) -> Result<()> {
        if weight > MAX_VALUE_WEIGHT {
            return Err(Error::Limit("managed value size"));
        }
        Ok(())
    }

    pub fn spend(&mut self, weight: usize) -> Result<()> {
        self.remaining = self
            .remaining
            .checked_sub(weight)
            .ok_or(Error::Limit("managed value work"))?;
        Ok(())
    }

    pub fn charge(&mut self, value: &Value) -> Result<()> {
        self.spend(value.weight()?)
    }

    pub fn copy(&mut self, value: &Value) -> Result<Value> {
        self.charge(value)?;
        Ok(value.clone())
    }

    pub fn copy_fields(
        &mut self,
        values: &BTreeMap<String, Value>,
    ) -> Result<BTreeMap<String, Value>> {
        let mut copied = BTreeMap::new();
        for (name, value) in values {
            self.spend(VALUE_NODE_WEIGHT + name.len())?;
            copied.insert(name.clone(), self.copy(value)?);
        }
        Ok(copied)
    }
}

fn failure_weight(failure: &Failure) -> usize {
    if let Failure::Message(message) = failure {
        message.len()
    } else {
        0
    }
}

fn value_weight(value: &Value, depth: usize, total: &mut usize) -> Result<()> {
    if depth > MAX_VALUE_DEPTH {
        return Err(Error::Limit("managed value depth"));
    }
    *total = total.saturating_add(VALUE_NODE_WEIGHT);
    match &value.0 {
        ValueKind::Text(text)
        | ValueKind::Html(text)
        | ValueKind::Url(text)
        | ValueKind::Response(_, text) => *total = total.saturating_add(text.len()),
        ValueKind::Id(tag, id) => *total = total.saturating_add(tag.len() + id.len()),
        ValueKind::Record(tag, fields) => {
            *total = total.saturating_add(tag.len());
            for (name, field) in fields {
                *total = total.saturating_add(name.len());
                ValueBudget::check_weight(*total)?;
                value_weight(field, depth + 1, total)?;
            }
        }
        ValueKind::Variant(tag, name, values) => {
            *total = total.saturating_add(tag.len() + name.len());
            for value in values {
                value_weight(value, depth + 1, total)?;
            }
        }
        ValueKind::List(values) => {
            for value in values {
                value_weight(value, depth + 1, total)?;
            }
        }
        ValueKind::AppErr(value) | ValueKind::Ok(value) | ValueKind::Some(value) => {
            value_weight(value, depth + 1, total)?;
        }
        ValueKind::DataError(failure) | ValueKind::Err(failure) => {
            *total = total.saturating_add(failure_weight(failure));
        }
        ValueKind::Decimal(_)
        | ValueKind::Timestamp(_)
        | ValueKind::Duration(_)
        | ValueKind::Int(_)
        | ValueKind::Bool(_)
        | ValueKind::Unit
        | ValueKind::None => {}
    }
    ValueBudget::check_weight(*total)
}

impl Value {
    pub(crate) fn weight(&self) -> Result<usize> {
        let mut weight = 0;
        value_weight(self, 1, &mut weight)?;
        Ok(weight)
    }

    /// Unwrap a Diamond Result returned by `main`, preserving an explicit error.
    /// Ordinary non-Result values pass through unchanged.
    ///
    /// # Errors
    /// Returns a propagated Diamond error for `Err` values.
    pub fn into_result(self) -> Result<Self> {
        match self.0 {
            ValueKind::Ok(value) => Ok(*value),
            ValueKind::Err(failure) => Err(failure.into_error()),
            ValueKind::AppErr(value) => Err(Error::Application(*value)),
            _ => Ok(self),
        }
    }

    pub(crate) fn operation(result: Result<Self>) -> Result<Self> {
        Ok(Self(match result {
            Ok(value) => ValueKind::Ok(Box::new(value)),
            Err(error) => ValueKind::Err(Failure::from_error(error)?),
        }))
    }

    /// Return a scalar's human-readable representation without implicit coercion in Diamond.
    ///
    /// # Errors
    /// Returns an input error for records and compound values.
    pub fn display_text(&self) -> Result<String> {
        match &self.0 {
            ValueKind::Int(value) => Ok(value.to_string()),
            ValueKind::Bool(value) => Ok(value.to_string()),
            ValueKind::Text(value)
            | ValueKind::Html(value)
            | ValueKind::Url(value)
            | ValueKind::Id(_, value) => Ok(value.clone()),
            ValueKind::Unit => Ok(String::new()),
            _ => Err(Error::Input(
                "compound values need an explicit representation".into(),
            )),
        }
    }

    pub(crate) fn boolean(&self) -> Result<bool> {
        match self.0 {
            ValueKind::Bool(value) => Ok(value),
            _ => Err(Error::Input("expected Bool".into())),
        }
    }

    pub(crate) fn fits(&self, ty: &Type) -> bool {
        match (&self.0, ty) {
            (ValueKind::Decimal(_), Type::Decimal)
            | (ValueKind::Timestamp(_), Type::Timestamp)
            | (ValueKind::Duration(_), Type::Duration)
            | (ValueKind::Int(_), Type::Int)
            | (ValueKind::Bool(_), Type::Bool)
            | (ValueKind::Text(_), Type::Text)
            | (ValueKind::Unit, Type::Unit)
            | (ValueKind::Html(_), Type::Html)
            | (ValueKind::Url(_), Type::Url)
            | (ValueKind::None, Type::Option(_))
            | (ValueKind::Err(_), Type::Result(_))
            | (ValueKind::DataError(_), Type::DataError)
            | (
                ValueKind::Response(_, _)
                | ValueKind::Html(_)
                | ValueKind::Url(_)
                | ValueKind::Text(_),
                Type::Response,
            ) => true,
            (ValueKind::Text(value), Type::BoundedText(minimum, maximum)) => {
                (*minimum..=*maximum).contains(&value.chars().count())
            }
            (ValueKind::Id(tag, value), Type::Id(expected)) => {
                tag == expected && crate::auth::valid_id(value)
            }
            (ValueKind::Record(tag, _), Type::Record(expected))
            | (ValueKind::Variant(tag, _, _), Type::Enum(expected)) => tag == expected,
            (ValueKind::List(values), Type::List(inner)) => {
                values.iter().all(|value| value.fits(inner))
            }
            (ValueKind::AppErr(value), Type::AppResult(_, error)) => value.fits(error),
            (ValueKind::Ok(value), Type::AppResult(inner, _) | Type::Success(inner)) => {
                value.fits(inner)
            }
            (ValueKind::Some(value), Type::Option(inner))
            | (ValueKind::Ok(value), Type::Result(inner)) => value.fits(inner),
            _ => false,
        }
    }
}
