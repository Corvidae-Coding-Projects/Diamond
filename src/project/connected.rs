//! Connected loopback serving with exact-origin RPC and shared `PostgreSQL` identity.

use super::{
    CheckedProject, artifact, identity::Session, input, server::response as asset_response,
    storage::PostgresStore, wire,
};
use crate::authority::Scope;
use crate::error::{Error, Result};
use axum::{
    Router,
    body::to_bytes,
    extract::{Request, State},
    http::{StatusCode, Uri, header},
    response::Response,
};
use serde::Deserialize;
use serde_json::{Value as Json, json};
use std::{collections::BTreeMap, fmt, net::SocketAddr, sync::Arc, time::Duration};
use tokio::sync::Semaphore;
use zeroize::{Zeroize, Zeroizing};

struct Secrets {
    configuration: Zeroizing<String>,
    dummy: Zeroizing<String>,
}
impl fmt::Debug for Secrets {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secrets([PRIVATE])")
    }
}

#[derive(Debug)]
struct Server {
    project: CheckedProject,
    files: BTreeMap<String, String>,
    authority: String,
    origin: String,
    contract: String,
    cookie: String,
    secrets: Arc<Secrets>,
    permits: Arc<Semaphore>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RpcBody {
    contract: String,
    arguments: Vec<Json>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Login {
    username: String,
    password: String,
}
impl Drop for Login {
    fn drop(&mut self) {
        self.password.zeroize();
    }
}

pub(super) fn serve(
    project: &CheckedProject,
    address: SocketAddr,
    origin: &str,
    configuration: &str,
) -> Result<()> {
    if !address.ip().is_loopback() {
        return Err(input("connected development serving is loopback-only"));
    }
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(input(
            "call synchronous connected serving outside an async runtime",
        ));
    }
    let uri: Uri = origin
        .parse()
        .map_err(|_| input("invalid connected public origin"))?;
    let authority = uri
        .authority()
        .ok_or_else(|| input("public origin requires an authority"))?;
    if uri.scheme_str() != Some("http")
        || !["localhost", "127.0.0.1", "[::1]"].contains(&authority.host())
        || authority.port_u16().is_none()
        || uri.path() != "/"
        || uri.query().is_some()
        || origin != format!("http://{authority}")
    {
        return Err(input(
            "W2 requires an exact http://localhost:PORT loopback development origin",
        ));
    }
    let mut store = PostgresStore::open(project, configuration, false)?;
    store.finish_request(Ok(()))?;
    let secrets = Arc::new(Secrets {
        configuration: Zeroizing::new(configuration.into()),
        dummy: Zeroizing::new(crate::accounts::hash_password(
            &crate::authority::random_hex()?,
        )?),
    });
    let state = Arc::new(Server {
        project: project.clone(),
        files: artifact::files(project)?
            .into_iter()
            .filter(|(p, _)| p == "index.html" || p.starts_with("assets/"))
            .collect(),
        authority: authority.to_string(),
        origin: origin.into(),
        contract: artifact::contract_id(project)?,
        cookie: format!(
            "__Host-diamond_{}",
            &artifact::hash(project.manifest.name.as_bytes())[..16]
        ),
        secrets,
        permits: Arc::new(Semaphore::new(16)),
    });
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(4)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(address).await?;
        let app = Router::new().fallback(handler).with_state(state);
        eprintln!("Diamond connected development server: {origin}");
        axum::serve(listener, app)
            .with_graceful_shutdown(async {
                let _signal = tokio::signal::ctrl_c().await;
            })
            .await
            .map_err(Error::Io)
    })
}

