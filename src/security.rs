//! Central, fail-closed security properties for managed Diamond types.

use crate::hir::CheckedProgram;
use crate::value::Type;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum MessageSafety {
    Allowed,
    Forbidden,
}

/// Security properties needed by compiler and runtime sinks.
///
/// These properties are derived from the complete type rather than annotations
/// supplied at individual call sites. Unknown or excessively recursive shapes
/// are treated as unsafe.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct TypeSecurity {
    pub(crate) contains_secret: bool,
    pub(crate) browser_managed: bool,
    pub(crate) client_wire_safe: bool,
    component_messages: MessageSafety,
}

impl TypeSecurity {
    const fn public_wire() -> Self {
        Self {
            contains_secret: false,
            browser_managed: true,
            client_wire_safe: true,
            component_messages: MessageSafety::Allowed,
        }
    }

    const fn server_only() -> Self {
        Self {
            contains_secret: false,
            browser_managed: false,
            client_wire_safe: false,
            component_messages: MessageSafety::Forbidden,
        }
    }

    const fn secret() -> Self {
        Self {
            contains_secret: true,
            browser_managed: false,
            client_wire_safe: false,
            component_messages: MessageSafety::Forbidden,
        }
    }

    const fn browser_only() -> Self {
        Self {
            contains_secret: false,
            browser_managed: true,
            client_wire_safe: false,
            component_messages: MessageSafety::Forbidden,
        }
    }

    const fn combine(self, other: Self) -> Self {
        Self {
            contains_secret: self.contains_secret || other.contains_secret,
            browser_managed: self.browser_managed && other.browser_managed,
            client_wire_safe: self.client_wire_safe && other.client_wire_safe,
            component_messages: if self.component_message_safe() && other.component_message_safe() {
                MessageSafety::Allowed
            } else {
                MessageSafety::Forbidden
            },
        }
    }

    pub(crate) const fn component_message_safe(self) -> bool {
        matches!(self.component_messages, MessageSafety::Allowed)
    }
}

/// Derive the security properties of a managed type.
pub(crate) fn type_security(ty: &Type, program: &CheckedProgram) -> TypeSecurity {
    analyze(ty, program, 0)
}

fn analyze(ty: &Type, program: &CheckedProgram, depth: usize) -> TypeSecurity {
    if depth > 32 {
        return TypeSecurity::secret();
    }
    match ty {
        Type::Int
        | Type::Decimal
        | Type::Timestamp
        | Type::Duration
        | Type::Bool
        | Type::Text
        | Type::BoundedText(..)
        | Type::Unit
        | Type::Id(_)
        | Type::Never => TypeSecurity::public_wire(),
        Type::Secret(_) => TypeSecurity::secret(),
        Type::Blob(_) | Type::Stream(_) | Type::DataError => TypeSecurity::browser_only(),
        Type::List(inner) | Type::Option(inner) | Type::Result(inner) | Type::Success(inner) => {
            analyze(inner, program, depth + 1)
        }
        Type::AppResult(value, error) => {
            analyze(value, program, depth + 1).combine(analyze(error, program, depth + 1))
        }
        Type::Action(value, error) => {
            let mut security =
                analyze(value, program, depth + 1).combine(analyze(error, program, depth + 1));
            // Actions use their dedicated dispatcher; no generic wire/storage escape.
            security.client_wire_safe = false;
            security
        }
        Type::Record(name) => {
            program
                .records
                .get(name)
                .map_or_else(TypeSecurity::secret, |record| {
                    record
                        .fields
                        .values()
                        .fold(TypeSecurity::public_wire(), |security, field| {
                            security.combine(analyze(&field.ty, program, depth + 1))
                        })
                })
        }
        Type::Enum(name) => {
            program
                .enums
                .get(name)
                .map_or_else(TypeSecurity::secret, |enumeration| {
                    enumeration
                        .variants
                        .values()
                        .flatten()
                        .fold(TypeSecurity::public_wire(), |security, (_, field)| {
                            security.combine(analyze(field, program, depth + 1))
                        })
                })
        }
        Type::Html | Type::Url | Type::Response => TypeSecurity::server_only(),
    }
}
