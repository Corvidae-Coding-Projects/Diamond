//! Schema-owned controls and fixed-code error presentation.

use super::{Attribute, Node, error};
use crate::error::{Location, Result};
use crate::project::schemas::Field;
use crate::scalar_input::Scalar;
use std::collections::{BTreeMap, BTreeSet};

pub(super) fn controls(nodes: &mut Vec<Node>, schema: &BTreeMap<String, Field>) -> Result<()> {
    let mut found = BTreeSet::new();
    visit(nodes, schema, &mut found)?;
    if found.len() != schema.len() {
        return Err(error(
            Location::default(),
            "schema form must bind every declared field once",
        ));
    }
    Ok(())
}

fn visit(
    nodes: &mut Vec<Node>,
    schema: &BTreeMap<String, Field>,
    found: &mut BTreeSet<String>,
) -> Result<()> {
    for mut node in std::mem::take(nodes) {
        let mut field = None;
        if let Node::Field(control) = &mut node {
            let definition = schema
                .get(&control.name)
                .ok_or_else(|| error(Location::default(), "undeclared schema form field"))?;
            if !found.insert(control.name.clone()) {
                return Err(error(Location::default(), "duplicate schema form field"));
            }
            let inner = if let Scalar::Optional { inner } = &definition.codec {
                inner.as_ref()
            } else {
                &definition.codec
            };
            if control.checked != matches!(definition.codec, Scalar::Bool)
                || control.multiline && !matches!(inner, Scalar::Text { .. })
            {
                return Err(error(
                    Location::default(),
                    "field draft/control does not match its scalar schema",
                ));
            }
            control.definition = Some(definition.clone());
            nodes.push(node);
            continue;
        }
        if matches!(node, Node::Submit(_) | Node::Status(_)) {
            nodes.push(node);
            continue;
        }
        if let Node::Element {
            tag,
            attributes,
            children,
        } = &mut node
        {
            if tag == "form" {
                return Err(error(Location::default(), "schema forms cannot nest"));
            }
            if ["input", "textarea", "select"].contains(&tag.as_str()) {
                let name = text(attributes, "name")
                    .ok_or_else(|| {
                        error(
                            Location::default(),
                            "schema form controls require a static field name",
                        )
                    })?
                    .to_owned();
                let definition = schema
                    .get(&name)
                    .ok_or_else(|| error(Location::default(), "undeclared schema form field"))?;
                if !found.insert(name.clone()) {
                    return Err(error(Location::default(), "duplicate schema form field"));
                }
                check_control(tag, attributes, children, &definition.codec)?;
                attributes.push(("field".into(), Attribute::Field(name.clone())));
                field = Some(name);
            } else if text(attributes, "name").is_some() {
                return Err(error(
                    Location::default(),
                    "named controls must belong to the schema",
                ));
            }
            visit(children, schema, found)?;
        } else if !matches!(node, Node::Text(_) | Node::Value(_)) {
            return Err(error(
                Location::default(),
                "schema forms require static controls",
            ));
        }
        nodes.push(node);
        if let Some(name) = field {
            nodes.push(Node::Element {
                tag: "span".into(),
                attributes: vec![
                    ("data-diamond-field-error".into(), Attribute::Text(name)),
                    ("role".into(), Attribute::Text("alert".into())),
                ],
                children: vec![],
            });
        }
    }
    Ok(())
}

fn text<'a>(attributes: &'a [(String, Attribute)], name: &str) -> Option<&'a str> {
    attributes.iter().find_map(|(key, value)| {
        if key == name {
            if let Attribute::Text(value) = value {
                Some(value.as_str())
            } else {
                None
            }
        } else {
            None
        }
    })
}

fn check_control(
    tag: &str,
    attributes: &mut Vec<(String, Attribute)>,
    children: &[Node],
    codec: &Scalar,
) -> Result<()> {
    let invalid = || {
        error(
            Location::default(),
            "schema controls must match their scalar kind; password/file/hidden fields are forbidden",
        )
    };
    if attributes.iter().any(|(name, _)| {
        [
            "required",
            "minlength",
            "maxlength",
            "pattern",
            "min",
            "max",
            "step",
            "multiple",
            "form",
        ]
        .contains(&name.as_str())
    }) {
        return Err(error(
            Location::default(),
            "schema field constraints are owned by the compiler",
        ));
    }
    let inner = if let Scalar::Optional { inner } = codec {
        inner
    } else {
        codec
    };
    let checkbox = tag == "input" && text(attributes, "type") == Some("checkbox");
    if checkbox {
        if !matches!(codec, Scalar::Bool) {
            return Err(invalid());
        }
    } else if tag == "select" {
        let mut allowed: BTreeSet<String> = match inner {
            Scalar::Bool => BTreeSet::from(["false".into(), "true".into()]),
            Scalar::Choice { choices, .. } => choices.keys().cloned().collect(),
            _ => return Err(invalid()),
        };
        if matches!(codec, Scalar::Optional { .. }) {
            allowed.insert(String::new());
        }
        let actual = children
            .iter()
            .filter(|node| !matches!(node, Node::Text(value) if value.trim().is_empty()))
            .map(|node| match node {
                Node::Element {
                    tag, attributes, ..
                } if tag == "option" => text(attributes, "value")
                    .map(str::to_owned)
                    .ok_or_else(invalid),
                _ => Err(invalid()),
            })
            .collect::<Result<BTreeSet<_>>>()?;
        if actual != allowed {
            return Err(error(
                Location::default(),
                "schema select options must exactly match its allowed values",
            ));
        }
    } else {
        if !matches!(inner, Scalar::Text { .. } | Scalar::Int | Scalar::Id { .. })
            || tag == "input" && text(attributes, "type").is_some_and(|kind| kind != "text")
        {
            return Err(invalid());
        }
        let maximum = match inner {
            Scalar::Text { max, .. } => max.saturating_mul(2).min(8192),
            _ => 128,
        };
        attributes.push(("maxlength".into(), Attribute::Text(maximum.to_string())));
    }
    Ok(())
}
