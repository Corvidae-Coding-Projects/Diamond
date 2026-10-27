//! Semantics-preserving source formatting built on the real Diamond parser.

use crate::error::{Error, Result};

/// Format one valid Diamond source document.
///
/// The formatter canonicalizes indentation to two spaces per source block and
/// line endings to LF. Comments, string contents, and template-relative
/// whitespace are preserved. Invalid input is rejected before output is returned.
pub(crate) fn format(source: &str) -> Result<String> {
    let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
    let before = crate::parser::project_module(&normalized)?;
    let formatted = formatted_text(&normalized)?;
    let after = crate::parser::project_module(&formatted)?;
    if semantic_syntax(&before)? != semantic_syntax(&after)? {
        return Err(Error::Input(
            "formatter would change the parsed Diamond program".into(),
        ));
    }
    Ok(formatted)
}

fn formatted_text(normalized: &str) -> Result<String> {
    let mut indentation = vec![0usize];
    let mut html: Option<usize> = None;
    let mut output = Vec::new();

    for line in normalized.lines() {
        let trimmed = line.trim_start_matches(' ');
        let spaces = line.len().saturating_sub(trimmed.len());

        if let Some(old_parent) = html {
            if trimmed.is_empty() || spaces > old_parent {
                output.push(line.to_owned());
                continue;
            }
            html = None;
        }

        if trimmed.is_empty() {
            output.push(String::new());
            continue;
        }

        if trimmed.starts_with('#') {
            let depth = if spaces
                > *indentation
                    .last()
                    .ok_or(Error::Limit("formatter indentation"))?
            {
                indentation.len()
            } else {
                indentation
                    .iter()
                    .rposition(|value| *value <= spaces)
                    .unwrap_or_default()
            };
            output.push(format!("{}{}", "  ".repeat(depth), trimmed));
            continue;
        }

        while spaces
            < *indentation
                .last()
                .ok_or(Error::Limit("formatter indentation"))?
        {
            indentation.pop();
        }
        if spaces
            != *indentation
                .last()
                .ok_or(Error::Limit("formatter indentation"))?
        {
            indentation.push(spaces);
        }
        let new_spaces = (indentation.len() - 1) * 2;
        output.push(format!("{}{}", " ".repeat(new_spaces), trimmed));
        if trimmed == "html:" {
            html = Some(spaces);
        }
    }

    Ok(output.join("\n") + "\n")
}

fn semantic_syntax(module: &crate::syntax::ProjectModule) -> Result<serde_json::Value> {
    let mut value = serde_json::to_value(module)
        .map_err(|_| Error::Input("formatter syntax comparison".into()))?;
    remove_locations(&mut value);
    Ok(value)
}

fn remove_locations(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Array(values) => values.iter_mut().for_each(remove_locations),
        serde_json::Value::Object(values) => {
            values.remove("location");
            values.values_mut().for_each(remove_locations);
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formatting_is_semantic_and_idempotent() -> Result<()> {
        let source = "# café formatter fixture\r\nfn message() -> Text:\r\n    # nested comment stays with this block\r\n    \"snowman ☃ and escaped \\\\n text\"\r\n\r\nfn view() -> Html:\r\n    html:\r\n        <main>\r\n          <p>Unicode 雪 and template spacing</p>\r\n        </main>\r\n";
        let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
        let formatted = formatted_text(&normalized)?;
        let before = semantic_syntax(&crate::parser::project_module(&normalized)?)?;
        let after = semantic_syntax(&crate::parser::project_module(&formatted)?)?;
        assert_eq!(before, after, "formatted text:\n{formatted}");
        let formatted = format(source)?;
        assert_eq!(format(&formatted)?, formatted);
        assert!(formatted.contains("  # nested comment stays with this block"));
        assert!(formatted.contains("snowman ☃ and escaped \\\\n text"));
        Ok(())
    }
}