async fn handler(State(server): State<Arc<Server>>, request: Request) -> Response {
    let Ok(permit) = Arc::clone(&server.permits).try_acquire_owned() else {
        return failure(&Error::Limit("request capacity"));
    };
    if !valid_request(&server, &request) {
        return json_response(StatusCode::BAD_REQUEST, &json!({"error":"invalid_request"}));
    }
    let path = request.uri().path().to_owned();
    if request.method() == "GET" || request.method() == "HEAD" {
        if path != "/_diamond/session" {
            let key = if path == "/" {
                "index.html"
            } else {
                path.trim_start_matches('/')
            };
            return server.files.get(key).map_or_else(
                || json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"})),
                |text| {
                    response(
                        StatusCode::OK,
                        if key == "index.html" {
                            "text/html; charset=utf-8"
                        } else {
                            "text/javascript; charset=utf-8"
                        },
                        text,
                        request.method() == "HEAD",
                    )
                },
            );
        }
        if request.method() != "GET" {
            return json_response(StatusCode::METHOD_NOT_ALLOWED, &json!({"error":"method"}));
        }
        if one_header(&request, "x-diamond-session") != Some("1") {
            return json_response(StatusCode::FORBIDDEN, &json!({"error":"bootstrap_header"}));
        }
    } else if request.method() != "POST" {
        return json_response(StatusCode::METHOD_NOT_ALLOWED, &json!({"error":"method"}));
    }
    if !["/_diamond/session", "/_diamond/login", "/_diamond/logout"].contains(&path.as_str())
        && !path
            .strip_prefix("/_diamond/rpc/")
            .is_some_and(|name| server.project.rpc.contains_key(name))
    {
        return json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"}));
    }
    let post = request.method() == "POST";
    if post
        && (path == "/_diamond/session"
            || one_header(&request, header::ORIGIN.as_str()) != Some(server.origin.as_str()))
    {
        return json_response(StatusCode::FORBIDDEN, &json!({"error":"origin"}));
    }
    if post && one_header(&request, header::CONTENT_TYPE.as_str()) != Some("application/json") {
        return json_response(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            &json!({"error":"content_type"}),
        );
    }
    let csrf = one_header(&request, "x-diamond-csrf")
        .unwrap_or_default()
        .to_owned();
    let token = match cookie(&request, &server.cookie) {
        Ok(v) => v,
        Err(e) => return failure(&e),
    };
    let body = match tokio::time::timeout(
        Duration::from_secs(3),
        to_bytes(request.into_body(), 16_384),
    )
    .await
    {
        Ok(Ok(bytes)) => Zeroizing::new(bytes.to_vec()),
        _ => {
            return json_response(
                StatusCode::PAYLOAD_TOO_LARGE,
                &json!({"error":"body_limit"}),
            );
        }
    };
    dispatch(server, path, token, csrf, body, permit).await
}

async fn dispatch(
    server: Arc<Server>,
    path: String,
    token: Option<Zeroizing<String>>,
    csrf: String,
    body: Zeroizing<Vec<u8>>,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> Response {
    let work_server = Arc::clone(&server);
    // The owned permit stays with the blocking work if delivery is canceled.
    // Its private connection driver is aborted/joined before the worker exits.
    let job = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        work(
            &work_server,
            &path,
            token.as_deref().map(String::as_str),
            &csrf,
            &body,
        )
    });
    match job.await {
        Ok(Ok((value, set_cookie))) => {
            let mut result = json_response(StatusCode::OK, &value);
            if let Some(value) = set_cookie
                && let Ok(value) = value.parse()
            {
                result.headers_mut().insert(header::SET_COOKIE, value);
            }
            result
        }
        Ok(Err(error)) => failure(&error),
        Err(_) => json_response(
            StatusCode::SERVICE_UNAVAILABLE,
            &json!({"error":"outcome_unknown"}),
        ),
    }
}

fn work(
    server: &Server,
    path: &str,
    token: Option<&str>,
    csrf: &str,
    body: &[u8],
) -> Result<(Json, Option<String>)> {
    // Decode public input/contract before opening an application transaction.
    let decoded = if let Some(name) = path.strip_prefix("/_diamond/rpc/") {
        let op = &server.project.rpc[name];
        let envelope: RpcBody =
            serde_json::from_slice(body).map_err(|_| input("invalid RPC envelope"))?;
        if envelope.contract != server.contract {
            return Err(input_error_contract());
        }
        if envelope.arguments.len() != op.arguments.len() {
            return Err(input("RPC argument count"));
        }
        Some(
            op.arguments
                .iter()
                .zip(&envelope.arguments)
                .map(|(ty, value)| wire::decode(ty, value, &server.project.server))
                .collect::<Result<Vec<_>>>()?,
        )
    } else {
        None
    };
    let mut store = PostgresStore::open(&server.project, &server.secrets.configuration, false)?;
    if path == "/_diamond/session" {
        if !body.is_empty() {
            return Err(input("session bootstrap has no body"));
        }
        let session = store.session(token)?;
        store.finish_request(Ok(()))?;
        let cookie = session_cookie(&server.cookie, &session.token, false);
        return Ok((
            json!({"user":session.user,"csrf":session.csrf}),
            Some(cookie),
        ));
    }
    if path == "/_diamond/login" {
        let login: Login =
            serde_json::from_slice(body).map_err(|_| input("invalid login shape"))?;
        let result = store.login(
            token.ok_or(Error::Denied)?,
            csrf,
            &login.username,
            &login.password,
            &server.secrets.dummy,
        );
        // Shared failed-attempt counters must commit on authentication denial.
        // Database/shape failures still roll back; they are not failed passwords.
        match result {
            Err(error @ (Error::Denied | Error::Limit("shared login rate"))) => {
                store.finish_request(Ok(()))?;
                return Err(error);
            }
            Err(error) => return Err(error),
            Ok(session) => {
                store.finish_request(Ok(()))?;
                return Ok(login_response(&server.cookie, session));
            }
        }
    }
    if path == "/_diamond/logout" {
        let empty: BTreeMap<String, Json> =
            serde_json::from_slice(body).map_err(|_| input("invalid logout shape"))?;
        if !empty.is_empty() {
            return Err(input("logout has no arguments"));
        }
        store.logout(token.ok_or(Error::Denied)?, csrf)?;
        store.finish_request(Ok(()))?;
        return Ok((
            json!({"value":""}),
            Some(session_cookie(&server.cookie, "", true)),
        ));
    }
    let principal = store.principal(token, csrf)?;
    if principal.id().is_none() {
        return Err(Error::Denied);
    }
    let name = path.strip_prefix("/_diamond/rpc/").ok_or(Error::Denied)?;
    let op = &server.project.rpc[name];
    let grants = &server.project.server.functions[&op.function].effects;
    let scope = Scope::new(&principal, grants);
    let result = crate::eval::Interpreter::new(&server.project.server, &scope, None, None)
        .with_postgres(&mut store)
        .function(&op.function, &decoded.ok_or(Error::Denied)?)
        .and_then(crate::Value::into_result);
    let value = store.finish_request(result)?;
    Ok((json!({"value":wire::encode(&value)?}), None))
}

