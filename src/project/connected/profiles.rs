//! Separate account-cookie and external bearer credential profiles.
use super::{
    Arc, BTreeMap, CheckedProject, Deserialize, Duration, Error, Json, Login, PostgresStore,
    Request, Response, Result, Scope, Server, StatusCode, Zeroizing, failure, header, input, json,
    json_response, login_response, one_header, session_cookie, to_bytes, wire,
};
use crate::project::actions::durable::{self, Control};
use crate::{
    authority::{Principal, Restrictions},
    project::{accounts, api},
};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiBody {
    arguments: Option<Vec<Json>>,
    #[serde(default)]
    ticket: Option<String>,
    invocation: Option<String>,
}
pub(super) async fn api_request(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
) -> Response {
    if server.project.manifest.http_api.is_none() {
        return json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"}));
    }
    if request.headers().contains_key(header::COOKIE) {
        return failure(&Error::Denied);
    }
    let path = request.uri().path();
    if path == "/api/v1/_contract" {
        if request.headers().contains_key("x-diamond-api-version")
            && one_header(&request, "x-diamond-api-version") != Some("1")
        {
            return failure(&input("invalid API version"));
        }
        if request.method() != "GET" || request.headers().contains_key(header::AUTHORIZATION) {
            return json_response(StatusCode::METHOD_NOT_ALLOWED, &json!({"error":"method"}));
        }
        return json_response(
            StatusCode::OK,
            &api::contract(&server.project.api, &server.project.server),
        );
    }
    let operation = path.strip_prefix("/api/v1/").unwrap_or_default();
    let (operation, control) = super::super::actions::split(operation);
    let Some(name) = Some(operation)
        .filter(|name| server.project.api.contains_key(*name))
        .map(str::to_owned)
    else {
        return json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"}));
    };
    if control != Control::Execute && !server.project.api[&name].rpc.action {
        return json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"}));
    }
    if request.method() != "POST" {
        return json_response(StatusCode::METHOD_NOT_ALLOWED, &json!({"error":"method"}));
    }
    if one_header(&request, "x-diamond-api-version") != Some("1") {
        return failure(&input("invalid API version"));
    }
    if one_header(&request, "content-type") != Some("application/json") {
        return json_response(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            &json!({"error":"content_type"}),
        );
    }
    let authorization = one_header(&request, "authorization");
    let token = match server.project.api[&name].auth {
        api::Auth::Public => {
            if request.headers().contains_key(header::AUTHORIZATION) {
                return failure(&Error::Denied);
            }
            None
        }
        api::Auth::Token => match authorization.and_then(|v| v.strip_prefix("Bearer ")) {
            Some(v) => Some(Zeroizing::new(v.to_owned())),
            None => return failure(&Error::Unauthenticated),
        },
    };
    let body =
        match tokio::time::timeout(Duration::from_secs(3), to_bytes(request.into_body(), 16384))
            .await
        {
            Ok(Ok(v)) => Zeroizing::new(v.to_vec()),
            _ => return failure(&input("API body limit")),
        };
    let job = crate::project::admission::blocking(move || {
        let _permit = permit;
        api_work(
            &server,
            &name,
            token.as_deref().map(String::as_str),
            &peer,
            &body,
            control,
        )
    });
    match job.await {
        Ok(Ok(value)) => json_response(StatusCode::OK, &value),
        Ok(Err(e)) => failure(&e),
        Err(_) => failure(&Error::OutcomeUnknown(String::new())),
    }
}
fn api_work(
    server: &Server,
    name: &str,
    token: Option<&str>,
    peer: &str,
    body: &[u8],
    control: Control,
) -> Result<Json> {
    accounts::charge(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
        "api",
        peer,
        token.unwrap_or("public"),
    )?;
    let eligible = super::super::retry::eligible(&server.project, &server.project.api[name].rpc);
    super::super::retry::run(
        eligible,
        &server.admission.retries,
        &server.transaction_retries,
        || api_attempt(server, name, token, body, control),
    )
}
fn api_attempt(
    server: &Server,
    name: &str,
    token: Option<&str>,
    body: &[u8],
    control: Control,
) -> Result<Json> {
    let envelope: ApiBody =
        wire::strict_from_slice(body).map_err(|_| input("invalid API envelope"))?;
    let op = &server.project.api[name];
    let args = if [Control::Prepare, Control::Execute].contains(&control) {
        Some(decode(
            &op.rpc,
            envelope
                .arguments
                .as_deref()
                .ok_or_else(|| input("operation arguments are required"))?,
            &server.project,
        )?)
    } else {
        if envelope.arguments.is_some() {
            return Err(input("status/ack accepts no action arguments"));
        }
        None
    };
    if durable::binding(&server.project, &op.rpc).is_some() {
        if envelope.ticket.is_some() {
            return Err(input("transactional actions use invocation identities"));
        }
        return durable_api(server, name, token, control, &envelope, args.as_deref());
    }
    if ![Control::Prepare, Control::Execute].contains(&control) || envelope.invocation.is_some() {
        return Err(input("operation does not support durable recovery"));
    }
    let args = args.ok_or(Error::Denied)?;
    if !op.rpc.action && envelope.ticket.is_some() {
        return Err(input("ordinary API operations do not accept action proofs"));
    }
    if op.rpc.action
        && let Some(reply) = api_action_control(
            server,
            name,
            token,
            &args,
            envelope.ticket.as_deref(),
            control == Control::Prepare,
        )?
    {
        return Ok(reply);
    }
    let query = server
        .project
        .queries
        .iter()
        .find_map(|(query_name, plan)| {
            (server.project.rpc[query_name].function == op.rpc.function).then_some(plan)
        });
    let requested = query.map(|plan| plan.check_arguments(&args)).transpose()?;
    let mut store = PostgresStore::open_with(
        &server.project,
        &server.secrets.configuration,
        false,
        server.pool.as_ref(),
    )?;
    let grant = if op.auth == api::Auth::Token {
        Some(store.api_grant(token.ok_or(Error::Unauthenticated)?, name, &server.project)?)
    } else {
        None
    };
    let anonymous = Principal::Anonymous;
    let empty = Restrictions::new();
    let principal = grant.as_ref().map_or(&anonymous, |g| &g.principal);
    let restrictions = grant.as_ref().map_or(&empty, |g| &g.restrictions);
    let grants = &server.project.server.functions[&op.rpc.function].effects;
    let scope = if grant.is_some() {
        Scope::restricted(principal, grants, restrictions, op.allow_unscoped)
    } else {
        Scope::new(principal, grants)
    };
    let value = invoke(
        &server.project,
        &op.rpc,
        &args,
        &scope,
        &mut store,
        &server.runtime,
        query.map(super::super::queries::Plan::max_queries),
    )?;
    if let (Some(plan), Some(requested)) = (query, requested) {
        plan.check_result(&value, requested)?;
    }
    Ok(json!({"api_version":1,"value":value}))
}

