//! Checked browser IR, with the same expression tree used by reference evaluation.

mod asynchronous;
mod reach;
use crate::checker::expression_type;
use crate::error::{Error, Location, Result};
use crate::hir::CheckedProgram;
use crate::syntax::{self, Expr, ExprKind, HtmlAttribute, HtmlNode, Statement};
use crate::value::Type;
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize)]
pub(super) struct Expression {
    pub ty: Type,
    pub ast: Expr,
}
#[derive(Clone, Debug, Serialize)]
pub(super) enum EventValue {
    Async {
        expression: Expression,
        remotes: BTreeMap<String, EventValue>,
    },
    Pure(Expression),
    Remote {
        operation: String,
        arguments: Vec<Expression>,
        types: Vec<Type>,
        result: Type,
    },
}
#[derive(Clone, Debug, Serialize)]
pub(super) struct Function {
    pub parameters: Vec<(String, Type)>,
    pub result: Type,
    pub body: Vec<Statement>,
    pub location: Location,
}
#[derive(Clone, Debug, Serialize)]
pub(super) enum Node {
    Text(String),
    Value(Expression),
    Element {
        tag: String,
        attributes: Vec<(String, Attribute)>,
        children: Vec<Self>,
    },
    When(Expression, Vec<Self>),
    Each {
        items: Expression,
        binding: String,
        key: Expression,
        children: Vec<Self>,
    },
    Use {
        component: String,
        properties: Vec<(String, Expression)>,
    },
    Route(String, Vec<Self>),
    Watch {
        stream: Expression,
        into: String,
        cursor: String,
        payload: Type,
    },
}
#[derive(Clone, Debug, Serialize)]
pub(super) enum Attribute {
    Text(String),
    Value(Expression),
    Event(String),
    Binding(String),
    Checked(String),
}
#[derive(Clone, Debug, Serialize)]
pub(super) struct Component {
    pub properties: Vec<(String, Type)>,
    pub state: Vec<(String, Expression)>,
    pub derived: Vec<(String, Expression)>,
    pub events: BTreeMap<String, Vec<(String, EventValue)>>,
    pub template: Vec<Node>,
}
#[derive(Clone, Debug, Serialize)]
pub(super) struct BrowserProgram {
    pub component: String,
    pub components: BTreeMap<String, Component>,
    pub functions: BTreeMap<String, Function>,
    pub records: BTreeMap<String, BTreeMap<String, Type>>,
    pub enums: BTreeMap<String, BTreeMap<String, Vec<Type>>>,
    pub defaults: BTreeMap<String, BTreeMap<String, serde_json::Value>>,
    pub wire_names: BTreeMap<String, String>,
    pub aliases: BTreeMap<String, Type>,
    pub routes: BTreeSet<String>,
}
struct Lower<'a> {
    accounts: bool,
    oauth: bool,
    files: BTreeSet<String>,
    channels: BTreeMap<String, Type>,
    program: &'a CheckedProgram,
    signatures: BTreeMap<String, Vec<(String, Type)>>,
    routes: BTreeSet<String>,
}
pub(super) fn lower(
    program: &CheckedProgram,
    root: &str,
    components: &[syntax::Component],
    rpc: &BTreeMap<String, super::rpc::Operation>,
    identity: Option<&super::accounts::Config>,
    files: Option<&super::files::Config>,
    channels: BTreeMap<String, Type>,
) -> Result<BrowserProgram> {
    let mut lower = Lower {
        accounts: identity.is_some(),
        oauth: identity.is_some_and(|config| config.oauth.is_some()),
        channels,
        files: files
            .map(|c| c.bindings.keys().cloned().collect())
            .unwrap_or_default(),
        program,
        signatures: BTreeMap::new(),
        routes: BTreeSet::from(["/".into()]),
    };
    lower.signatures = property_signatures(program, components)?;
    if !lower.signatures.get(root).is_some_and(Vec::is_empty) {
        return Err(error(
            Location::default(),
            "root component must exist and take no properties",
        ));
    }
    let mut checked = BTreeMap::new();
    for c in components {
        checked.insert(c.name.clone(), lower.component(c, rpc)?);
    }
    let mut used_components = BTreeSet::new();
    visit(root, &checked, &mut BTreeSet::new(), &mut used_components)?;
    checked.retain(|n, _| used_components.contains(n));
    lower.routes = BTreeSet::from(["/".into()]);
    for component in checked.values() {
        collect_routes(&component.template, &mut lower.routes);
    }
    let used = reach::used(program, &checked);
    let functions = reachable_functions(program, &used.functions)?;
    Ok(BrowserProgram {
        component: root.into(),
        components: checked,
        functions,
        records: program
            .records
            .iter()
            .filter(|(n, _)| used.records.contains(*n))
            .map(|(n, r)| {
                (
                    n.clone(),
                    r.fields
                        .iter()
                        .map(|(n, f)| (n.clone(), f.ty.clone()))
                        .collect(),
                )
            })
            .collect(),
        enums: program
            .enums
            .iter()
            .filter(|(n, _)| used.enums.contains(*n))
            .map(|(n, e)| {
                (
                    n.clone(),
                    e.variants
                        .iter()
                        .map(|(n, f)| (n.clone(), f.iter().map(|(_, ty)| ty.clone()).collect()))
                        .collect(),
                )
            })
            .collect(),
        defaults: program
            .records
            .iter()
            .filter(|(n, _)| used.records.contains(*n))
            .map(|(n, r)| {
                Ok((
                    n.clone(),
                    r.fields
                        .iter()
                        .filter_map(|(n, f)| f.default.as_ref().map(|v| (n, v)))
                        .map(|(n, v)| Ok((n.clone(), super::wire::encode(v)?)))
                        .collect::<Result<_>>()?,
                ))
            })
            .collect::<Result<_>>()?,
        wire_names: program
            .enums
            .iter()
            .filter(|(n, _)| used.enums.contains(*n))
            .flat_map(|(_, e)| e.variants.keys())
            .map(|n| (n.clone(), super::resolve::public_name(n).into()))
            .collect(),
        aliases: program
            .aliases
            .iter()
            .filter(|(n, _)| used.aliases.contains(*n))
            .map(|(n, t)| (n.clone(), t.clone()))
            .collect(),
        routes: lower.routes,
    })
}
impl Lower<'_> {
    fn expression(&self, value: &Expr, locals: &BTreeMap<String, Type>) -> Result<Expression> {
        let ty = expression_type(self.program, locals.clone(), value)?;
        if !crate::security::type_security(&ty, self.program).browser_managed {
            return Err(error(
                value.location,
                "browser expression is not a managed browser value",
            ));
        }
        Ok(Expression {
            ty,
            ast: value.clone(),
        })
    }
    fn component(
        &mut self,
        c: &syntax::Component,
        rpc: &BTreeMap<String, super::rpc::Operation>,
    ) -> Result<Component> {
        let properties = self.signatures[&c.name].clone();
        let mut locals: BTreeMap<_, _> = properties.iter().cloned().collect();
        let mut state = Vec::new();
        for (n, t, e) in &c.states {
            let ty = crate::checker::resolve_type(t, self.program, e.location)?;
            let value = self.expression(e, &locals)?;
            if !ty.accepts(&value.ty) {
                return Err(error(e.location, "state initializer type mismatch"));
            }
            state.push((
                n.clone(),
                Expression {
                    ty: ty.clone(),
                    ast: value.ast,
                },
            ));
            locals.insert(n.clone(), ty);
        }
        locals.extend([
            ("pending".into(), Type::Bool),
            ("error".into(), Type::Text),
            ("route".into(), Type::Text),
        ]);
        let mut derived = Vec::new();
        for (n, t, e) in &c.derived {
            let ty = crate::checker::resolve_type(t, self.program, e.location)?;
            let value = self.expression(e, &locals)?;
            if !ty.accepts(&value.ty) {
                return Err(error(e.location, "derived value type mismatch"));
            }
            locals.insert(n.clone(), ty.clone());
            derived.push((n.clone(), Expression { ty, ast: value.ast }));
        }
        let mut events = BTreeMap::new();
        for (name, assignments) in &c.events {
            let mut output = Vec::new();
            for (target, value) in assignments {
                let expected = state
                    .iter()
                    .find(|(n, _)| n == target)
                    .map(|(_, v)| &v.ty)
                    .ok_or_else(|| {
                        error(
                            value.location,
                            "event assignment targets mutable state only",
                        )
                    })?;
                let event = self.event(value, &locals, rpc)?;
                let actual = match &event {
                    EventValue::Pure(e) | EventValue::Async { expression: e, .. } => &e.ty,
                    EventValue::Remote { result, .. } => result,
                };
                if !expected.accepts(actual) {
                    return Err(error(value.location, "event assignment changes state type"));
                }
                if name == "unmount" && !matches!(event, EventValue::Pure(_)) {
                    return Err(error(
                        value.location,
                        "unmount cleanup cannot dispatch server mutations",
                    ));
                }
                output.push((target.clone(), event));
            }
            events.insert(name.clone(), output);
        }
        let mutable = state
            .iter()
            .map(|(n, e)| (n.clone(), e.ty.clone()))
            .collect();
        let template = self.nodes(&c.template, &locals, &events, &mutable)?;
        Ok(Component {
            properties,
            state,
            derived,
            events,
            template,
        })
    }
    fn event(
        &self,
        e: &Expr,
        locals: &BTreeMap<String, Type>,
        rpc: &BTreeMap<String, super::rpc::Operation>,
    ) -> Result<EventValue> {
        asynchronous::lower(self, e, locals, rpc)
    }
    fn remote(
        &self,
        e: &Expr,
        locals: &BTreeMap<String, Type>,
        rpc: &BTreeMap<String, super::rpc::Operation>,
    ) -> Result<EventValue> {
        if let ExprKind::Call {
            callee,
            arguments,
            types,
        } = &e.kind
            && let ExprKind::Field(base, name) = &callee.kind
            && let ExprKind::Name(namespace) = &base.kind
            && ["rpc", "session", "files"].contains(&namespace.as_str())
        {
            let (parameters, result, operation) = if namespace == "rpc" {
                let op = rpc
                    .get(name)
                    .ok_or_else(|| error(e.location, "RPC is not declared in the manifest"))?;
                (
                    op.arguments.clone(),
                    op.result.clone(),
                    format!("rpc/{name}"),
                )
            } else if namespace == "files" {
                let (parameters, result) = self.file_signature(name, arguments, e.location)?;
                (parameters, result, format!("files/{name}"))
            } else {
                let params = match name.as_str() {
                    "login" => vec![Type::Text, Type::Text],
                    "oauth_login" if self.oauth => vec![Type::Text, Type::Text],
                    "logout" | "user" => vec![],
                    _ if self.accounts => super::accounts::signature(name)
                        .ok_or_else(|| error(e.location, "unknown account operation"))?,
                    _ => {
                        return Err(error(
                            e.location,
                            "unknown session operation or missing accounts-v1 profile",
                        ));
                    }
                };
                (params, Type::Text, format!("session/{name}"))
            };
            if !types.is_empty()
                || arguments.len() != parameters.len()
                || arguments.iter().any(|(n, _)| n.is_some())
            {
                return Err(error(
                    e.location,
                    "remote arguments must match the declared positional signature",
                ));
            }
            let mut values = Vec::new();
            for ((_, e), ty) in arguments.iter().zip(&parameters) {
                let value = self.expression(e, locals)?;
                if !(ty.accepts(&value.ty)
                    || matches!(ty, Type::BoundedText(..)) && value.ty == Type::Text)
                {
                    return Err(error(e.location, "remote argument type mismatch"));
                }
                values.push(value);
            }
            Ok(EventValue::Remote {
                operation,
                arguments: values,
                types: parameters,
                result,
            })
        } else {
            Ok(EventValue::Pure(self.expression(e, locals)?))
        }
    }
    fn file_signature(
        &self,
        name: &str,
        arguments: &[(Option<String>, Expr)],
        location: Location,
    ) -> Result<(Vec<Type>, Type)> {
        let Some((
            None,
            Expr {
                kind: ExprKind::Text(binding),
                ..
            },
        )) = arguments.first()
        else {
            return Err(error(
                location,
                "file binding must be a static declared name",
            ));
        };
        if !self.files.contains(binding)
            || !["upload", "status", "delete", "download"].contains(&name)
        {
            return Err(error(location, "unknown file binding or operation"));
        }
        if name == "upload"
            && !matches!(
                arguments.get(2),
                Some((
                    None,
                    Expr {
                        kind: ExprKind::Text(_),
                        ..
                    }
                ))
            )
        {
            return Err(error(location, "file input id must be static"));
        }
        let blob = Type::Blob(binding.clone());
        Ok(match name {
            "upload" => (vec![Type::Text, Type::Text, Type::Text], blob),
            "status" => (vec![Type::Text, blob], Type::Text),
            "download" => (vec![Type::Text, blob.clone()], blob),
            "delete" => (vec![Type::Text, blob.clone()], Type::Option(Box::new(blob))),
            _ => return Err(error(location, "unknown file operation")),
        })
    }
    fn nodes(
        &mut self,
        nodes: &[HtmlNode],
        locals: &BTreeMap<String, Type>,
        events: &BTreeMap<String, Vec<(String, EventValue)>>,
        mutable: &BTreeMap<String, Type>,
    ) -> Result<Vec<Node>> {
        nodes.iter().map(|node|match node {
            HtmlNode::Text(s)=>Ok(Node::Text(s.clone())),
            HtmlNode::Expression(e)=>{let v=self.expression(e,locals)?;if !matches!(v.ty,Type::Int|Type::Bool|Type::Text|Type::BoundedText(..)|Type::Id(_)) {return Err(error(e.location,"render compound values through explicit fields, match, each or formatting utilities"));}Ok(Node::Value(v))},
            HtmlNode::Element{tag,attributes,children}=>{
                let attrs:BTreeMap<_,_>=attributes.iter().map(|(n,v)|(n.as_str(),v)).collect();
                let literal=|n:&str|match attrs.get(n) {Some(HtmlAttribute::Literal(s))=>Ok(s.clone()),_=>Err(error(Location::default(),"directive requires a static name/path"))};
                let expr=|n:&str|match attrs.get(n) {Some(HtmlAttribute::Expression(e))=>Ok((*e).clone()),_=>Err(error(Location::default(),"directive requires an expression"))};
                match tag.as_str() {
                    "watch"=>{
                        if attrs.len()!=3 || !children.iter().all(|n|matches!(n,HtmlNode::Text(t) if t.trim().is_empty())) {return Err(error(Location::default(),"watch requires stream, into, cursor and no content"));}
                        let into=literal("into")?;let cursor=literal("cursor")?;let stream=self.expression(&expr("stream")?,locals)?;
                        let Type::Stream(channel)=&stream.ty else {return Err(error(stream.ast.location,"watch requires a typed Stream"));};
                        let payload=self.channels.get(channel).ok_or_else(||error(Location::default(),"unknown event channel"))?.clone();
                        if !mutable.contains_key(&into) || locals.get(&into)!=Some(&payload) || !mutable.contains_key(&cursor) || locals.get(&cursor)!=Some(&Type::Text) || into==cursor {return Err(error(Location::default(),"watch targets must be distinct mutable payload and Text cursor states"));}
                        return Ok(Node::Watch{stream,into,cursor,payload});
                    },
                    "when"=>{if attrs.len()!=1 {return Err(error(Location::default(),"when requires only test"));}let v=self.expression(&expr("test")?,locals)?;if v.ty!=Type::Bool {return Err(error(v.ast.location,"when test must be Bool"));}return Ok(Node::When(v,self.nodes(children,locals,events,mutable)?));},
                    "each"=>{if attrs.len()!=3 {return Err(error(Location::default(),"each requires items, as and key"));}let items=self.expression(&expr("items")?,locals)?;let Type::List(inner)=&items.ty else {return Err(error(items.ast.location,"each requires a List"));};let binding=literal("as")?;if !super::resolve::identifier(&binding) || locals.contains_key(&binding) {return Err(error(items.ast.location,"each binding must be a fresh local name"));}let mut nested=locals.clone();nested.insert(binding.clone(),(**inner).clone());let key=self.expression(&expr("key")?,&nested)?;if !matches!(key.ty,Type::Int|Type::Text|Type::BoundedText(..)|Type::Id(_)) {return Err(error(key.ast.location,"key requires Int, Text or Id"));}return Ok(Node::Each{items,binding,key,children:self.nodes(children,&nested,events,mutable)?});},
                    "route"=>{if attrs.len()!=1 {return Err(error(Location::default(),"route requires only path"));}let path=literal("path")?;crate::template::validate_internal_url(&path,Location::default())?;if path.contains(['?','#']) || path.starts_with("/_diamond") || path.starts_with("/assets/") {return Err(error(Location::default(),"route path is reserved or not canonical"));}self.routes.insert(path.clone());return Ok(Node::Route(path,self.nodes(children,locals,events,mutable)?));},
                    "use"=>{if !children.iter().all(|n|matches!(n,HtmlNode::Text(s) if s.trim().is_empty())) {return Err(error(Location::default(),"use takes properties only"));}let component=literal("component")?;let signature=self.signatures.get(&component).ok_or_else(||error(Location::default(),"unknown or invisible component"))?;if attrs.len()!=signature.len()+1 {return Err(error(Location::default(),"component properties must match its declared signature"));}let properties=signature.iter().map(|(n,ty)|{let e=self.expression(&expr(n)?,locals)?;if !ty.accepts(&e.ty) {return Err(error(e.ast.location,"component property type mismatch"));}Ok((n.clone(),e))}).collect::<Result<_>>()?;return Ok(Node::Use{component,properties});},
                    "html"|"head"|"body"|"title"=>return Err(error(Location::default(),"components render inside the document body")),_=>{}
                }
                let mut checked=Vec::new();
                for (name,value) in attributes {
                    let attr=match (name.as_str(),value) {
                        ("on-click"|"on-submit",HtmlAttribute::Expression(Expr{kind:ExprKind::Name(event),..})) if events.contains_key(event) && ((name=="on-click" && tag=="button") || (name=="on-submit" && tag=="form"))=>Attribute::Event(event.clone()),
                        ("on-click"|"on-submit",_)=>return Err(error(Location::default(),"event requires a declared handler on a button/form")),
                        ("bind-value",HtmlAttribute::Expression(Expr{kind:ExprKind::Name(n),..})) if ["input","textarea","select"].contains(&tag.as_str()) && mutable.get(n)==Some(&Type::Text)=>Attribute::Binding(n.clone()),
                        ("bind-checked",HtmlAttribute::Expression(Expr{kind:ExprKind::Name(n),..})) if tag=="input" && mutable.get(n)==Some(&Type::Bool)=>Attribute::Checked(n.clone()),
                        ("bind-value"|"bind-checked",_)=>return Err(error(Location::default(),"binding requires compatible mutable state")),
                        ("id",HtmlAttribute::Literal(s)) if ["diamond-app","diamond-error"].contains(&s.as_str())=>return Err(error(Location::default(),"component id is reserved by the runtime")),
                        (_,HtmlAttribute::Literal(s))=>Attribute::Text(s.clone()),
                        ("href"|"action"|"src",_)=>return Err(error(Location::default(),"URL attributes require static validated references")),
                        (_,HtmlAttribute::Expression(e))=>{
                            let value=self.expression(e,locals)?;
                            if tag=="option" && name=="value" && !matches!(value.ty,Type::Int|Type::Text|Type::BoundedText(..)|Type::Id(_)) {return Err(error(e.location,"option values require Int, Text or Id"));}
                            Attribute::Value(value)
                        },
                    };checked.push((name.clone(),attr));
                }
                let file_input = tag=="input" && checked.iter().any(|(n,v)| n=="type" && matches!(v,Attribute::Text(s) if s=="file"));
                if file_input && (self.files.is_empty() || !checked.iter().any(|(n,v)|n=="id" && matches!(v,Attribute::Text(s) if super::resolve::identifier(s))) || checked.iter().any(|(n,_)| ["bind-value","bind-checked","value","multiple"].contains(&n.as_str()))) { return Err(error(Location::default(),"file inputs require a static id and a file profile, without bindings or multiple selection")); }
                if tag=="input" && !file_input && (!checked.iter().any(|(n,_)|["bind-value","bind-checked"].contains(&n.as_str())) || checked.iter().any(|(n,v)| n=="value" || n=="type" && !matches!(v,Attribute::Text(s) if ["text","password","checkbox"].contains(&s.as_str())))) {return Err(error(Location::default(),"inputs require a binding and a static text/password/checkbox type"));}
                if tag=="input" && !file_input {
                    let checkbox=checked.iter().any(|(n,v)|n=="type" && matches!(v,Attribute::Text(s) if s=="checkbox"));
                    let binding=checked.iter().any(|(_,v)|matches!(v,Attribute::Checked(_)));
                    if checkbox!=binding {return Err(error(Location::default(),"checkbox inputs require bind-checked; text/password inputs require bind-value"));}
                }
                if tag=="select" {
                    if !checked.iter().any(|(_,v)|matches!(v,Attribute::Binding(_))) {return Err(error(Location::default(),"select requires bind-value"));}
                    let mut values=BTreeSet::new();
                    validate_select_children(children,&mut values)?;
                }
                if tag=="form" && !checked.iter().any(|(n,_)|n=="on-submit") {return Err(error(Location::default(),"component forms require on-submit"));}
                Ok(Node::Element{tag:tag.clone(),attributes:checked,children:self.nodes(children,locals,events,mutable)?})
            }
        }).collect()
    }
}
fn error(location: Location, message: &str) -> Error {
    Error::diagnostic("D330", location, message)
}

