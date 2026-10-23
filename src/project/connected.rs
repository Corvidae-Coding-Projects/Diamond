//! Connected loopback serving with exact-origin RPC and shared `PostgreSQL` identity.

mod git;
mod oauth;
mod profiles;

use super::{
    CheckedProject, artifact, identity::Session, input, server::response as asset_response,
    storage::PostgresStore, wire,
};
use crate::authority::Scope;
use crate::error::{Error, Result};
use axum::{
    Router,
    body::to_bytes,
    extract::{ConnectInfo, Request, State},
    http::{StatusCode, Uri, header},
    response::Response,
};
use serde::Deserialize;
use serde_json::{Value as Json, json};
use std::{collections::BTreeMap, fmt, net::SocketAddr, sync::Arc, time::Duration};
use tokio::sync::Semaphore;
use zeroize::{Zeroize, Zeroizing};

pub(super) struct Secrets {
    pub(super) configuration: Zeroizing<String>,
    dummy: Zeroizing<String>,
}
impl fmt::Debug for Secrets {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secrets([PRIVATE])")
    }
}

#[derive(Debug)]
pub(super) struct Server {
    pub(super) project: CheckedProject,
    pub(super) files: BTreeMap<String, String>,
    pub(super) pages: BTreeMap<String, String>,
    pub(super) authority: String,
    pub(super) origin: String,
    pub(super) contract: String,
    pub(super) cookie: String,
    pub(super) secrets: Arc<Secrets>,
    pub(super) permits: Arc<Semaphore>,
    pub(super) health_permits: Arc<Semaphore>,
    pub(super) pool: Option<Arc<crate::relational::Pool>>,
    pub(super) mailbox: Option<super::accounts::Mailbox>,
    pub(super) runtime: crate::RuntimeGrants,
    pub(super) objects: Option<super::files::Store>,
    pub(super) sockets: Arc<Semaphore>,
    pub(super) draining: std::sync::atomic::AtomicBool,
    pub(super) requests: std::sync::atomic::AtomicU64,
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
    mailbox: Option<&std::path::Path>,
) -> Result<()> {
    serve_runtime(
        project,
        address,
        origin,
        configuration,
        mailbox,
        crate::RuntimeGrants::default(),
    )
}

pub(super) fn serve_runtime(
    project: &CheckedProject,
    address: SocketAddr,
    origin: &str,
    configuration: &str,
    mailbox: Option<&std::path::Path>,
    runtime: crate::RuntimeGrants,
) -> Result<()> {
    for effect in &project.server.grants {
        if let crate::authority::Effect::Host(name) = effect
            && let Some(name) = name.strip_prefix("Http<").and_then(|s| s.strip_suffix('>'))
        {
            runtime.require(name)?;
        }
    }
    if !address.ip().is_loopback() {
        return Err(input("connected development serving is loopback-only"));
    }
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(input(
            "call synchronous connected serving outside an async runtime",
        ));
    }
    let _deployment_guard = super::deployment::guard(project, configuration, false)?;
    let objects = super::files::Store::open(project, configuration, &runtime, false)?;
    super::git::validate(project, configuration, &runtime)?;
    oauth::validate_runtime(project, &runtime)?;
    let authority = check_origin(origin)?;
    let mailbox = match (&project.manifest.identity, mailbox) {
        (Some(_), Some(path)) => Some(super::accounts::Mailbox::open(path)?),
        (None, None) => None,
        _ => {
            return Err(input(
                "accounts-v1 serving requires an explicit private identity mailbox",
            ));
        }
    };
    let mut store = PostgresStore::open(project, configuration, false)?;
    store.finish_request(Ok(()))?;
    let secrets = Arc::new(Secrets {
        configuration: Zeroizing::new(configuration.into()),
        dummy: Zeroizing::new(crate::accounts::hash_password(
            &crate::authority::random_hex()?,
        )?),
    });
    let state = Arc::new(Server {
        runtime,
        objects,
        sockets: Arc::new(Semaphore::new(32)),
        draining: std::sync::atomic::AtomicBool::new(false),
        requests: std::sync::atomic::AtomicU64::new(0),
        mailbox,
        project: project.clone(),
        files: artifact::files(project)?
            .into_iter()
            .filter(|(p, _)| {
                p == "index.html" || p.starts_with("assets/") || p.starts_with("pages/")
            })
            .collect(),
        pages: project
            .browser
            .routes
            .iter()
            .map(|p| (p.clone(), artifact::page_key(p)))
            .collect(),
        authority,
        origin: origin.into(),
        contract: artifact::contract_id(project)?,
        cookie: format!(
            "__Host-diamond_{}",
            &artifact::hash(project.manifest.name.as_bytes())[..16]
        ),
        secrets,
        permits: Arc::new(Semaphore::new(16)),
        health_permits: Arc::new(Semaphore::new(2)),
        pool: project
            .server
            .storage
            .as_ref()
            .map(|_| crate::relational::Pool::new(configuration)),
    });
    run(state, address)
}

