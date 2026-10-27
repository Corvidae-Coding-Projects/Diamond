//! Checked managed native controls; only a containing schema can bind a field.

use super::{Expression, Lower, Node, error};
use crate::error::{Location, Result};
use crate::project::schemas::Field;
use crate::syntax::{Expr, ExprKind, HtmlAttribute};
use crate::value::Type;
use serde::Serialize;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Serialize)]
pub(in crate::project) struct Control {
    pub name: String,
    pub label: Expression,
    pub description: Option<Expression>,
    pub binding: String,
    pub checked: bool,
    pub multiline: bool,
    pub definition: Option<Field>,
}

impl Lower<'_> {
    pub(super) fn accessible_text(
        &self,
        value: Option<&HtmlAttribute>,
        locals: &BTreeMap<String, Type>,
        name: bool,
    ) -> Result<Expression> {
        let expr = match value {
            Some(HtmlAttribute::Literal(value)) => Expr {
                kind: ExprKind::Text(value.clone()),
                location: Location::default(),
            },
            Some(HtmlAttribute::Expression(value)) => value.clone(),
            None => {
                return Err(error(
                    Location::default(),
                    "accessible label/value is required",
                ));
            }
        };
        let checked = self.expression(&expr, locals)?;
        if !Type::Text.accepts(&checked.ty)
            || name && matches!(&expr.kind, ExprKind::Text(value) if value.trim().is_empty())
        {
            return Err(error(
                expr.location,
                "accessible names require nonempty Text; status/description values require Text",
            ));
        }
        Ok(checked)
    }

    pub(super) fn control(
        &self,
        attrs: &BTreeMap<&str, &HtmlAttribute>,
        locals: &BTreeMap<String, Type>,
        mutable: &BTreeMap<String, Type>,
    ) -> Result<Control> {
        if attrs.keys().any(|name| {
            ![
                "name",
                "label",
                "description",
                "bind-value",
                "bind-checked",
                "multiline",
            ]
            .contains(name)
        }) {
            return Err(error(
                Location::default(),
                "field IDs, labels, descriptions, errors and constraints are owned by the compiler",
            ));
        }
        let Some(HtmlAttribute::Literal(name)) = attrs.get("name") else {
            return Err(error(
                Location::default(),
                "field requires a static schema field name",
            ));
        };
        let label = self.accessible_text(attrs.get("label").copied(), locals, true)?;
        let description = attrs
            .get("description")
            .map(|value| self.accessible_text(Some(value), locals, false))
            .transpose()?;
        let checked = attrs.contains_key("bind-checked");
        if attrs.contains_key("bind-value") == checked {
            return Err(error(
                Location::default(),
                "field requires exactly one checked draft binding",
            ));
        }
        let Some(HtmlAttribute::Expression(Expr {
            kind: ExprKind::Name(binding),
            ..
        })) = attrs.get(if checked {
            "bind-checked"
        } else {
            "bind-value"
        })
        else {
            return Err(error(
                Location::default(),
                "field binds mutable Text/Bool draft state",
            ));
        };
        if mutable.get(binding) != Some(if checked { &Type::Bool } else { &Type::Text }) {
            return Err(error(
                Location::default(),
                "field draft binding type mismatch",
            ));
        }
        let multiline = match attrs.get("multiline") {
            None => false,
            Some(HtmlAttribute::Literal(value)) if ["true", "false"].contains(&value.as_str()) => {
                value == "true"
            }
            _ => {
                return Err(error(
                    Location::default(),
                    "multiline requires a static Boolean spelling",
                ));
            }
        };
        Ok(Control {
            name: name.clone(),
            label,
            description,
            binding: binding.clone(),
            checked,
            multiline,
            definition: None,
        })
    }
}

pub(super) fn bound(nodes: &[Node]) -> Result<()> {
    walk(nodes, false)
}
fn walk(nodes: &[Node], in_form: bool) -> Result<()> {
    for node in nodes {
        match node {
            Node::Field(field) if field.definition.is_none() => {
                return Err(error(
                    Location::default(),
                    "field belongs inside its own schema form",
                ));
            }
            Node::Submit(_) if !in_form => {
                return Err(error(
                    Location::default(),
                    "submit belongs inside its own schema form",
                ));
            }
            Node::Element {
                attributes,
                children,
                ..
            } => walk(
                children,
                in_form
                    || attributes
                        .iter()
                        .any(|(_, value)| matches!(value, super::Attribute::Form { .. })),
            )?,
            Node::When(_, children)
            | Node::Route(_, children)
            | Node::Each { children, .. }
            | Node::NamedRoute { children, .. } => walk(children, in_form)?,
            _ => {}
        }
    }
    Ok(())
}
