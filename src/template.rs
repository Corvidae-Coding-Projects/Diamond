//! Typed HTML parsing and a small allowlisted HTML surface.

use crate::error::{Error, Location, Result};
use crate::syntax::{HtmlAttribute, HtmlNode};

pub(crate) fn parse(source: &str, location: Location) -> Result<Vec<HtmlNode>> {
    let mut parser = Template {
        source,
        cursor: 0,
        location,
        nodes: 0,
        browser: false,
    };
    parser.children(None, 0)
}

pub(crate) fn parse_browser(source: &str, location: Location) -> Result<Vec<HtmlNode>> {
    Template {
        source,
        cursor: 0,
        location,
        nodes: 0,
        browser: true,
    }
    .children(None, 0)
}

struct Template<'a> {
    source: &'a str,
    cursor: usize,
    location: Location,
    nodes: usize,
    browser: bool,
}

impl Template<'_> {
    fn rest(&self) -> &str {
        &self.source[self.cursor..]
    }
    fn location(&self) -> Location {
        let prefix = &self.source[..self.cursor];
        Location {
            line: self.location.line + 1 + prefix.bytes().filter(|b| *b == b'\n').count(),
            column: prefix
                .rsplit('\n')
                .next()
                .map_or(1, |line| line.chars().count() + 1),
        }
    }
    fn error(&self, message: impl Into<String>) -> Error {
        Error::diagnostic("D020", self.location(), message)
    }
    fn whitespace(&mut self) {
        while let Some(c) = self.rest().chars().next() {
            if !c.is_whitespace() {
                break;
            }
            self.cursor += c.len_utf8();
        }
    }
    fn consume(&mut self, value: &str) -> Result<()> {
        if !self.rest().starts_with(value) {
            return Err(self.error(format!("expected {value:?} in HTML")));
        }
        self.cursor += value.len();
        Ok(())
    }
    fn name(&mut self) -> Result<String> {
        let start = self.cursor;
        while let Some(c) = self.rest().chars().next() {
            if !(c.is_ascii_alphanumeric() || c == '-') {
                break;
            }
            self.cursor += c.len_utf8();
        }
        if start == self.cursor {
            return Err(self.error("expected an HTML name"));
        }
        Ok(self.source[start..self.cursor].to_string())
    }
    fn children(&mut self, closing: Option<&str>, depth: usize) -> Result<Vec<HtmlNode>> {
        if depth > 32 {
            return Err(Error::Limit("HTML nesting"));
        }
        let mut children = Vec::new();
        while !self.rest().is_empty() {
            self.nodes += 1;
            if self.nodes > 4096 {
                return Err(Error::Limit("HTML nodes"));
            }
            if self.rest().starts_with("</") {
                let closing_location = self.location();
                self.cursor += 2;
                let tag = self.name()?;
                self.whitespace();
                self.consume(">")?;
                if Some(tag.as_str()) != closing {
                    return Err(Error::diagnostic(
                        "D020",
                        closing_location,
                        format!("unexpected closing tag </{tag}>"),
                    ));
                }
                return Ok(children);
            }
            let node = if self.rest().starts_with('<') {
                self.element(depth)?
            } else if self.rest().starts_with('{') {
                HtmlNode::Expression(self.interpolation()?)
            } else {
                let length = self.rest().find(['<', '{']).unwrap_or(self.rest().len());
                let text = self.rest()[..length].to_string();
                self.cursor += length;
                HtmlNode::Text(text)
            };
            children.push(node);
        }
        if let Some(tag) = closing {
            return Err(self.error(format!("missing closing tag </{tag}>")));
        }
        Ok(children)
    }
    fn element(&mut self, depth: usize) -> Result<HtmlNode> {
        self.consume("<")?;
        let tag = self.name()?;
        self.validate_tag(&tag)?;
        let mut attributes = Vec::new();
        loop {
            self.whitespace();
            if self.rest().starts_with('>') || self.rest().starts_with("/>") {
                break;
            }
            let name = self.name()?;
            self.validate_attribute(&tag, &name)?;
            if attributes.iter().any(|(previous, _)| previous == &name) {
                return Err(self.error("duplicate HTML attribute"));
            }
            self.whitespace();
            self.consume("=")?;
            self.whitespace();
            let value = if self.rest().starts_with('{') {
                HtmlAttribute::Expression(self.interpolation()?)
            } else {
                let value_location = self.location();
                self.consume("\"")?;
                let end = self
                    .rest()
                    .find('"')
                    .ok_or_else(|| self.error("unterminated HTML attribute"))?;
                let literal = self.rest()[..end].to_string();
                self.cursor += end + 1;
                if ["href", "action"].contains(&name.as_str()) {
                    validate_internal_url(&literal, value_location)?;
                }
                if name == "method" && !["GET", "POST", "get", "post"].contains(&literal.as_str()) {
                    return Err(self.error("form method must be GET or POST"));
                }
                HtmlAttribute::Literal(literal)
            };
            if name == "method" && matches!(value, HtmlAttribute::Expression(_)) {
                return Err(Error::unsupported(self.location(), "dynamic form methods"));
            }
            attributes.push((name, value));
        }
        let self_closing = self.rest().starts_with("/>");
        self.cursor += if self_closing { 2 } else { 1 };
        let void = ["input", "br", "hr", "img"].contains(&tag.as_str());
        if self_closing && !void {
            return Err(self.error("only void HTML elements may use />"));
        }
        let children = if void {
            Vec::new()
        } else {
            self.children(Some(&tag), depth + 1)?
        };
        Ok(HtmlNode::Element {
            tag,
            attributes,
            children,
        })
    }
    fn validate_attribute(&self, tag: &str, name: &str) -> Result<()> {
        if !([
            "id",
            "class",
            "title",
            "href",
            "action",
            "method",
            "name",
            "value",
            "type",
            "for",
            "placeholder",
            "maxlength",
            "aria-label",
            "aria-labelledby",
            "aria-describedby",
        ]
        .contains(&name)
            || self.browser
                && ([
                    "on-click",
                    "on-submit",
                    "bind-value",
                    "bind-checked",
                    "test",
                    "items",
                    "as",
                    "key",
                    "component",
                    "stream",
                    "into",
                    "cursor",
                    "path",
                    "schema",
                    "src",
                    "alt",
                    "required",
                    "minlength",
                    "disabled",
                    "checked",
                    "role",
                    "aria-live",
                    "aria-current",
                    "aria-invalid",
                    "aria-controls",
                    "autocomplete",
                    "width",
                    "height",
                    "label",
                    "description",
                    "multiline",
                    "aria-atomic",
                ]
                .contains(&name)
                    || tag == "use"))
        {
            return Err(Error::unsupported(
                self.location(),
                format!("HTML attribute {name}"),
            ));
        }
        Ok(())
    }
    fn validate_tag(&self, tag: &str) -> Result<()> {
        if !([
            "html", "head", "title", "body", "main", "header", "footer", "nav", "section",
            "article", "div", "span", "p", "h1", "h2", "h3", "ul", "ol", "li", "a", "strong", "em",
            "pre", "code", "form", "fieldset", "legend", "label", "input", "textarea", "button",
            "br", "hr",
        ]
        .contains(&tag)
            || (self.browser
                && [
                    "when", "each", "use", "route", "img", "select", "option", "watch", "field",
                    "submit", "status",
                ]
                .contains(&tag)))
        {
            return Err(Error::unsupported(
                self.location(),
                format!(
                    "HTML tag {tag}; scripts, raw HTML, SVG, CSS, and custom elements are outside the initial template subset"
                ),
            ));
        }
        Ok(())
    }
    fn interpolation(&mut self) -> Result<crate::syntax::Expr> {
        self.consume("{")?;
        let location = self.location();
        let start = self.cursor;
        let mut quoted = false;
        let mut escaped = false;
        while let Some(c) = self.rest().chars().next() {
            if c == '}' && !quoted {
                let raw = &self.source[start..self.cursor];
                let leading = raw.len() - raw.trim_start().len();
                let expression_location = if leading == 0 {
                    location
                } else {
                    let saved = self.cursor;
                    self.cursor = start + leading;
                    let adjusted = self.location();
                    self.cursor = saved;
                    adjusted
                };
                let expression = crate::parser::expression(raw.trim(), expression_location)?;
                self.cursor += 1;
                return Ok(expression);
            }
            self.cursor += c.len_utf8();
            if escaped {
                escaped = false;
            } else if c == '\\' && quoted {
                escaped = true;
            } else if c == '"' {
                quoted = !quoted;
            }
        }
        Err(self.error("unterminated HTML interpolation"))
    }
}

