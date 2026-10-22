//! Separate account-cookie and external bearer credential profiles.
use super::{
    Arc, BTreeMap, CheckedProject, Deserialize, Duration, Error, Json, Login, PostgresStore,
    Request, Response, Result, Scope, Server, StatusCode, Zeroizing, failure, header, input, json,
    json_response, login_response, one_header, session_cookie, to_bytes, wire,
};
use crate::{
    authority::{Principal, Restrictions},
    project::{accounts, api},
};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ApiBody {
    arguments: Vec<Json>,
}
pub(super) async fn api_request(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: tokio::sync::OwnedSemaphorePermit,
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
    let Some(name) = path
        .strip_prefix("/api/v1/")
        .filter(|name| server.project.api.contains_key(*name))
        .map(str::to_owned)
    else {
        return json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"}));
    };
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
    let job = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        api_work(
            &server,
            &name,
            token.as_deref().map(String::as_str),
            &peer,
            &body,
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
) -> Result<Json> {
    accounts::charge(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
        "api",
        peer,
        token.unwrap_or("public"),
    )?;
    let envelope: ApiBody =
        wire::strict_from_slice(body).map_err(|_| input("invalid API envelope"))?;
    let op = &server.project.api[name];
    let args = decode(&op.rpc, &envelope.arguments, &server.project)?;
    let mut store = PostgresStore::open_with(
        &server.project,
        &server.secrets.configuration,
        false,
        server.pool.as_ref(),
    )?;
    let grant = if op.auth == api::Auth::Token {
        Some(store.api_grant(
            token.ok_or(Error::Unauthenticated)?,
            name,
            &server.project.api,
        )?)
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
    )?;
    Ok(json!({"api_version":1,"value":value}))
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
pub(super) fn invoke(
    project: &CheckedProject,
    op: &super::super::rpc::Operation,
    args: &[crate::Value],
    scope: &Scope<'_>,
    store: &mut PostgresStore,
    runtime: &crate::RuntimeGrants,
) -> Result<Json> {
    let result = crate::eval::Interpreter::new(&project.server, scope, None, None)
        .with_postgres(store)
        .with_integrations(runtime)
        .function(&op.function, args);
    if matches!(op.result, crate::value::Type::AppResult(..)) {
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
