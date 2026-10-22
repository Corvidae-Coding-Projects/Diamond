//! Pure server rendering of the checked browser tree; never executes mount/RPC.
use super::{
    CheckedProject,
    lower::{Attribute, Node},
};
use crate::authority::{Effects, Principal, Scope};
use crate::error::{Error, Result};
use crate::value::{Value, ValueKind};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;

pub(super) fn render(project: &CheckedProject, path: &str) -> Result<String> {
    let principal = Principal::Anonymous;
    let effects = Effects::new();
    let scope = Scope::new(&principal, &effects);
    let mut renderer = Render {
        project,
        path,
        interpreter: crate::eval::Interpreter::new(&project.browser_checked, &scope, None, None),
        count: 0,
    };
    let mut output = String::new();
    renderer.component(&project.browser.component, &BTreeMap::new(), &mut output)?;
    if output.len() > super::MAX_PROJECT_BYTES {
        return Err(Error::Limit("rendered page"));
    }
    Ok(output)
}
struct Render<'a, 's> {
    project: &'a CheckedProject,
    path: &'a str,
    interpreter: crate::eval::Interpreter<'a, 's>,
    count: usize,
}
impl Render<'_, '_> {
    fn value(
        &mut self,
        e: &super::lower::Expression,
        locals: &BTreeMap<String, Value>,
    ) -> Result<Value> {
        let v = self.interpreter.expr(&e.ast, locals)?;
        if !v.fits(&e.ty) {
            return Err(Error::Input("initial component value bounds".into()));
        }
        Ok(v)
    }
    fn component(
        &mut self,
        name: &str,
        props: &BTreeMap<String, Value>,
        out: &mut String,
    ) -> Result<()> {
        let def = &self.project.browser.components[name];
        let mut locals = props.clone();
        for (n, e) in &def.state {
            let v = self.value(e, &locals)?;
            locals.insert(n.clone(), v);
        }
        locals.extend([
            ("pending".into(), Value(ValueKind::Bool(false))),
            ("error".into(), Value(ValueKind::Text(String::new()))),
            ("route".into(), Value(ValueKind::Text(self.path.into()))),
        ]);
        for (n, e) in &def.derived {
            let v = self.value(e, &locals)?;
            locals.insert(n.clone(), v);
        }
        write!(
            out,
            "<diamond-component data-diamond-component=\"{}\" aria-busy=\"false\">",
            crate::template::escape(name)
        )
        .map_err(|_| Error::Limit("HTML formatting"))?;
        self.nodes(&def.template, &locals, out)?;
        out.push_str("</diamond-component>");
        Ok(())
    }
    fn nodes(
        &mut self,
        nodes: &[Node],
        locals: &BTreeMap<String, Value>,
        out: &mut String,
    ) -> Result<()> {
        let escape = escaped;
        for node in nodes {
            self.count += 1;
            if self.count > 8192 {
                return Err(Error::Limit("render node capacity"));
            }
            match node {
                Node::Text(s) => out.push_str(&escape(s)),
                Node::Value(e) => out.push_str(&escape(&self.value(e, locals)?.display_text()?)),
                Node::When(e, children) => {
                    if self.value(e, locals)?.boolean()? {
                        self.nodes(children, locals, out)?;
                    }
                }
                Node::Route(path, children) => {
                    if path == self.path {
                        self.nodes(children, locals, out)?;
                    }
                }
                Node::Each {
                    items,
                    binding,
                    key,
                    children,
                } => {
                    let ValueKind::List(values) = self.value(items, locals)?.0 else {
                        return Err(Error::Denied);
                    };
                    let mut keys = BTreeSet::new();
                    for value in values {
                        let mut nested = locals.clone();
                        nested.insert(binding.clone(), value);
                        if !keys.insert(self.value(key, &nested)?.display_text()?) {
                            return Err(Error::Input("duplicate key".into()));
                        }
                        out.push_str("<diamond-fragment>");
                        self.nodes(children, &nested, out)?;
                        out.push_str("</diamond-fragment>");
                    }
                }
                Node::Use {
                    component,
                    properties,
                } => {
                    let props = properties
                        .iter()
                        .map(|(n, e)| Ok((n.clone(), self.value(e, locals)?)))
                        .collect::<Result<_>>()?;
                    self.component(component, &props, out)?;
                }
                Node::Element {
                    tag,
                    attributes,
                    children,
                } => {
                    self.element(tag, attributes, children, locals, out)?;
                }
            }
            if out.len() > super::MAX_PROJECT_BYTES {
                return Err(Error::Limit("rendered page bytes"));
            }
        }
        Ok(())
    }
    fn element(
        &mut self,
        tag: &str,
        attributes: &[(String, Attribute)],
        children: &[Node],
        locals: &BTreeMap<String, Value>,
        out: &mut String,
    ) -> Result<()> {
        let escape = escaped;
        write!(out, "<{tag}").map_err(|_| Error::Limit("HTML formatting"))?;
        let mut textarea = None;
        for (n, a) in attributes {
            let value = match a {
                Attribute::Text(s) => s.clone(),
                Attribute::Value(e) => self.value(e, locals)?.display_text()?,
                Attribute::Binding(n) => locals.get(n).ok_or(Error::Denied)?.display_text()?,
                Attribute::Checked(n) => locals.get(n).ok_or(Error::Denied)?.boolean()?.to_string(),
                Attribute::Event(_) => continue,
            };
            let name = match a {
                Attribute::Binding(_) => "value",
                Attribute::Checked(_) => "checked",
                _ => n,
            };
            if name == "id" && ["diamond-app", "diamond-error"].contains(&value.as_str()) {
                return Err(Error::Input(
                    "component id is reserved by the runtime".into(),
                ));
            }
            if ["checked", "required", "disabled", "selected"].contains(&name) {
                if value == "true" {
                    write!(out, " {name}=\"\"").map_err(|_| Error::Limit("HTML formatting"))?;
                }
                continue;
            }
            if name == "value" && tag == "textarea" {
                textarea = Some(value);
                continue;
            }
            write!(out, " {name}=\"{}\"", escape(&value))
                .map_err(|_| Error::Limit("HTML formatting"))?;
        }
        out.push('>');
        if let Some(v) = textarea {
            if v.starts_with('\n') {
                out.push('\n');
            }
            out.push_str(&escape(&v));
        } else if tag == "select" {
            self.select(attributes, children, locals, out)?;
        } else {
            let mut content = String::new();
            self.nodes(children, locals, &mut content)?;
            if tag == "pre" && content.starts_with('\n') {
                out.push('\n');
            }
            out.push_str(&content);
        }
        if !["input", "br", "hr", "img"].contains(&tag) {
            write!(out, "</{tag}>").map_err(|_| Error::Limit("HTML formatting"))?;
        }
        Ok(())
    }
    fn select(
        &mut self,
        attributes: &[(String, Attribute)],
        children: &[Node],
        locals: &BTreeMap<String, Value>,
        out: &mut String,
    ) -> Result<()> {
        let state = attributes
            .iter()
            .find_map(|(_, v)| {
                if let Attribute::Binding(n) = v {
                    Some(n)
                } else {
                    None
                }
            })
            .ok_or(Error::Denied)?;
        let value = locals.get(state).ok_or(Error::Denied)?.display_text()?;
        let mut nodes = children.to_vec();
        let mut found = false;
        for node in &mut nodes {
            if let Node::Element {
                tag, attributes, ..
            } = node
                && tag == "option"
            {
                let selected = attributes
                    .iter()
                    .any(|(n, v)| n == "value" && matches!(v,Attribute::Text(s) if s==&value));
                attributes.push(("selected".into(), Attribute::Text(selected.to_string())));
                found |= selected;
            }
        }
        if !found {
            return Err(Error::Input(
                "initial select state must match a declared option".into(),
            ));
        }
        self.nodes(&nodes, locals, out)
    }
}

// HTML data-state parsing drops literal NUL; a character reference has a stable
// replacement-character parse which hydration checks before restoring managed text.
fn escaped(value: &str) -> String {
    crate::template::escape(value).replace('\0', "&#0;")
}