fn validate_select_children(
    nodes: &[HtmlNode],
    static_values: &mut BTreeSet<String>,
) -> Result<()> {
    for node in nodes {
        match node {
            HtmlNode::Text(text) if text.trim().is_empty() => {}
            HtmlNode::Element {
                tag,
                attributes,
                children,
            } if tag == "option" => {
                let values = attributes
                    .iter()
                    .filter(|(name, _)| name == "value")
                    .collect::<Vec<_>>();
                if values.len() != 1 {
                    return Err(error(
                        Location::default(),
                        "select options require exactly one value attribute",
                    ));
                }
                if let HtmlAttribute::Literal(value) = &values[0].1
                    && !static_values.insert(value.clone())
                {
                    return Err(error(Location::default(), "duplicate select option value"));
                }
                if children
                    .iter()
                    .any(|child| !matches!(child, HtmlNode::Text(_) | HtmlNode::Expression(_)))
                {
                    return Err(error(
                        Location::default(),
                        "option labels support text and scalar expressions only",
                    ));
                }
            }
            HtmlNode::Element { tag, children, .. } if ["each", "when"].contains(&tag.as_str()) => {
                validate_select_children(children, static_values)?;
            }
            _ => {
                return Err(error(
                    Location::default(),
                    "select supports option children, including options inside each or when",
                ));
            }
        }
    }
    Ok(())
}

