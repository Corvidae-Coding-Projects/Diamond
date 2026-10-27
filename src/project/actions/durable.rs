//! One dispatcher for cookie/API prepare, execution, reconciliation and acknowledgement.
use super::{
    Binding, CheckedProject, PostgresStore, footprint, input,
    receipts::{self, Receipt},
    rpc, wire,
};
use crate::{
    authority::{Effect, Effects, Principal, Restrictions, Scope},
    error::{Error, Result},
    value::{Value, ValueKind},
};
use serde_json::{Value as Json, json};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(in crate::project) enum Control {
    Prepare,
    Execute,
    Status,
    Ack,
}
pub(in crate::project) fn valid_identity(id: &str) -> Result<()> {
    receipts::valid_id(id)
}
#[derive(Debug)]
pub(in crate::project) struct Auth {
    pub principal: Principal,
    pub restrictions: Option<Restrictions>,
    pub unscoped: bool,
    pub endpoint: String,
    pub subject: String,
    pub fingerprint: String,
}
impl Auth {
    fn scope<'a>(&'a self, grants: &'a Effects) -> Scope<'a> {
        self.restrictions.as_ref().map_or_else(
            || Scope::new(&self.principal, grants),
            |r| Scope::restricted(&self.principal, grants, r, self.unscoped),
        )
    }
}
pub(in crate::project) fn binding<'a>(
    project: &'a CheckedProject,
    op: &rpc::Operation,
) -> Option<&'a Binding> {
    project.manifest.actions.iter().find_map(|(name, binding)| {
        (project.rpc[name].function == op.function && binding.durable()).then_some(binding)
    })
}
pub(in crate::project) fn grants(
    project: &CheckedProject,
    op: &rpc::Operation,
    binding: &Binding,
) -> Effects {
    let mut grants = project.server.functions[&op.function].effects.clone();
    for effect in grants.clone() {
        if let Effect::Write(resource) = effect {
            grants.insert(Effect::Read(resource));
        }
    }
    if let Some(resource) = &binding.resource {
        grants.insert(Effect::Read(resource.clone()));
    }
    grants
}
fn parent(
    project: &CheckedProject,
    op: &rpc::Operation,
    binding: &Binding,
    args: &[Value],
) -> Result<String> {
    let index = project.server.functions[&op.function]
        .parameters
        .iter()
        .position(|(name, _)| Some(name) == binding.parent.as_ref())
        .ok_or(Error::Denied)?;
    let ValueKind::Id(_, value) = &args.get(index).ok_or(Error::Denied)?.0 else {
        return Err(Error::Denied);
    };
    Ok(value.clone())
}
fn fingerprint(args: &[Value]) -> Result<String> {
    Ok(super::super::artifact::hash(
        &serde_json::to_vec(&args.iter().map(wire::encode).collect::<Result<Vec<_>>>()?)
            .map_err(|_| Error::Denied)?,
    ))
}
pub(in crate::project) struct Request<'a> {
    pub project: &'a CheckedProject,
    pub operation: &'a rpc::Operation,
    pub binding: &'a Binding,
    pub auth: &'a Auth,
    pub control: Control,
    pub invocation: Option<&'a str>,
    pub arguments: Option<&'a [Value]>,
    pub runtime: &'a crate::RuntimeGrants,
}
impl std::fmt::Debug for Request<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ActionRequest")
            .field("control", &self.control)
            .finish_non_exhaustive()
    }
}
impl Request<'_> {
    pub(in crate::project) fn run(&self, store: &mut PostgresStore) -> Result<Json> {
        let user = self.auth.principal.id().ok_or(Error::Unauthenticated)?;
        let grants = grants(self.project, self.operation, self.binding);
        let scope = self.auth.scope(&grants);
        let version = super::super::artifact::contract_id(self.project)?;
        if self.control == Control::Prepare {
            let args = self.arguments.ok_or(Error::Denied)?;
            let parent = parent(self.project, self.operation, self.binding, args)?;
            let resource = self.binding.resource.as_ref().ok_or(Error::Denied)?;
            store.attachment(&scope, resource, &parent, false)?;
            let binding = receipts::Binding {
                app: store.app.clone(),
                username: user.into(),
                endpoint: self.auth.endpoint.clone(),
                subject: self.auth.subject.clone(),
                authority: self.auth.fingerprint.clone(),
                version,
                resource: resource.clone(),
                parent,
                fingerprint: fingerprint(args)?,
                expires: 0,
            };
            let reply = receipts::prepare(store, binding)?;
            return store.finish_request(Ok(reply));
        }
        let id = self
            .invocation
            .ok_or_else(|| input("action requires a runtime-issued invocation identity"))?;
        receipts::valid_id(id)?;
        if self.control == Control::Ack {
            // Forgetting one's reference reveals no protected outcome. It remains
            // possible after resource access or credential restrictions change.
            store.connection.query("UPDATE diamond_actions.invocations SET ack=true WHERE app=$1 AND id=$2 AND username=$3 AND endpoint=$4 AND subject=$5",&[&store.app,&id,&user,&self.auth.endpoint,&self.auth.subject])?;
            return store.finish_request(Ok(json!({"acknowledged":true})));
        }
        self.resume(store, &scope, &version, user, id)
    }
    fn resume(
        &self,
        store: &mut PostgresStore,
        scope: &Scope<'_>,
        version: &str,
        user: &str,
        id: &str,
    ) -> Result<Json> {
        let Some(stored) = receipts::load(store, id)? else {
            return store.finish_request(receipts::outcome("ActionExpired"));
        };
        if stored.binding.username != user {
            return Err(Error::Denied);
        }
        if stored.expired {
            return store.finish_request(receipts::outcome("ActionExpired"));
        }
        let mut receipt = match receipts::open(store, id, &stored) {
            Ok(receipt) => receipt,
            Err(Error::Denied) => return store.finish_request(receipts::outcome("ActionCorrupt")),
            Err(error) => return Err(error),
        };
        if receipt.binding.endpoint != self.auth.endpoint
            || receipt.binding.subject != self.auth.subject
            || receipt.binding.authority != self.auth.fingerprint
            || receipt.binding.version != version
            || Some(&receipt.binding.resource) != self.binding.resource.as_ref()
        {
            return store.finish_request(receipts::outcome("ActionConflict"));
        }
        if let Some(args) = self.arguments
            && (receipt.binding.parent != parent(self.project, self.operation, self.binding, args)?
                || receipt.binding.fingerprint != fingerprint(args)?)
        {
            return store.finish_request(receipts::outcome("ActionConflict"));
        }
        if let Some(outcome) = &receipt.outcome {
            let decoded =
                wire::decode(&self.operation.client_result, outcome, &self.project.server);
            let Ok(Value(ValueKind::Variant(_, tag, _))) = decoded else {
                return store.finish_request(receipts::outcome("ActionCorrupt"));
            };
            if ![
                "ActionSucceeded",
                "ActionRejected",
                "ActionDenied",
                "ActionInvalid",
                "ActionAborted",
            ]
            .contains(&tag.as_str())
            {
                return store.finish_request(receipts::outcome("ActionCorrupt"));
            }
            if tag != "ActionSucceeded"
                || !footprint::deleted_parent(
                    store,
                    &receipt.accesses,
                    &receipt.binding.resource,
                    &receipt.binding.parent,
                )?
            {
                store.attachment(
                    scope,
                    &receipt.binding.resource,
                    &receipt.binding.parent,
                    false,
                )?;
            }
            // The sealed deletion fact replaces the absent parent's read guard;
            // footprint replay still checks any recreated row and current policy.
            footprint::reauthorize(store, scope, &receipt.accesses, tag == "ActionSucceeded")?;
            return store.finish_request(Ok(json!({"value":outcome})));
        }
        store.attachment(
            scope,
            &receipt.binding.resource,
            &receipt.binding.parent,
            false,
        )?;
        if self.control == Control::Status {
            // Holding the same invocation lock proves no mutation transaction
            // remains active. Closing Prepared also fences a delayed execute.
            receipt.outcome = Some(wire::encode(&crate::action::value(
                "ActionAborted",
                vec![],
            ))?);
            receipts::save(store, id, &receipt)?;
        } else {
            self.execute(store, scope, id, &mut receipt)?;
        }
        match store.finish_request(Ok(json!({"value":receipt.outcome}))) {
            Ok(reply) => Ok(reply),
            Err(_) => Err(Error::OutcomeUnknown(id.into())),
        }
    }
    fn execute(
        &self,
        store: &mut PostgresStore,
        scope: &Scope<'_>,
        id: &str,
        receipt: &mut Receipt,
    ) -> Result<()> {
        store.action_savepoint()?;
        store.action_access = Some(vec![]);
        let result = crate::eval::Interpreter::new(&self.project.server, scope, None, None)
            .with_postgres(store)
            .with_integrations(self.runtime)
            .function(
                &self.operation.function,
                self.arguments.ok_or(Error::Denied)?,
            );
        let result = if let Some(error) = store.action_failure() {
            Err(error)
        } else {
            result
        };
        let (tag, fields) = match result {
            Ok(Value(ValueKind::Ok(value))) => ("ActionSucceeded", vec![*value]),
            Ok(Value(ValueKind::AppErr(error))) => ("ActionRejected", vec![*error]),
            Err(Error::Application(error)) => ("ActionRejected", vec![error]),
            Err(Error::Denied | Error::Unauthenticated) => ("ActionDenied", vec![]),
            Err(Error::Input(_)) => ("ActionInvalid", vec![]),
            _ => ("ActionAborted", vec![]),
        };
        receipt.accesses = store.action_access.take().unwrap_or_default();
        if tag != "ActionSucceeded" {
            store.rollback_action_body()?;
        }
        // An abort carries no protected payload. An incomplete/over-budget
        // footprint must not make the confirmed rollback receipt unrecoverable.
        if tag == "ActionAborted" {
            receipt.accesses.clear();
        }
        receipt.outcome = Some(wire::encode(&crate::action::value(tag, fields))?);
        match receipts::save(store, id, receipt) {
            Err(Error::Limit(_)) => {
                store.rollback_action_body()?;
                receipt.accesses.clear();
                receipt.outcome = Some(wire::encode(&crate::action::value(
                    "ActionAborted",
                    vec![],
                ))?);
                receipts::save(store, id, receipt)
            }
            result => result,
        }
    }
}

pub(in crate::project) fn discover(
    store: &mut PostgresStore,
    project: &CheckedProject,
    principal: &Principal,
) -> Result<Json> {
    let user = principal.id().ok_or(Error::Unauthenticated)?;
    let rows=store.connection.query("SELECT id,endpoint FROM diamond_actions.invocations WHERE app=$1 AND username=$2 AND endpoint LIKE 'cookie:%' AND NOT ack ORDER BY expires,id LIMIT 16",&[&store.app,&user])?;
    let mut references = vec![];
    for row in rows {
        let endpoint: String = row.try_get(1).map_err(|_| Error::Denied)?;
        let Some(name) = endpoint.strip_prefix("cookie:") else {
            continue;
        };
        if project
            .manifest
            .actions
            .get(name)
            .is_some_and(Binding::durable)
        {
            references.push(json!({"action":name,"invocation":row.try_get::<_,String>(0).map_err(|_|Error::Denied)?}));
        }
    }
    store.finish_request(Ok(json!({"references":references})))
}
