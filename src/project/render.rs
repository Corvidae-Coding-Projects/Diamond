//! Pure server rendering of the checked browser tree; never executes mount/RPC.
mod controls;

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
        components: 0,
    };
    let mut output = String::new();
    renderer.component(
        &project.browser.component,
        &BTreeMap::new(),
        "root",
        &mut output,
    )?;
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
    components: usize,
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
        identity: &str,
        out: &mut String,
    ) -> Result<()> {
        self.components += 1;
        if self.components > 1024 {
            return Err(Error::Limit("component capacity"));
        }
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
        self.nodes(&def.template, &locals, identity, out)?;
        out.push_str("</diamond-component>");
        Ok(())
    }
    fn nodes(
        &mut self,
        nodes: &[Node],
        locals: &BTreeMap<String, Value>,
        identity: &str,
        out: &mut String,
    ) -> Result<()> {
        let escape = escaped;
        for (index, node) in nodes.iter().enumerate() {
            let id = format!("{identity}/{index}");
            if id.len() > 8192 {
                return Err(Error::Limit("component identity bytes"));
            }
            self.count += 1;
            if self.count > 8192 {
                return Err(Error::Limit("render node capacity"));
            }
            match node {
                Node::Field(_) | Node::Submit(_) | Node::Status(_) => {
                    self.managed_control(node, locals, &id, out)?;
                }
                Node::Watch { .. } => {}
                Node::Text(s) => out.push_str(&escape(s)),
                Node::Value(e) => out.push_str(&escape(&self.value(e, locals)?.display_text()?)),
                Node::When(e, children) => {
                    if self.value(e, locals)?.boolean()? {
                        self.nodes(children, locals, &id, out)?;
                    }
                }
                Node::Route(path, children) => {
                    if path == self.path {
                        self.nodes(children, locals, &id, out)?;
                    }
                }
                Node::NamedRoute {
                    route,
                    binding,
                    children,
                } => {
                    if let Some((name, value)) = self.project.browser.schemas.match_path(self.path)
                        && &name == route
                    {
                        let mut nested = locals.clone();
                        nested.insert(binding.clone(), value);
                        self.nodes(
                            children,
                            &nested,
                            &format!("{id}/{}", controls::encode(self.path)),
                            out,
                        )?;
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
                        let key = self.value(key, &nested)?;
                        let key = controls::key(&key)?;
                        if !keys.insert(key.clone()) {
                            return Err(Error::Input("duplicate key".into()));
                        }
                        out.push_str("<diamond-fragment>");
                        self.nodes(
                            children,
                            &nested,
                            &format!("{id}/{}", controls::encode(&key)),
                            out,
                        )?;
                        out.push_str("</diamond-fragment>");
                    }
                }
                Node::Use {
                    component,
                    properties,
                    ..
                } => {
                    let props = properties
                        .iter()
                        .map(|(n, e)| Ok((n.clone(), self.value(e, locals)?)))
                        .collect::<Result<_>>()?;
                    self.component(component, &props, &id, out)?;
                }
                Node::Element {
                    tag,
                    attributes,
                    children,
                } => {
                    self.element(tag, attributes, children, locals, &id, out)?;
                }
            }
            if out.len() > super::MAX_PROJECT_BYTES {
                return Err(Error::Limit("rendered page bytes"));
            }
        }
        Ok(())
    }
    fn managed_control(
        &mut self,
        node: &Node,
        locals: &BTreeMap<String, Value>,
        id: &str,
        out: &mut String,
    ) -> Result<()> {
        let escape = escaped;
        match node {
            Node::Field(field) => {
                self.count += controls::field_cost(field)?;
                if self.count > 8192 {
                    return Err(Error::Limit("render node capacity"));
                }
                let label = self.value(&field.label, locals)?.display_text()?;
                let description = field
                    .description
                    .as_ref()
                    .map(|value| self.value(value, locals)?.display_text())
                    .transpose()?;
                out.push_str(&controls::field(
                    field,
                    &label,
                    description.as_deref(),
                    locals.get(&field.binding).ok_or(Error::Denied)?,
                    id,
                )?);
            }
            Node::Submit(label) => {
                self.count += 1;
                let label = self.value(label, locals)?.display_text()?;
                controls::name(&label)?;
                write!(out, "<button type=\"submit\">{}</button>", escape(&label))
                    .map_err(|_| Error::Limit("HTML formatting"))?;
            }
            Node::Status(value) => {
                self.count += 1;
                let value = self.value(value, locals)?.display_text()?;
                controls::message(&value)?;
                write!(
                    out,
                    "<p role=\"status\" aria-live=\"polite\" aria-atomic=\"true\">{}</p>",
                    escape(&value)
                )
                .map_err(|_| Error::Limit("HTML formatting"))?;
            }
            _ => return Err(Error::Denied),
        }
        if self.count > 8192 {
            return Err(Error::Limit("render node capacity"));
        }
        Ok(())
    }
    fn element(
        &mut self,
        tag: &str,
        attributes: &[(String, Attribute)],
        children: &[Node],
        locals: &BTreeMap<String, Value>,
        identity: &str,
        out: &mut String,
    ) -> Result<()> {
        let escape = escaped;
        write!(out, "<{tag}").map_err(|_| Error::Limit("HTML formatting"))?;
        let mut textarea = None;
        for (n, a) in attributes {
            let value = match a {
                Attribute::Text(s) => s.clone(),
                Attribute::Value(e) => self.value(e, locals)?.display_text()?,
                Attribute::Route(e) => self.project.browser.schemas.url(&self.value(e, locals)?)?,
                Attribute::Binding(n) => locals.get(n).ok_or(Error::Denied)?.display_text()?,
                Attribute::Checked(n) => locals.get(n).ok_or(Error::Denied)?.boolean()?.to_string(),
                Attribute::Field(name) => name.clone(),
                Attribute::Event(_) | Attribute::Form { .. } => continue,
            };
            let name = match a {
                Attribute::Binding(_) => "value",
                Attribute::Checked(_) => "checked",
                Attribute::Field(_) => "data-diamond-field",
                _ => n,
            };
            if name == "id"
                && (["diamond-app", "diamond-error", "diamond-recovery"].contains(&value.as_str())
                    || value.starts_with("diamond-control-")
                    || value.starts_with("diamond-field-"))
            {
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
            self.nodes(children, locals, identity, &mut content)?;
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
        let mut values = BTreeSet::new();
        let mut found = false;
        self.select_nodes(children, locals, &value, &mut values, &mut found, out)?;
        if !found {
            return Err(Error::Input(
                "initial select state must match a declared option".into(),
            ));
        }
        Ok(())
    }
    fn select_nodes(
        &mut self,
        nodes: &[Node],
        locals: &BTreeMap<String, Value>,
        selected_value: &str,
        values: &mut BTreeSet<String>,
        found: &mut bool,
        out: &mut String,
    ) -> Result<()> {
        for node in nodes {
            self.count += 1;
            if self.count > 8192 {
                return Err(Error::Limit("render node capacity"));
            }
            match node {
                Node::Text(text) if text.trim().is_empty() => out.push_str(&escaped(text)),
                Node::When(condition, children) => {
                    if self.value(condition, locals)?.boolean()? {
                        self.select_nodes(children, locals, selected_value, values, found, out)?;
                    }
                }
                Node::Each {
                    items,
                    binding,
                    key,
                    children,
                } => {
                    let ValueKind::List(entries) = self.value(items, locals)?.0 else {
                        return Err(Error::Denied);
                    };
                    let mut keys = BTreeSet::new();
                    for entry in entries {
                        let mut nested = locals.clone();
                        nested.insert(binding.clone(), entry);
                        if !keys.insert(self.value(key, &nested)?.display_text()?) {
                            return Err(Error::Input("duplicate key".into()));
                        }
                        self.select_nodes(children, &nested, selected_value, values, found, out)?;
                    }
                }
                Node::Element {
                    tag,
                    attributes,
                    children,
                } if tag == "option" => {
                    let option_value = attributes
                        .iter()
                        .find_map(|(name, attribute)| (name == "value").then_some(attribute))
                        .ok_or(Error::Denied)?;
                    let option_value = match option_value {
                        Attribute::Text(value) => value.clone(),
                        Attribute::Value(expression) => {
                            self.value(expression, locals)?.display_text()?
                        }
                        _ => return Err(Error::Denied),
                    };
                    if !values.insert(option_value.clone()) {
                        return Err(Error::Input("duplicate select option value".into()));
                    }
                    let selected = option_value == selected_value;
                    *found |= selected;
                    let mut rendered_attributes = attributes.clone();
                    rendered_attributes
                        .push(("selected".into(), Attribute::Text(selected.to_string())));
                    // Checked options contain text only, with no component/control identity.
                    self.element(tag, &rendered_attributes, children, locals, "option", out)?;
                }
                _ => return Err(Error::Denied),
            }
        }
        Ok(())
    }
}

// HTML data-state parsing drops literal NUL; a character reference has a stable
// replacement-character parse which hydration checks before restoring managed text.
fn escaped(value: &str) -> String {
    crate::template::escape(value).replace('\0', "&#0;")
}