pub(crate) fn validate_internal_url(value: &str, location: Location) -> Result<()> {
    if let Some(fragment) = value.strip_prefix('#')
        && !fragment.is_empty()
        && fragment.len() <= 128
        && fragment
            .chars()
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic())
        && fragment
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_'))
    {
        return Ok(());
    }
    if !value.starts_with('/')
        || value.starts_with("//")
        || value
            .chars()
            .any(|c| c.is_control() || c == '\\' || c.is_whitespace())
    {
        return Err(Error::diagnostic(
            "D020",
            location,
            "only absolute internal URL paths or simple same-page fragments are supported",
        ));
    }
    // Encoded delimiters are excluded until a full URL canonicalization contract exists.
    if value.contains('%') {
        return Err(Error::unsupported(location, "percent-encoded URL literals"));
    }
    Ok(())
}

pub(crate) fn escape(value: &str) -> String {
    let mut result = String::with_capacity(value.len());
    for c in value.chars() {
        match c {
            '&' => result.push_str("&amp;"),
            '<' => result.push_str("&lt;"),
            '>' => result.push_str("&gt;"),
            '"' => result.push_str("&quot;"),
            '\'' => result.push_str("&#39;"),
            _ => result.push(c),
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::validate_internal_url;
    use crate::error::Location;

    #[test]
    fn same_page_fragments_are_simple_and_bounded() {
        assert!(validate_internal_url("#page-content", Location::default()).is_ok());
        assert!(validate_internal_url("#main_content2", Location::default()).is_ok());
        for invalid in ["#", "#1content", "#page/content", "#page%20content", "#雪"] {
            assert!(validate_internal_url(invalid, Location::default()).is_err());
        }
        assert!(
            validate_internal_url(&format!("#a{}", "b".repeat(128)), Location::default()).is_err()
        );
    }
}