fn durable_api(
    server: &Server,
    name: &str,
    token: Option<&str>,
    control: Control,
    envelope: &ApiBody,
    args: Option<&[crate::Value]>,
) -> Result<Json> {
    let op = &server.project.api[name];
    let binding = durable::binding(&server.project, &op.rpc).ok_or(Error::Denied)?;
    let key = action_key(control, envelope.invocation.as_deref())?;
    let mut store = PostgresStore::open_action(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
        key,
    )?;
    let subject = token.ok_or(Error::Unauthenticated)?;
    let grant = store.api_grant(subject, name, &server.project)?;
    let auth = durable::Auth {
        principal: grant.principal,
        restrictions: Some(grant.restrictions),
        unscoped: op.allow_unscoped,
        endpoint: format!("api:{name}"),
        subject: super::super::artifact::hash(subject.as_bytes()),
        fingerprint: grant.fingerprint,
    };
    let reply = durable::Request {
        project: &server.project,
        operation: &op.rpc,
        binding,
        auth: &auth,
        control,
        invocation: envelope.invocation.as_deref(),
        arguments: args,
        runtime: &server.runtime,
    }
    .run(&mut store);
    let mut reply = action_result(reply, envelope.invocation.as_deref())?;
    reply
        .as_object_mut()
        .ok_or(Error::Denied)?
        .insert("api_version".into(), json!(1));
    Ok(reply)
}