fn run(state: Arc<Server>, address: SocketAddr) -> Result<()> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(4)
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(address).await?;
        let app = Router::new()
            .fallback(handler)
            .with_state(Arc::clone(&state));
        eprintln!("Diamond connected server: {}", state.origin);
        let (stop, cleaner) = super::files::cleaner(Arc::clone(&state));
        let draining_state = Arc::clone(&state);
        let result = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(async move {
            let _signal = tokio::signal::ctrl_c().await;
            draining_state
                .draining
                .store(true, std::sync::atomic::Ordering::Relaxed);
        })
        .await
        .map_err(Error::Io);
        let _sent = stop.send(());
        let _joined = cleaner.await;
        let _sockets = tokio::time::timeout(
            Duration::from_secs(5),
            Arc::clone(&state.sockets).acquire_many_owned(32),
        )
        .await;
        result
    });
    // Retain the pool until the outer runtime has joined all owned blocking work.
    // Its synchronous drivers must be destroyed outside an async runtime context.
    drop(runtime);
    drop(state);
    result
}

async fn handler(
    state: State<Arc<Server>>,
    peer: ConnectInfo<SocketAddr>,
    request: Request,
) -> Response {
    let class = if request.uri().path().starts_with("/api/") {
        "api"
    } else if request.uri().path().starts_with("/_diamond/git/") {
        "git"
    } else if request.uri().path().starts_with("/_diamond/rpc/") {
        "rpc"
    } else if request.uri().path().starts_with("/_diamond/files/") {
        "files"
    } else if request.uri().path().starts_with("/_diamond/events/") {
        "events"
    } else if request.uri().path().starts_with("/_diamond/") {
        "control"
    } else {
        "asset"
    };
    let response = handler_inner(state, peer, request).await;
    eprintln!(
        "{}",
        serde_json::json!({"event":"request_complete","class":class,"status":response.status().as_u16()})
    );
    response
}

async fn handler_inner(
    State(server): State<Arc<Server>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    request: Request,
) -> Response {
    if !valid_request(&server, &request) {
        return json_response(StatusCode::BAD_REQUEST, &json!({"error":"invalid_request"}));
    }
    server
        .requests
        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    if request.uri().path().starts_with("/_diamond/health") {
        let Ok(_permit) = Arc::clone(&server.health_permits).try_acquire_owned() else {
            return failure(&Error::Limit("health capacity"));
        };
        return super::deployment::health(server, request).await;
    }
    let Ok(permit) = Arc::clone(&server.permits).try_acquire_owned() else {
        return failure(&Error::Limit("request capacity"));
    };
    if server.draining.load(std::sync::atomic::Ordering::Relaxed) {
        return failure(&Error::Limit("server draining"));
    }
    if request.uri().path().starts_with("/_diamond/git/") {
        return git::request(server, request, peer.ip().to_string(), permit).await;
    }
    if request.uri().path().starts_with("/_diamond/oauth/") {
        return oauth::request(server, request, peer.ip().to_string(), permit).await;
    }
    if request.uri().path().starts_with("/_diamond/events/") {
        drop(permit);
        let Ok(socket_permit) = Arc::clone(&server.sockets).try_acquire_owned() else {
            return failure(&Error::Limit("socket capacity"));
        };
        return super::events::request(server, request, socket_permit).await;
    }
    let peer = peer.ip().to_string();
    let path = request.uri().path().to_owned();
    if path.starts_with("/api/") {
        return profiles::api_request(server, request, peer, permit).await;
    }
    if request.headers().contains_key(header::AUTHORIZATION) {
        return failure(&Error::Denied);
    }
    if path.starts_with("/_diamond/files/") {
        return super::files::request(server, request, permit).await;
    }
    request_body(server, request, peer, permit).await
}

