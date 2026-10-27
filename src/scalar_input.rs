//! Finite scalar input codecs shared by form and reactive URL boundaries.

use crate::hir::CheckedProgram;
use crate::value::{Type, Value, ValueKind};
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind")]
pub(crate) enum Scalar {
    Text {
        min: usize,
        max: usize,
    },
    Int,
    Bool,
    Id {
        tag: String,
    },
    Choice {
        name: String,
        choices: BTreeMap<String, String>,
    },
    Optional {
        inner: Box<Self>,
    },
}

#[derive(Clone, Copy, Debug, Serialize)]
pub(crate) enum FieldError {
    Required,
    TooShort,
    TooLong,
    InvalidInteger,
    InvalidBoolean,
    InvalidId,
    InvalidChoice,
}

impl Scalar {
    pub(crate) fn checked(ty: &Type, program: &CheckedProgram) -> Option<Self> {
        Some(match ty {
            Type::Text => Self::Text {
                min: 0,
                max: crate::MAX_SOURCE_BYTES,
            },
            Type::BoundedText(min, max) => Self::Text {
                min: *min,
                max: *max,
            },
            Type::Int => Self::Int,
            Type::Bool => Self::Bool,
            Type::Id(tag) => Self::Id { tag: tag.clone() },
            Type::Enum(name) => {
                let definition = program.enums.get(name)?;
                if definition
                    .variants
                    .values()
                    .any(|fields| !fields.is_empty())
                {
                    return None;
                }
                Self::Choice {
                    name: name.clone(),
                    choices: definition
                        .variants
                        .keys()
                        .map(|name| {
                            (
                                crate::project::resolve::public_name(name).to_owned(),
                                name.clone(),
                            )
                        })
                        .collect(),
                }
            }
            Type::Option(inner) if !matches!(**inner, Type::Option(_)) => Self::Optional {
                inner: Box::new(Self::checked(inner, program)?),
            },
            _ => return None,
        })
    }

    pub(crate) fn decode(&self, raw: &str) -> std::result::Result<Value, FieldError> {
        if raw.len() > 16_384 {
            return Err(FieldError::TooLong);
        }
        if let Self::Optional { inner } = self {
            return if raw.is_empty() {
                Ok(Value(ValueKind::None))
            } else {
                inner
                    .decode(raw)
                    .map(|value| Value(ValueKind::Some(Box::new(value))))
            };
        }
        if raw.is_empty() && !matches!(self, Self::Text { min: 0, .. }) {
            return Err(FieldError::Required);
        }
        Ok(Value(match self {
            Self::Text { min, max } => {
                let length = raw.chars().count();
                if length < *min {
                    return Err(FieldError::TooShort);
                }
                if length > *max {
                    return Err(FieldError::TooLong);
                }
                ValueKind::Text(raw.into())
            }
            Self::Int => {
                let value: i64 = raw.parse().map_err(|_| FieldError::InvalidInteger)?;
                if value.to_string() != raw {
                    return Err(FieldError::InvalidInteger);
                }
                ValueKind::Int(value)
            }
            Self::Bool => ValueKind::Bool(match raw {
                "true" => true,
                "false" => false,
                _ => return Err(FieldError::InvalidBoolean),
            }),
            Self::Id { tag } => {
                if !crate::auth::valid_id(raw) {
                    return Err(FieldError::InvalidId);
                }
                ValueKind::Id(tag.clone(), raw.into())
            }
            Self::Choice { name, choices } => ValueKind::Variant(
                name.clone(),
                choices.get(raw).ok_or(FieldError::InvalidChoice)?.clone(),
                vec![],
            ),
            Self::Optional { .. } => return Err(FieldError::InvalidChoice),
        }))
    }

    pub(crate) fn format(&self, value: &Value) -> Option<String> {
        match (self, &value.0) {
            (Self::Optional { .. }, ValueKind::None) => Some(String::new()),
            (Self::Optional { inner }, ValueKind::Some(value)) => inner.format(value),
            (Self::Choice { choices, .. }, ValueKind::Variant(_, variant, fields))
                if fields.is_empty() =>
            {
                choices
                    .iter()
                    .find(|(_, name)| *name == variant)
                    .map(|(name, _)| name.clone())
            }
            _ => {
                let raw = value.display_text().ok()?;
                self.decode(&raw).ok()?;
                Some(raw)
            }
        }
    }
}