fn api_action_control(
    server: &Server,
    name: &str,
    token: Option<&str>,
    arguments: &[crate::Value],
    ticket: Option<&str>,
    prepare: bool,
) -> Result<Option<Json>> {
    let subject = token.ok_or(Error::Unauthenticated)?;
    let mut store = PostgresStore::open_with(
        &server.project,
        &server.secrets.configuration,
        false,
        server.pool.as_ref(),
    )?;
    let grant = store.api_grant(subject, name, &server.project)?;
    let username = grant.principal.id().ok_or(Error::Unauthenticated)?;
    let operation = format!("api:{name}");
    let submission = super::super::actions::Submission {
        contract: &server.contract,
        operation: &operation,
        subject,
        username,
        arguments,
    };
    let reply = submission.control(&mut store, ticket, prepare)?;
    Ok(reply.map(|mut value| {
        if let Json::Object(fields) = &mut value {
            fields.insert("api_version".into(), json!(1));
        }
        value
    }))
}
pub(super) fn account_work(
    server: &Server,
    path: &str,
    token: Option<&str>,
    csrf: &str,
    body: &[u8],
) -> Result<(Json, Option<String>)> {
    let mut store = PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    match path {
        "/_diamond/session" => {
            if !body.is_empty() {
                return Err(input("session bootstrap has no body"));
            }
            let session = store.session(token)?;
            store.finish_request(Ok(()))?;
            let cookie = session_cookie(&server.cookie, &session.token, false);
            Ok((
                json!({"user":session.user,"csrf":session.csrf}),
                Some(cookie),
            ))
        }
        "/_diamond/login" => {
            let login: Login =
                wire::strict_from_slice(body).map_err(|_| input("invalid login shape"))?;
            let result = store.login(
                token.ok_or(Error::Unauthenticated)?,
                csrf,
                &login.username,
                &login.password,
                &server.secrets.dummy,
            );
            let session = store.finish_request(result)?;
            Ok(login_response(&server.cookie, session))
        }
        "/_diamond/logout" => {
            let empty: BTreeMap<String, Json> =
                wire::strict_from_slice(body).map_err(|_| input("invalid logout shape"))?;
            if !empty.is_empty() {
                return Err(input("logout has no arguments"));
            }
            let result = store.logout(token.ok_or(Error::Unauthenticated)?, csrf);
            store.finish_request(result)?;
            Ok((
                json!({"value":""}),
                Some(session_cookie(&server.cookie, "", true)),
            ))
        }
        _ => {
            let principal = store.principal(token, csrf)?;
            let operation_id = store.operation_id.clone();
            let result = accounts::dispatch(&mut store, &server.project, path, &principal, body);
            let response = store.finish_request(result)?;
            if let Some(message) = response.delivery {
                server
                    .mailbox
                    .as_ref()
                    .ok_or(Error::Denied)?
                    .send(&message)
                    .map_err(|_| Error::OutcomeUnknown(operation_id))?;
            }
            Ok((
                response.value,
                response
                    .clear
                    .then(|| session_cookie(&server.cookie, "", true)),
            ))
        }
    }
}
pub(super) fn decode(
    op: &super::super::rpc::Operation,
    arguments: &[Json],
    project: &CheckedProject,
) -> Result<Vec<crate::Value>> {
    if arguments.len() != op.arguments.len() {
        return Err(input("operation argument count"));
    }
    op.arguments
        .iter()
        .zip(arguments)
        .map(|(ty, v)| wire::decode(ty, v, &project.server))
        .collect()
}

#[derive(Clone, Copy)]
pub(super) struct ActionCall<'a> {
    pub ticket: Option<&'a str>,
    pub control: Control,
    pub arguments: Option<&'a [crate::Value]>,
    pub invocation: Option<&'a str>,
}
impl std::fmt::Debug for ActionCall<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ActionCall")
            .field("control", &self.control)
            .finish_non_exhaustive()
    }
}
pub(super) fn cookie_action_control(
    server: &Server,
    name: &str,
    token: Option<&str>,
    csrf: &str,
    call: ActionCall<'_>,
) -> Result<Option<Json>> {
    let ActionCall {
        ticket,
        control,
        arguments,
        invocation,
    } = call;
    let subject = token.ok_or(Error::Unauthenticated)?;
    let op = &server.project.rpc[name];
    if let Some(binding) = durable::binding(&server.project, op) {
        if ticket.is_some() {
            return Err(input("transactional actions use invocation identities"));
        }
        let key = action_key(control, invocation)?;
        let mut store = PostgresStore::open_action(
            &server.project,
            &server.secrets.configuration,
            server.pool.as_ref(),
            key,
        )?;
        let principal = store.principal(Some(subject), csrf)?;
        let user = principal.id().ok_or(Error::Unauthenticated)?;
        let fingerprint = super::super::artifact::hash(user.as_bytes());
        let auth = durable::Auth {
            principal,
            restrictions: None,
            unscoped: true,
            endpoint: format!("cookie:{name}"),
            subject: fingerprint.clone(),
            fingerprint,
        };
        return action_result(
            durable::Request {
                project: &server.project,
                operation: op,
                binding,
                auth: &auth,
                control,
                invocation,
                arguments,
                runtime: &server.runtime,
            }
            .run(&mut store),
            invocation,
        )
        .map(Some);
    }
    if ![Control::Prepare, Control::Execute].contains(&control) || invocation.is_some() {
        return Err(input("operation does not support durable recovery"));
    }
    let mut store = PostgresStore::open_with(
        &server.project,
        &server.secrets.configuration,
        false,
        server.pool.as_ref(),
    )?;
    let principal = store.principal(Some(subject), csrf)?;
    let username = principal.id().ok_or(Error::Unauthenticated)?;
    let operation = format!("cookie:{name}");
    let submission = super::super::actions::Submission {
        contract: &server.contract,
        operation: &operation,
        subject,
        username,
        arguments: arguments.ok_or(Error::Denied)?,
    };
    submission.control(&mut store, ticket, control == Control::Prepare)
}