async fn request_body(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> Response {
    let path = request.uri().path().to_owned();
    if request.method() == "GET" || request.method() == "HEAD" {
        if path != "/_diamond/session" {
            let key = server
                .pages
                .get(&path)
                .map_or_else(|| path.trim_start_matches('/'), String::as_str);
            return server.files.get(key).map_or_else(
                || json_response(StatusCode::NOT_FOUND, &json!({"error":"not_found"})),
                |text| {
                    response(
                        StatusCode::OK,
                        artifact::content_type(key),
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
    let known = ["/_diamond/session", "/_diamond/login", "/_diamond/logout"]
        .contains(&path.as_str())
        || path
            .strip_prefix("/_diamond/rpc/")
            .is_some_and(|name| server.project.rpc.contains_key(name))
        || (server.project.manifest.identity.is_some() && super::accounts::route(&path));
    if !known {
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
    dispatch(server, path, token, csrf, body, peer, permit).await
}

async fn dispatch(
    server: Arc<Server>,
    path: String,
    token: Option<Zeroizing<String>>,
    csrf: String,
    body: Zeroizing<Vec<u8>>,
    peer: String,
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
            &peer,
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
    peer: &str,
) -> Result<(Json, Option<String>)> {
    if server.project.manifest.identity.is_some() && !path.starts_with("/_diamond/rpc/") {
        super::accounts::charge(
            &server.project,
            &server.secrets.configuration,
            server.pool.as_ref(),
            "auth",
            peer,
            &super::accounts::subject(path, body),
        )?;
        if !path.starts_with("/_diamond/rpc/") {
            return profiles::account_work(server, path, token, csrf, body);
        }
    }
    // Decode public input/contract before opening an application transaction.
    let decoded = if let Some(name) = path.strip_prefix("/_diamond/rpc/") {
        let op = &server.project.rpc[name];
        let envelope: RpcBody =
            super::wire::strict_from_slice(body).map_err(|_| input("invalid RPC envelope"))?;
        if envelope.contract != server.contract {
            return Err(input_error_contract());
        }
        Some(profiles::decode(op, &envelope.arguments, &server.project)?)
    } else {
        None
    };
    let mut store = PostgresStore::open_with(
        &server.project,
        &server.secrets.configuration,
        false,
        server.pool.as_ref(),
    )?;
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
    let name = path.strip_prefix("/_diamond/rpc/").ok_or(Error::Denied)?;
    if principal.id().is_none() && !server.project.manifest.public_rpc.contains(name) {
        return Err(Error::Denied);
    }
    let op = &server.project.rpc[name];
    let grants = &server.project.server.functions[&op.function].effects;
    let scope = Scope::new(&principal, grants);
    let value = profiles::invoke(
        &server.project,
        op,
        &decoded.ok_or(Error::Denied)?,
        &scope,
        &mut store,
        &server.runtime,
    )?;
    Ok((json!({"value":value}), None))
}

pub(super) fn one_header<'a>(request: &'a Request, name: &str) -> Option<&'a str> {
    let mut values = request.headers().get_all(name).iter();
    let value = values.next()?.to_str().ok()?;
    if values.next().is_some() {
        None
    } else {
        Some(value)
    }
}

fn valid_request(server: &Server, request: &Request) -> bool {
    let git = request.uri().path().starts_with("/_diamond/git/");
    let oauth_callback = request.method() == "GET"
        && request.uri().path().starts_with("/_diamond/oauth/")
        && request.uri().path().ends_with("/callback");
    one_header(request, "host") == Some(server.authority.as_str())
        && !request
            .headers()
            .keys()
            .any(|k| k == "forwarded" || k.as_str().starts_with("x-forwarded-"))
        && (request.uri().query().is_none() || git || oauth_callback)
        && request.uri().authority().is_none()
        && !request.uri().path().contains(['%', '\\'])
        && !request.uri().path().starts_with("//")
        && (!request.headers().contains_key(header::TRANSFER_ENCODING)
            || (git
                && !request.headers().contains_key(header::CONTENT_LENGTH)
                && one_header(request, header::TRANSFER_ENCODING.as_str()) == Some("chunked")))
        && (!request.headers().contains_key(header::UPGRADE)
            || request.uri().path().starts_with("/_diamond/events/"))
        && (!request.headers().contains_key(header::ORIGIN)
            || one_header(request, "origin") == Some(server.origin.as_str()))
        && (one_header(request, "sec-fetch-site") != Some("cross-site") || oauth_callback)
        && (!request.headers().contains_key(header::CONTENT_LENGTH)
            || one_header(request, "content-length")
                .and_then(|v| v.parse::<u64>().ok())
                .is_some_and(|n| {
                    n <= if git {
                        super::git::MAX_TRANSPORT_BYTES
                    } else if request.method() == "PUT"
                        && request.uri().path().starts_with("/_diamond/files/")
                    {
                        super::files::MAX_BYTES
                    } else {
                        16_384
                    }
                }))
}

pub(super) fn cookie(request: &Request, name: &str) -> Result<Option<Zeroizing<String>>> {
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
        "{name}={token}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={}",
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
pub(super) fn json_response(status: StatusCode, value: &Json) -> Response {
    response(
        status,
        "application/json; charset=utf-8",
        &value.to_string(),
        false,
    )
}
fn response(status: StatusCode, kind: &'static str, text: &str, head: bool) -> Response {
    let mut response = asset_response(status, kind, text, head);
    if kind != "image/svg+xml" {
        response.headers_mut().insert("content-security-policy",header::HeaderValue::from_static("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"));
    }
    response
}

pub(super) fn failure(error: &Error) -> Response {
    let (status, code) = match error {
        Error::Unauthenticated => (StatusCode::UNAUTHORIZED, "authentication_required"),
        Error::Denied => (StatusCode::FORBIDDEN, "denied"),
        Error::Aborted | Error::Conflict => (StatusCode::CONFLICT, "aborted"),
        Error::OutcomeUnknown(_) => (StatusCode::SERVICE_UNAVAILABLE, "outcome_unknown"),
        Error::Input(message) if message == "RPC contract mismatch" => {
            (StatusCode::CONFLICT, "contract_mismatch")
        }
        Error::Input(message) if message.starts_with("PostgreSQL") => {
            (StatusCode::SERVICE_UNAVAILABLE, "unavailable")
        }
        Error::Input(_) => (StatusCode::BAD_REQUEST, "invalid_input"),
        Error::Limit("shared login rate" | "identity rate") => {
            (StatusCode::TOO_MANY_REQUESTS, "rate_limited")
        }
        _ => (StatusCode::SERVICE_UNAVAILABLE, "unavailable"),
    };
    let mut response = json_response(status, &json!({"error":code}));
    if status == StatusCode::UNAUTHORIZED {
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            header::HeaderValue::from_static("Bearer realm=\"diamond\""),
        );
    }
    response
}

fn check_origin(origin: &str) -> Result<String> {
    super::deployment::origin(origin)
}

pub(super) fn development_origin(origin: &str) -> Result<String> {
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
    Ok(authority.to_string())
}
