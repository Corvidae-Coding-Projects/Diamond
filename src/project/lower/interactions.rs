//! Typed component messages, handler arguments, and schema-form controls.

use super::{Event, EventStep, EventValue, Expression, Handler, Lower, Node, error};
use crate::error::{Location, Result};
use crate::hir::CheckedProgram;
use crate::syntax::{self, Expr, ExprKind, HtmlAttribute};
use crate::value::Type;
use std::collections::BTreeMap;

type Signature = Vec<(String, Type)>;
type Outputs = BTreeMap<String, Signature>;

pub(super) fn root_identity(
    root: &str,
    components: &BTreeMap<String, super::Component>,
) -> Result<()> {
    fn identity(value: &EventValue) -> bool {
        match value {
            EventValue::Remote { operation, .. } => [
                "login",
                "logout",
                "verify",
                "reset",
                "change_password",
                "logout_all",
                "disable",
            ]
            .iter()
            .any(|name| operation == &format!("session/{name}")),
            EventValue::Async { remotes, .. } => remotes.values().any(identity),
            EventValue::Pure(_) => false,
        }
    }
    fn direct(value: &EventValue) -> bool {
        match value {
            EventValue::Remote { .. } => true,
            EventValue::Async {
                expression,
                remotes,
            } => matches!(
                &expression.ast.kind,
                ExprKind::Name(name) if remotes.len() == 1 && remotes.contains_key(name)
            ),
            EventValue::Pure(_) => false,
        }
    }
    for (name, component) in components {
        for event in component.events.values() {
            let mut remote_seen = false;
            for step in &event.body {
                if let EventStep::Assign(_, value) = step {
                    if identity(value) && (name != root || remote_seen || !direct(value)) {
                        return Err(error(
                            event.location,
                            "identity changes require a direct root-component assignment before other remote work",
                        ));
                    }
                    remote_seen |= !matches!(value, EventValue::Pure(_));
                }
            }
        }
    }
    Ok(())
}

pub(super) fn output_signatures(
    program: &CheckedProgram,
    components: &[syntax::Component],
) -> Result<BTreeMap<String, Outputs>> {
    components
        .iter()
        .map(|component| {
            let outputs = component
                .outputs
                .iter()
                .map(|output| {
                    Ok((
                        output.name.clone(),
                        parameters(program, &output.parameters, output.location)?,
                    ))
                })
                .collect::<Result<_>>()?;
            Ok((component.name.clone(), outputs))
        })
        .collect()
}

fn parameters(
    program: &CheckedProgram,
    parameters: &[(String, syntax::TypeName)],
    location: Location,
) -> Result<Signature> {
    parameters
        .iter()
        .map(|(name, ty)| {
            let ty = crate::checker::resolve_type(ty, program, location)?;
            if !crate::security::type_security(&ty, program).component_message_safe() {
                return Err(error(
                    location,
                    "component messages require recursively safe managed data",
                ));
            }
            Ok((name.clone(), ty))
        })
        .collect()
}