fn one_header<'a>(request: &'a Request, name: &str) -> Option<&'a str> {
    let mut values = request.headers().get_all(name).iter();
    let value = values.next()?.to_str().ok()?;
    if values.next().is_some() {
        None
    } else {
        Some(value)
    }
}

fn valid_request(server: &Server, request: &Request) -> bool {
    one_header(request, "host") == Some(server.authority.as_str())
        && !request
            .headers()
            .keys()
            .any(|k| k == "forwarded" || k.as_str().starts_with("x-forwarded-"))
        && request.uri().query().is_none()
        && request.uri().authority().is_none()
        && !request.uri().path().contains(['%', '\\'])
        && !request.uri().path().starts_with("//")
        && !request.headers().contains_key(header::TRANSFER_ENCODING)
        && !request.headers().contains_key(header::UPGRADE)
        && (!request.headers().contains_key(header::ORIGIN)
            || one_header(request, "origin") == Some(server.origin.as_str()))
        && one_header(request, "sec-fetch-site") != Some("cross-site")
        && (!request.headers().contains_key(header::CONTENT_LENGTH)
            || one_header(request, "content-length")
                .and_then(|v| v.parse::<usize>().ok())
                .is_some_and(|n| n <= 16_384))
}

fn cookie(request: &Request, name: &str) -> Result<Option<Zeroizing<String>>> {
    let mut result = None;
    for header in request.headers().get_all(header::COOKIE) {
        let header = header.to_str().map_err(|_| Error::Denied)?;
        for part in header.split(';') {
            if let Some((key, value)) = part.trim().split_once('=')
                && key == name
            {
                if result.is_some()
                    || value.len() != 64
                    || !value
                        .bytes()
                        .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                {
                    return Err(Error::Denied);
                }
                result = Some(Zeroizing::new(value.to_owned()));
            }
        }
    }
    Ok(result)
}

fn session_cookie(name: &str, token: &str, clear: bool) -> String {
    format!(
        "{name}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={}",
        if clear { 0 } else { 1800 }
    )
}
fn login_response(name: &str, session: Session) -> (Json, Option<String>) {
    (
        json!({"value":session.user.unwrap_or_default(),"csrf":session.csrf}),
        Some(session_cookie(name, &session.token, false)),
    )
}
fn input_error_contract() -> Error {
    input("RPC contract mismatch")
}
fn json_response(status: StatusCode, value: &Json) -> Response {
    response(
        status,
        "application/json; charset=utf-8",
        &value.to_string(),
        false,
    )
}
fn response(status: StatusCode, kind: &'static str, text: &str, head: bool) -> Response {
    let mut response = asset_response(status, kind, text, head);
    response.headers_mut().insert("content-security-policy",header::HeaderValue::from_static("default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"));
    response
}

fn failure(error: &Error) -> Response {
    let (status, code) = match error {
        Error::Denied => (StatusCode::FORBIDDEN, "denied"),
        Error::Aborted => (StatusCode::CONFLICT, "aborted"),
        Error::OutcomeUnknown(_) => (StatusCode::SERVICE_UNAVAILABLE, "outcome_unknown"),
        Error::Input(message) if message == "RPC contract mismatch" => {
            (StatusCode::CONFLICT, "contract_mismatch")
        }
        Error::Input(message) if message.starts_with("PostgreSQL") => {
            (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
        }
        Error::Input(_) => (StatusCode::BAD_REQUEST, "invalid_input"),
        Error::Limit("shared login rate") => (StatusCode::TOO_MANY_REQUESTS, "rate_limited"),
        _ => (StatusCode::SERVICE_UNAVAILABLE, "unavailable"),
    };
    json_response(status, &json!({"error":code}))
}