fn action_key(control: Control, invocation: Option<&str>) -> Result<&str> {
    if control == Control::Prepare {
        if invocation.is_some() {
            return Err(input("preparation accepts no invocation identity"));
        }
        return Ok("capacity");
    }
    let id = invocation.ok_or_else(|| input("action invocation identity is required"))?;
    super::super::actions::durable::valid_identity(id)?;
    Ok(id)
}
fn action_result(result: Result<Json>, invocation: Option<&str>) -> Result<Json> {
    result.map_err(|error| match error {
        Error::Conflict | Error::Aborted | Error::Postgres | Error::OutcomeUnknown(_) => {
            Error::OutcomeUnknown(invocation.unwrap_or_default().into())
        }
        other => other,
    })
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecoveryBody {
    contract: String,
}
pub(super) fn cookie_recovery(
    server: &Server,
    token: Option<&str>,
    csrf: &str,
    body: &[u8],
) -> Result<(Json, Option<String>)> {
    let envelope: RecoveryBody =
        wire::strict_from_slice(body).map_err(|_| input("invalid recovery envelope"))?;
    if envelope.contract != server.contract {
        return Err(super::input_error_contract());
    }
    let mut store = PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let principal = store.principal(token, csrf)?;
    Ok((
        durable::discover(&mut store, &server.project, &principal)?,
        None,
    ))
}
pub(super) fn cookie_rpc(
    server: &Server,
    store: &mut PostgresStore,
    principal: &Principal,
    name: &str,
    args: &[crate::Value],
) -> Result<(Json, Option<String>)> {
    if principal.id().is_none() && !server.project.manifest.public_rpc.contains(name) {
        return Err(Error::Denied);
    }
    let op = &server.project.rpc[name];
    let query = server.project.queries.get(name);
    let requested = query.map(|plan| plan.check_arguments(args)).transpose()?;
    let grants = &server.project.server.functions[&op.function].effects;
    let scope = Scope::new(principal, grants);
    let value = invoke(
        &server.project,
        op,
        args,
        &scope,
        store,
        &server.runtime,
        query.map(super::super::queries::Plan::max_queries),
    )?;
    if let (Some(plan), Some(requested)) = (query, requested) {
        plan.check_result(&value, requested)?;
    }
    Ok((json!({"value":value}), None))
}

pub(super) fn invoke(
    project: &CheckedProject,
    op: &super::super::rpc::Operation,
    args: &[crate::Value],
    scope: &Scope<'_>,
    store: &mut PostgresStore,
    runtime: &crate::RuntimeGrants,
    query_limit: Option<usize>,
) -> Result<Json> {
    let mut interpreter = crate::eval::Interpreter::new(&project.server, scope, None, None)
        .with_postgres(store)
        .with_integrations(runtime);
    if let Some(limit) = query_limit {
        interpreter = interpreter.with_query_limit(limit);
    }
    let result = interpreter.function(&op.function, args);
    if op.action {
        super::super::actions::finish(store, result)
    } else if matches!(op.result, crate::value::Type::AppResult(..)) {
        let result = result.and_then(|v| match v.0 {
            crate::value::ValueKind::AppErr(e) => Err(Error::Application(*e)),
            _ => Ok(v),
        });
        let value = match store.finish_request(result) {
            Err(Error::Application(e)) => {
                crate::Value(crate::value::ValueKind::AppErr(Box::new(e)))
            }
            other => other?,
        };
        wire::encode(&value)
    } else {
        wire::encode(&store.finish_request(result.and_then(crate::Value::into_result))?)
    }
}
