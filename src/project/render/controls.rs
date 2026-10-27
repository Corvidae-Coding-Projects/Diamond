//! The native half of generated control identities and markup.

use crate::error::{Error, Result};
use crate::project::lower::controls::Control;
use crate::scalar_input::Scalar;
use crate::value::Value;
use std::fmt::Write;

pub(super) fn key(value: &Value) -> Result<String> {
    use crate::value::ValueKind;
    match &value.0 {
        ValueKind::Int(value) => Ok(format!("i:{value}")),
        ValueKind::Text(value) => Ok(format!("t:{value}")),
        ValueKind::Id(tag, value) => Ok(format!("d:{tag}:{value}")),
        _ => Err(Error::Denied),
    }
}

pub(super) fn encode(raw: &str) -> String {
    let mut output = String::new();
    for byte in raw.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            output.push(char::from(byte));
        } else {
            let _ = write!(output, "%{byte:02X}");
        }
    }
    output
}

pub(super) fn name(raw: &str) -> Result<()> {
    if raw.trim().is_empty() || raw.chars().count() > 256 {
        return Err(Error::Input(
            "accessible name must contain 1..256 nonblank Text scalars".into(),
        ));
    }
    Ok(())
}

pub(super) fn message(raw: &str) -> Result<()> {
    if raw.chars().count() > 512 {
        return Err(Error::Limit("status text (512 scalars)"));
    }
    Ok(())
}

pub(super) fn field(
    control: &Control,
    label: &str,
    description: Option<&str>,
    draft: &Value,
    identity: &str,
) -> Result<String> {
    name(label)?;
    let definition = control.definition.as_ref().ok_or(Error::Denied)?;
    let codec = if let Scalar::Optional { inner } = &definition.codec {
        inner.as_ref()
    } else {
        &definition.codec
    };
    let id = format!("diamond-control-{}", encode(identity));
    let escape = crate::template::escape;
    let description = description.filter(|value| !value.is_empty());
    let described = if description.is_some() {
        format!("{id}-description {id}-error")
    } else {
        format!("{id}-error")
    };
    let common = format!(
        "id=\"{id}\" name=\"{}\" data-diamond-field=\"{}\" data-diamond-managed=\"true\" aria-describedby=\"{described}\" aria-invalid=\"false\"",
        escape(&control.name),
        escape(&control.name)
    );
    let value = draft.display_text()?;
    let mut output = format!(
        "<div class=\"diamond-field\"><label for=\"{id}\">{}</label>",
        escape(label)
    );
    if control.checked {
        write!(
            output,
            "<input type=\"checkbox\" {common}{}>",
            if draft.boolean()? {
                " checked=\"\""
            } else {
                ""
            }
        )
        .map_err(|_| Error::Limit("HTML formatting"))?;
    } else if matches!(codec, Scalar::Bool | Scalar::Choice { .. }) {
        write!(output, "<select {common}>").map_err(|_| Error::Limit("HTML formatting"))?;
        let mut choices = match codec {
            Scalar::Bool => vec!["false".to_owned(), "true".to_owned()],
            Scalar::Choice { choices, .. } => choices.keys().cloned().collect(),
            _ => return Err(Error::Denied),
        };
        if matches!(definition.codec, Scalar::Optional { .. }) {
            choices.insert(0, String::new());
        }
        if !choices.contains(&value) {
            return Err(Error::Input("select draft has no allowed option".into()));
        }
        for choice in choices {
            write!(
                output,
                "<option value=\"{}\"{}>{}</option>",
                escape(&choice),
                if choice == value {
                    " selected=\"\""
                } else {
                    ""
                },
                escape(if choice.is_empty() {
                    "Not set"
                } else {
                    &choice
                })
            )
            .map_err(|_| Error::Limit("HTML formatting"))?;
        }
        output.push_str("</select>");
    } else {
        let maximum = match codec {
            Scalar::Text { max, .. } => max.saturating_mul(2).min(8192),
            _ => 128,
        };
        if control.multiline {
            write!(
                output,
                "<textarea {common} maxlength=\"{maximum}\">{}{}</textarea>",
                if value.starts_with('\n') { "\n" } else { "" },
                escape(&value)
            )
            .map_err(|_| Error::Limit("HTML formatting"))?;
        } else {
            write!(
                output,
                "<input type=\"text\" {common} maxlength=\"{maximum}\" value=\"{}\">",
                escape(&value)
            )
            .map_err(|_| Error::Limit("HTML formatting"))?;
        }
    }
    if let Some(description) = description {
        write!(
            output,
            "<span id=\"{id}-description\">{}</span>",
            escape(description)
        )
        .map_err(|_| Error::Limit("HTML formatting"))?;
    }
    write!(output,"<span id=\"{id}-error\" data-diamond-field-error=\"{}\" role=\"alert\" aria-atomic=\"true\"></span></div>",escape(&control.name)).map_err(|_|Error::Limit("HTML formatting"))?;
    Ok(output)
}

pub(super) fn field_cost(control: &Control) -> Result<usize> {
    let codec = &control.definition.as_ref().ok_or(Error::Denied)?.codec;
    let codec = if let Scalar::Optional { inner } = codec {
        inner.as_ref()
    } else {
        codec
    };
    Ok(12
        + 2 * match codec {
            Scalar::Choice { choices, .. } => choices.len(),
            Scalar::Bool => 2,
            _ => 0,
        })
}