impl Lower<'_> {
    pub(super) fn events(
        &self,
        component: &syntax::Component,
        state: &[(String, Expression)],
        locals: &BTreeMap<String, Type>,
        rpc: &BTreeMap<String, crate::project::rpc::Operation>,
    ) -> Result<BTreeMap<String, Event>> {
        component
            .events
            .iter()
            .map(|event| {
                let parameters = parameters(self.program, &event.parameters, event.location)?;
                if ["mount", "unmount"].contains(&event.name.as_str()) && !parameters.is_empty() {
                    return Err(error(event.location, "lifecycle events take no parameters"));
                }
                let mut scope = locals.clone();
                scope.extend(parameters.iter().cloned());
                let body = event
                    .body
                    .iter()
                    .map(|step| self.event_step(step, &scope, rpc, component, &event.name, state))
                    .collect::<Result<_>>()?;
                Ok((
                    event.name.clone(),
                    Event {
                        parameters,
                        body,
                        location: event.location,
                    },
                ))
            })
            .collect()
    }

    fn event_step(
        &self,
        step: &syntax::EventStep,
        locals: &BTreeMap<String, Type>,
        rpc: &BTreeMap<String, crate::project::rpc::Operation>,
        component: &syntax::Component,
        name: &str,
        state: &[(String, Expression)],
    ) -> Result<EventStep> {
        match step {
            syntax::EventStep::Assign(target, value) => {
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
                let event = self.event(value, locals, rpc)?;
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
                Ok(EventStep::Assign(target.clone(), event))
            }
            syntax::EventStep::Emit(output, arguments, location) => {
                if ["mount", "unmount"].contains(&name) {
                    return Err(error(
                        *location,
                        "lifecycle events cannot emit component outputs",
                    ));
                }
                let signature = self.output_signatures[&component.name]
                    .get(output)
                    .ok_or_else(|| error(*location, "emit requires a declared component output"))?;
                let arguments = self.arguments(arguments, signature, locals, *location)?;
                Ok(EventStep::Emit(output.clone(), arguments))
            }
        }
    }

    fn arguments(
        &self,
        arguments: &[Expr],
        signature: &Signature,
        locals: &BTreeMap<String, Type>,
        location: Location,
    ) -> Result<Vec<Expression>> {
        if arguments.len() != signature.len() {
            return Err(error(location, "handler/output argument count mismatch"));
        }
        arguments
            .iter()
            .zip(signature)
            .map(|(value, (_, expected))| {
                let expression = self.expression(value, locals)?;
                if !expected.accepts(&expression.ty) {
                    return Err(error(
                        value.location,
                        "handler/output argument type mismatch",
                    ));
                }
                Ok(expression)
            })
            .collect()
    }

    pub(super) fn handler(
        &self,
        expression: &Expr,
        locals: &BTreeMap<String, Type>,
        events: &BTreeMap<String, Event>,
    ) -> Result<Handler> {
        let (name, arguments) = match &expression.kind {
            ExprKind::Name(name) => (name, Vec::new()),
            ExprKind::Call {
                callee,
                arguments,
                types,
            } if types.is_empty() && arguments.iter().all(|(name, _)| name.is_none()) => {
                let ExprKind::Name(name) = &callee.kind else {
                    return Err(error(
                        expression.location,
                        "handler must name a declared event",
                    ));
                };
                (name, arguments.iter().map(|(_, e)| e.clone()).collect())
            }
            _ => {
                return Err(error(
                    expression.location,
                    "handler requires a declared event and positional arguments",
                ));
            }
        };
        if ["mount", "unmount"].contains(&name.as_str()) {
            return Err(error(
                expression.location,
                "lifecycle events cannot be bound as handlers",
            ));
        }
        let signature = &events
            .get(name)
            .ok_or_else(|| error(expression.location, "unknown component event"))?
            .parameters;
        Ok(Handler {
            name: name.clone(),
            arguments: self.arguments(&arguments, signature, locals, expression.location)?,
        })
    }

    pub(super) fn component_use(
        &self,
        component: &str,
        attributes: &BTreeMap<&str, &HtmlAttribute>,
        locals: &BTreeMap<String, Type>,
        events: &BTreeMap<String, Event>,
    ) -> Result<Node> {
        let signature = self
            .signatures
            .get(component)
            .ok_or_else(|| error(Location::default(), "unknown or invisible component"))?;
        let child_outputs = &self.output_signatures[component];
        if attributes.len() != signature.len() + child_outputs.len() + 1 {
            return Err(error(
                Location::default(),
                "component properties and outputs must match its declared signature",
            ));
        }
        let properties = signature
            .iter()
            .map(|(name, ty)| {
                let Some(HtmlAttribute::Expression(expression)) = attributes.get(name.as_str())
                else {
                    return Err(error(
                        Location::default(),
                        "component properties require expressions",
                    ));
                };
                let expression = self.expression(expression, locals)?;
                if !ty.accepts(&expression.ty) {
                    return Err(error(
                        expression.ast.location,
                        "component property type mismatch",
                    ));
                }
                Ok((name.clone(), expression))
            })
            .collect::<Result<_>>()?;
        let outputs = child_outputs
            .iter()
            .map(|(name, parameters)| {
                let attribute = format!("on-{name}");
                let Some(HtmlAttribute::Expression(Expr {
                    kind: ExprKind::Name(handler),
                    location,
                })) = attributes.get(attribute.as_str())
                else {
                    return Err(error(
                        Location::default(),
                        "component output requires a declared parent handler",
                    ));
                };
                let expected = &events
                    .get(handler)
                    .ok_or_else(|| error(*location, "unknown parent output handler"))?
                    .parameters;
                if expected.len() != parameters.len()
                    || expected
                        .iter()
                        .zip(parameters)
                        .any(|((_, a), (_, b))| !a.accepts(b))
                    || ["mount", "unmount"].contains(&handler.as_str())
                {
                    return Err(error(
                        *location,
                        "component output handler signature mismatch",
                    ));
                }
                Ok((name.clone(), handler.clone()))
            })
            .collect::<Result<_>>()?;
        Ok(Node::Use {
            component: component.into(),
            properties,
            outputs,
        })
    }
}
