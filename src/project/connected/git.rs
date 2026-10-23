//! Bounded Git smart HTTP transport backed by the fixed native Git adapter.

use super::{
    Arc, Error, Request, Response, Result, Server, StatusCode, failure, header, one_header,
};
use axum::body::{Body, Bytes};
use base64::Engine as _;
use futures_util::StreamExt as _;
use std::{ffi::OsStr, io, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::Command,
    sync::mpsc,
};
use zeroize::Zeroizing;

const HEADER_LIMIT: usize = 16_384;
const CHUNK: usize = 65_536;
const TRANSPORT_DEADLINE: Duration = Duration::from_mins(15);

#[derive(Clone, Copy)]
enum Service {
    Upload,
    Receive,
}

impl Service {
    const fn name(self) -> &'static str {
        match self {
            Self::Upload => "git-upload-pack",
            Self::Receive => "git-receive-pack",
        }
    }

    const fn write(self) -> bool {
        matches!(self, Self::Receive)
    }
}

struct Route {
    binding: String,
    repository: String,
    suffix: &'static str,
    query: String,
    service: Service,
}

struct BackendRequest {
    peer: String,
    content_type: Option<String>,
    content_length: Option<u64>,
}

pub(super) async fn request(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> Response {
    match request_inner(server, request, peer, permit).await {
        Ok(response) => response,
        Err(error) => git_failure_response(&error),
    }
}

fn git_failure_response(error: &Error) -> Response {
    let mut response = failure(error);
    if matches!(error, Error::Unauthenticated) {
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            header::HeaderValue::from_static("Basic realm=\"diamond\", charset=\"UTF-8\""),
        );
    }
    response
}