fn collect_routes(nodes: &[Node], routes: &mut BTreeSet<String>) {
    for node in nodes {
        match node {
            Node::Route(path, children) => {
                routes.insert(path.clone());
                collect_routes(children, routes);
            }
            Node::When(_, children)
            | Node::Each { children, .. }
            | Node::Element { children, .. } => collect_routes(children, routes),
            _ => {}
        }
    }
}

fn visit(
    name: &str,
    all: &BTreeMap<String, Component>,
    active: &mut BTreeSet<String>,
    done: &mut BTreeSet<String>,
) -> Result<()> {
    if done.contains(name) {
        return Ok(());
    }
    if !active.insert(name.into()) {
        return Err(error(Location::default(), "component cycle"));
    }
    let mut names = BTreeSet::new();
    dependencies(&all[name].template, &mut names);
    for child in names {
        visit(&child, all, active, done)?;
    }
    active.remove(name);
    done.insert(name.into());
    Ok(())
}

fn dependencies(nodes: &[Node], names: &mut BTreeSet<String>) {
    for node in nodes {
        match node {
            Node::Use { component, .. } => {
                names.insert(component.clone());
            }
            Node::Element { children, .. }
            | Node::When(_, children)
            | Node::Each { children, .. }
            | Node::Route(_, children) => dependencies(children, names),
            _ => {}
        }
    }
}

fn property_signatures(
    program: &CheckedProgram,
    components: &[syntax::Component],
) -> Result<BTreeMap<String, Vec<(String, Type)>>> {
    let mut signatures = BTreeMap::new();
    for c in components {
        let props = c
            .properties
            .iter()
            .map(|(n, t)| {
                Ok((
                    n.clone(),
                    crate::checker::resolve_type(t, program, c.location)?,
                ))
            })
            .collect::<Result<Vec<_>>>()?;
        signatures.insert(c.name.clone(), props);
    }
    Ok(signatures)
}

fn reachable_functions(
    program: &CheckedProgram,
    names: &BTreeSet<String>,
) -> Result<BTreeMap<String, Function>> {
    let functions = program
        .functions
        .iter()
        .filter(|(n, _)| names.contains(*n))
        .map(|(name, f)| {
            if !f.effects.is_empty() {
                return Err(error(
                    f.location,
                    "browser functions cannot carry host authority",
                ));
            }
            Ok((
                name.clone(),
                Function {
                    parameters: f.parameters.clone(),
                    result: f.result.clone(),
                    body: f.body.clone(),
                    location: f.location,
                },
            ))
        })
        .collect::<Result<_>>()?;
    Ok(functions)
}
