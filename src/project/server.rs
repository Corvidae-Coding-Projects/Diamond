//! Bounded concurrent loopback asset serving; no server-function exposure in W1.

use super::{CheckedProject, artifact, input};
use crate::error::{Error, Result};
use axum::Router;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{Method, StatusCode, header};
use axum::response::Response;
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::Semaphore;

#[derive(Debug)]
struct Server {
    address: SocketAddr,
    files: BTreeMap<String, String>,
    permits: Semaphore,
}

pub(super) fn serve(project: &CheckedProject, address: SocketAddr) -> Result<()> {
    if !address.ip().is_loopback() {
        return Err(input(
            "W1 project serving is loopback-only; production deployment is a later checkpoint",
        ));
    }
    if tokio::runtime::Handle::try_current().is_ok() {
        return Err(input(
            "call the synchronous project serve API outside an async runtime",
        ));
    }
    let files = artifact::files(project)?
        .into_iter()
        .filter(|(path, _)| path == "index.html" || path.starts_with("assets/"))
        .collect();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .max_blocking_threads(4)
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(address).await?;
        let address = listener.local_addr()?;
        let state = Arc::new(Server {
            address,
            files,
            permits: Semaphore::new(16),
        });
        let application = Router::new().fallback(handler).with_state(state);
        eprintln!("Diamond W1 project server: http://{address}");
        axum::serve(listener, application)
            .with_graceful_shutdown(async {
                let _signal_result = tokio::signal::ctrl_c().await;
            })
            .await
            .map_err(Error::Io)
    })
}

async fn handler(State(server): State<Arc<Server>>, request: Request) -> Response {
    let Ok(_permit) = server.permits.try_acquire() else {
        return response(
            StatusCode::SERVICE_UNAVAILABLE,
            "text/plain; charset=utf-8",
            "Request capacity exhausted",
            false,
        );
    };
    if request.method() != Method::GET && request.method() != Method::HEAD {
        return response(
            StatusCode::METHOD_NOT_ALLOWED,
            "text/plain; charset=utf-8",
            "Method not allowed",
            false,
        );
    }
    let head = request.method() == Method::HEAD;
    let hosts = request
        .headers()
        .get_all(header::HOST)
        .iter()
        .collect::<Vec<_>>();
    let host = hosts.first().and_then(|value| value.to_str().ok());
    if hosts.len() != 1
        || !host.is_some_and(|host| {
            host == server.address.to_string()
                || host == format!("localhost:{}", server.address.port())
        })
        || request
            .headers()
            .keys()
            .any(|name| name == "forwarded" || name.as_str().starts_with("x-forwarded-"))
        || request.uri().query().is_some()
        || request.uri().path().starts_with("//")
        || request.uri().path().contains(['%', '\\'])
        || request.headers().contains_key(header::TRANSFER_ENCODING)
        || request.headers().contains_key(header::UPGRADE)
        || request
            .headers()
            .get(header::CONTENT_LENGTH)
            .is_some_and(|length| length != "0")
    {
        return response(
            StatusCode::BAD_REQUEST,
            "text/plain; charset=utf-8",
            "Invalid project request",
            head,
        );
    }
    if request
        .headers()
        .get_all(header::ORIGIN)
        .iter()
        .any(|origin| origin.to_str().ok() != host.map(|host| format!("http://{host}")).as_deref())
    {
        return response(
            StatusCode::FORBIDDEN,
            "text/plain; charset=utf-8",
            "Origin denied",
            head,
        );
    }
    let path = if request.uri().path() == "/" {
        "index.html"
    } else {
        request.uri().path().trim_start_matches('/')
    };
    if let Some(content) = server.files.get(path) {
        let kind = if path == "index.html" {
            "text/html; charset=utf-8"
        } else {
            "text/javascript; charset=utf-8"
        };
        return response(StatusCode::OK, kind, content, head);
    }
    response(
        StatusCode::NOT_FOUND,
        "text/plain; charset=utf-8",
        "Not found",
        head,
    )
}

fn response(status: StatusCode, kind: &'static str, text: &str, head: bool) -> Response {
    let mut response = Response::new(if head {
        Body::empty()
    } else {
        Body::from(text.to_owned())
    });
    *response.status_mut() = status;
    let headers = response.headers_mut();
    headers.insert(
        header::CONTENT_TYPE,
        kind.parse()
            .unwrap_or_else(|_| header::HeaderValue::from_static("text/plain")),
    );
    if let Ok(length) = text.len().to_string().parse() {
        headers.insert(header::CONTENT_LENGTH, length);
    }
    headers.insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
    );
    headers.insert(
        "x-content-type-options",
        header::HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        "cross-origin-resource-policy",
        header::HeaderValue::from_static("same-origin"),
    );
    headers.insert("content-security-policy",header::HeaderValue::from_static("default-src 'none'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"));
    response
}