async fn request_inner(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> Result<Response> {
    if request.headers().contains_key(header::COOKIE)
        || one_header(&request, "git-protocol").is_some_and(|value| value != "version=2")
    {
        return Err(Error::Denied);
    }
    let route = route(&request)?;
    let token = authorization(&request)?;
    if token.is_none() && route.service.write() {
        return Err(Error::Unauthenticated);
    }
    let content_type = if request.method() == "POST" {
        let expected = format!("application/x-{}-request", route.service.name());
        if one_header(&request, header::CONTENT_TYPE.as_str()) != Some(expected.as_str()) {
            return Err(super::input("invalid Git content type"));
        }
        Some(expected)
    } else {
        None
    };
    let content_length = one_header(&request, header::CONTENT_LENGTH.as_str())
        .map(str::parse::<u64>)
        .transpose()
        .map_err(|_| super::input("invalid Git content length"))?;
    if request.method() == "GET"
        && (content_length.is_some_and(|length| length != 0)
            || request.headers().contains_key(header::TRANSFER_ENCODING))
    {
        return Err(super::input("Git discovery requests cannot contain a body"));
    }
    if content_length.is_some_and(|length| length > super::super::git::MAX_TRANSPORT_BYTES) {
        return Err(Error::Limit("Git request body"));
    }

    let work_server = Arc::clone(&server);
    let work_token = token.clone();
    let binding = route.binding.clone();
    let repository = route.repository.clone();
    let write = route.service.write();
    let work_peer = peer.clone();
    let transport = tokio::task::spawn_blocking(move || {
        super::super::accounts::charge(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
            "git",
            &work_peer,
            &format!("{binding}:{repository}"),
        )?;
        super::super::git::authorize_transport(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
            &work_server.runtime,
            super::super::git::TransportRequest {
                token: work_token.as_deref().map(String::as_str),
                binding: &binding,
                repository: &repository,
                write,
            },
        )
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))?;
    let transport = match transport {
        Err(Error::Denied) if token.is_none() => return Err(Error::Unauthenticated),
        other => other?,
    };

    backend_response(
        transport,
        route,
        request,
        BackendRequest {
            peer,
            content_type,
            content_length,
        },
        permit,
        &server.secrets.configuration,
    )
    .await
}

async fn backend_response(
    mut transport: super::super::git::Transport,
    route: Route,
    request: Request,
    backend: BackendRequest,
    permit: tokio::sync::OwnedSemaphorePermit,
    configuration: &str,
) -> Result<Response> {
    let fence = transport.fence.take();
    let mut command = Command::new(&transport.executable);
    command
        .args([
            OsStr::new("-c"),
            OsStr::new("protocol.file.allow=never"),
            OsStr::new("-c"),
            OsStr::new("core.askPass="),
            OsStr::new("-c"),
            OsStr::new("credential.helper="),
            OsStr::new("-c"),
            OsStr::new("pack.threads=1"),
            OsStr::new("-c"),
            OsStr::new("core.deltaBaseCacheLimit=16777216"),
            OsStr::new("http-backend"),
        ])
        .env_clear()
        .env("HOME", &transport.root)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_HTTP_EXPORT_ALL", "1")
        .env("GIT_PROJECT_ROOT", &transport.root)
        .env(
            "PATH_INFO",
            format!("/{}{}", transport.repository, route.suffix),
        )
        .env("QUERY_STRING", &route.query)
        .env("REQUEST_METHOD", request.method().as_str())
        .env("REMOTE_ADDR", &backend.peer)
        .env("SERVER_PROTOCOL", "HTTP/1.1")
        .env("LC_ALL", "C.UTF-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    if route.service.write() {
        let policy = super::super::git::policy_environment(&transport)?;
        command
            .env("REMOTE_USER", "diamond")
            .env("DIAMOND_GIT_STORE_ROOT", &transport.root)
            .env("DIAMOND_GIT_EXECUTABLE", &transport.executable)
            .env("DIAMOND_GIT_POLICY_DATABASE", configuration)
            .env("DIAMOND_GIT_POLICY_CLAIM", policy.claim)
            .env("DIAMOND_GIT_POLICY_SIGNATURE", policy.signature);
    }
    if let Some(value) = backend.content_type {
        command.env("CONTENT_TYPE", value);
    }
    if let Some(value) = backend.content_length {
        command.env("CONTENT_LENGTH", value.to_string());
    }
    if let Some(value) = one_header(&request, "git-protocol") {
        command.env("GIT_PROTOCOL", value);
    }
    let mut child = command.spawn().map_err(|_| git_failure())?;
    let stdin = child.stdin.take().ok_or_else(git_failure)?;
    let stdout = child.stdout.take().ok_or_else(git_failure)?;
    let stderr = child.stderr.take().ok_or_else(git_failure)?;
    let input = tokio::spawn(pump_input(request.into_body(), stdin));
    let errors = tokio::spawn(drain_stderr(stderr));
    let mut stdout = BufReader::with_capacity(CHUNK, stdout);
    let headers =
        match tokio::time::timeout(Duration::from_secs(10), cgi_headers(&mut stdout)).await {
            Ok(Ok(headers)) => headers,
            Ok(Err(error)) => {
                input.abort();
                errors.abort();
                let _killed = child.kill().await;
                let _waited = child.wait().await;
                return Err(error);
            }
            Err(_) => {
                input.abort();
                errors.abort();
                let _killed = child.kill().await;
                let _waited = child.wait().await;
                return Err(Error::Limit("Git response header deadline"));
            }
        };

    let receiver = spawn_stream(child, stdout, input, errors, permit, fence);
    let stream = futures_util::stream::unfold(receiver, |mut receiver| async move {
        receiver.recv().await.map(|item| (item, receiver))
    });
    let mut response = Response::builder().status(headers.status);
    for (name, value) in headers.values {
        response = response.header(name, value);
    }
    response
        .header("x-content-type-options", "nosniff")
        .body(Body::from_stream(stream))
        .map_err(|_| git_failure())
}

fn spawn_stream(
    mut child: tokio::process::Child,
    mut stdout: BufReader<tokio::process::ChildStdout>,
    mut input: tokio::task::JoinHandle<io::Result<()>>,
    mut errors: tokio::task::JoinHandle<io::Result<()>>,
    permit: tokio::sync::OwnedSemaphorePermit,
    fence: Option<super::super::git::ReceiveFence>,
) -> mpsc::Receiver<io::Result<Bytes>> {
    let (sender, receiver) = mpsc::channel(4);
    tokio::spawn(async move {
        let _permit = permit;
        let _fence = fence;
        let output_ok = tokio::time::timeout(
            TRANSPORT_DEADLINE,
            pump_output(&mut stdout, &sender, super::super::git::MAX_TRANSPORT_BYTES),
        )
        .await
        .is_ok_and(|result| result.is_ok());
        if !output_ok {
            input.abort();
            errors.abort();
            let _killed = child.kill().await;
            let _waited = child.wait().await;
            return;
        }
        let input_ok = tokio::time::timeout(Duration::from_secs(5), &mut input)
            .await
            .is_ok_and(|result| result.is_ok_and(|value| value.is_ok()));
        if !input_ok {
            input.abort();
        }
        let error_ok = tokio::time::timeout(Duration::from_secs(5), &mut errors)
            .await
            .is_ok_and(|result| result.is_ok_and(|value| value.is_ok()));
        if !error_ok {
            errors.abort();
        }
        let success = input_ok
            && error_ok
            && tokio::time::timeout(Duration::from_secs(5), child.wait())
                .await
                .is_ok_and(|result| result.is_ok_and(|status| status.success()));
        if !success {
            let _killed = child.kill().await;
            let _waited = child.wait().await;
        }
    });
    receiver
}

fn route(request: &Request) -> Result<Route> {
    let path = request
        .uri()
        .path()
        .strip_prefix("/_diamond/git/")
        .ok_or(Error::Denied)?;
    let (binding, rest) = path.split_once('/').ok_or(Error::Denied)?;
    if !super::super::resolve::identifier(binding) {
        return Err(Error::Denied);
    }
    let (repository, suffix) = rest.split_once(".git/").ok_or(Error::Denied)?;
    if !crate::auth::valid_id(repository) {
        return Err(Error::Denied);
    }
    let (service, suffix, query) = if request.method() == "GET" && suffix == "info/refs" {
        match request.uri().query() {
            Some("service=git-upload-pack") => {
                (Service::Upload, "/info/refs", "service=git-upload-pack")
            }
            Some("service=git-receive-pack") => {
                (Service::Receive, "/info/refs", "service=git-receive-pack")
            }
            _ => return Err(Error::Denied),
        }
    } else if request.method() == "POST"
        && request.uri().query().is_none()
        && suffix == "git-upload-pack"
    {
        (Service::Upload, "/git-upload-pack", "")
    } else if request.method() == "POST"
        && request.uri().query().is_none()
        && suffix == "git-receive-pack"
    {
        (Service::Receive, "/git-receive-pack", "")
    } else {
        return Err(Error::Denied);
    };
    Ok(Route {
        binding: binding.to_owned(),
        repository: repository.to_owned(),
        suffix,
        query: query.to_owned(),
        service,
    })
}

fn authorization(request: &Request) -> Result<Option<Zeroizing<String>>> {
    let Some(value) = one_header(request, header::AUTHORIZATION.as_str()) else {
        return Ok(None);
    };
    if let Some(token) = value.strip_prefix("Bearer ") {
        return valid_token(token).map(Some);
    }
    let encoded = value.strip_prefix("Basic ").ok_or(Error::Unauthenticated)?;
    if encoded.len() > 512 {
        return Err(Error::Unauthenticated);
    }
    let decoded = Zeroizing::new(
        base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .map_err(|_| Error::Unauthenticated)?,
    );
    let decoded = std::str::from_utf8(&decoded).map_err(|_| Error::Unauthenticated)?;
    let (username, token) = decoded.split_once(':').ok_or(Error::Unauthenticated)?;
    if username != "diamond" {
        return Err(Error::Unauthenticated);
    }
    valid_token(token).map(Some)
}

fn valid_token(token: &str) -> Result<Zeroizing<String>> {
    if token.len() != 68
        || !token.strip_prefix("dia_").is_some_and(|secret| {
            secret
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
    {
        return Err(Error::Unauthenticated);
    }
    Ok(Zeroizing::new(token.to_owned()))
}

struct CgiHeaders {
    status: StatusCode,
    values: Vec<(String, String)>,
}

async fn cgi_headers(stdout: &mut BufReader<tokio::process::ChildStdout>) -> Result<CgiHeaders> {
    let mut consumed = 0_usize;
    let mut status = StatusCode::OK;
    let mut values = Vec::new();
    loop {
        let mut line = Vec::new();
        let count = stdout
            .read_until(b'\n', &mut line)
            .await
            .map_err(|_| git_failure())?;
        consumed = consumed.saturating_add(count);
        if count == 0 || consumed > HEADER_LIMIT || !line.ends_with(b"\n") {
            return Err(git_failure());
        }
        if line == b"\n" || line == b"\r\n" {
            break;
        }
        let line = std::str::from_utf8(&line).map_err(|_| git_failure())?;
        let line = line.trim_end_matches(['\r', '\n']);
        let (name, value) = line.split_once(':').ok_or_else(git_failure)?;
        let value = value.trim_start();
        if name.eq_ignore_ascii_case("status") {
            let code = value
                .split_ascii_whitespace()
                .next()
                .ok_or_else(git_failure)?
                .parse::<u16>()
                .map_err(|_| git_failure())?;
            status = StatusCode::from_u16(code).map_err(|_| git_failure())?;
        } else if matches!(
            name.to_ascii_lowercase().as_str(),
            "content-type" | "cache-control" | "expires" | "pragma"
        ) {
            if value.len() > 1024 || value.bytes().any(|byte| byte.is_ascii_control()) {
                return Err(git_failure());
            }
            values.push((name.to_ascii_lowercase(), value.to_owned()));
        } else {
            return Err(git_failure());
        }
    }
    if !values.iter().any(|(name, _)| name == "content-type") {
        return Err(git_failure());
    }
    Ok(CgiHeaders { status, values })
}

async fn pump_input(body: Body, mut stdin: tokio::process::ChildStdin) -> io::Result<()> {
    let mut total = 0_u64;
    let mut stream = body.into_data_stream();
    while let Some(item) = stream.next().await {
        let bytes = item.map_err(io::Error::other)?;
        total = total.saturating_add(u64::try_from(bytes.len()).unwrap_or(u64::MAX));
        if total > super::super::git::MAX_TRANSPORT_BYTES {
            return Err(io::Error::other("Git request limit"));
        }
        stdin.write_all(&bytes).await?;
    }
    stdin.shutdown().await
}

async fn pump_output(
    stdout: &mut BufReader<tokio::process::ChildStdout>,
    sender: &mpsc::Sender<io::Result<Bytes>>,
    maximum: u64,
) -> io::Result<()> {
    let mut total = 0_u64;
    let mut buffer = vec![0_u8; CHUNK];
    loop {
        let count = stdout.read(&mut buffer).await?;
        if count == 0 {
            return Ok(());
        }
        total = total.saturating_add(u64::try_from(count).unwrap_or(u64::MAX));
        if total > maximum {
            return Err(io::Error::other("Git response limit"));
        }
        if sender
            .send(Ok(Bytes::copy_from_slice(&buffer[..count])))
            .await
            .is_err()
        {
            return Err(io::Error::new(io::ErrorKind::BrokenPipe, "client left"));
        }
    }
}

async fn drain_stderr(mut stderr: tokio::process::ChildStderr) -> io::Result<()> {
    let mut buffer = [0_u8; 4096];
    while stderr.read(&mut buffer).await? != 0 {}
    Ok(())
}

fn git_failure() -> Error {
    Error::Propagation("Git transport failed.".into())
}
