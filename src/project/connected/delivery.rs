//! Repository-authorized generic artifact and OCI Distribution reads.

use super::{Server, failure, git, json_response, one_header};
use crate::{Error, error::Result};
use axum::{
    body::Body,
    extract::Request,
    http::{Response, StatusCode, header},
};
use serde_json::json;
use std::io::{Seek as _, SeekFrom};
use std::sync::Arc;
use std::time::Duration;

enum Route {
    Artifact {
        binding: String,
        repository: String,
        id: String,
    },
    Blob {
        binding: String,
        repository: String,
        digest: String,
    },
    Manifest {
        binding: String,
        repository: String,
        name: String,
        reference: String,
    },
    Tags {
        binding: String,
        repository: String,
        name: String,
    },
}

impl Route {
    fn binding(&self) -> &str {
        match self {
            Self::Artifact { binding, .. }
            | Self::Blob { binding, .. }
            | Self::Manifest { binding, .. }
            | Self::Tags { binding, .. } => binding,
        }
    }

    fn repository(&self) -> &str {
        match self {
            Self::Artifact { repository, .. }
            | Self::Blob { repository, .. }
            | Self::Manifest { repository, .. }
            | Self::Tags { repository, .. } => repository,
        }
    }
}

pub(super) async fn request(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
) -> Response<Body> {
    match request_inner(server, request, peer, permit).await {
        Ok(response) => response,
        Err(error) => {
            let mut response = failure(&error);
            if matches!(error, Error::Unauthenticated) {
                response.headers_mut().insert(
                    header::WWW_AUTHENTICATE,
                    header::HeaderValue::from_static("Basic realm=\"diamond\", charset=\"UTF-8\""),
                );
            }
            response
        }
    }
}

async fn request_inner(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
) -> Result<Response<Body>> {
    if request.headers().contains_key(header::COOKIE)
        || request.headers().contains_key(header::TRANSFER_ENCODING)
        || one_header(&request, header::CONTENT_LENGTH.as_str()).is_some_and(|value| value != "0")
    {
        return Err(Error::Denied);
    }
    if request.method() != "GET" && request.method() != "HEAD" {
        return Ok(json_response(
            StatusCode::METHOD_NOT_ALLOWED,
            &json!({"error":"method"}),
        ));
    }
    if request.uri().path() == "/v2/" {
        return Ok(json_response(StatusCode::OK, &json!({})));
    }
    let route = parse(request.uri().path())?;
    let token = git::authorization(&request)?;
    let binding = route.binding().to_owned();
    let repository = route.repository().to_owned();
    let git_binding = super::super::delivery::git_binding(&server.project, &binding)?;
    let head = request.method() == "HEAD";
    let range = optional_header(&request, header::RANGE.as_str())?;
    let if_none_match = optional_header(&request, header::IF_NONE_MATCH.as_str())?;
    let if_range = optional_header(&request, header::IF_RANGE.as_str())?;
    let work_server = Arc::clone(&server);
    let work_token = token.clone();
    let (result, permit) = crate::project::admission::blocking(move || {
        super::super::accounts::charge(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
            "delivery",
            &peer,
            &format!("{binding}:{repository}"),
        )?;
        let authorized = super::super::git::authorize_transport(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
            &work_server.runtime,
            super::super::git::TransportRequest {
                token: work_token.as_deref().map(String::as_str),
                binding: &git_binding,
                repository: &repository,
                write: false,
            },
        );
        if matches!(authorized, Err(Error::Denied)) && work_token.is_none() {
            return Err(Error::Unauthenticated);
        }
        let _authorized = authorized?;
        let kind = match &route {
            Route::Artifact { id, .. } => super::super::delivery::FetchKind::Artifact(id),
            Route::Blob { digest, .. } => super::super::delivery::FetchKind::Blob(digest),
            Route::Manifest {
                name, reference, ..
            } => super::super::delivery::FetchKind::Manifest { name, reference },
            Route::Tags { name, .. } => super::super::delivery::FetchKind::Tags(name),
        };
        let fetch = super::super::delivery::fetch(
            &work_server.project,
            &work_server.secrets.configuration,
            &work_server.runtime,
            &binding,
            &repository,
            kind,
        )?;
        Ok::<_, Error>((fetch, permit))
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))??;
    fetch_response(
        result,
        head,
        range.as_deref(),
        if_none_match.as_deref(),
        if_range.as_deref(),
        permit,
    )
}

fn fetch_response(
    result: super::super::delivery::Fetch,
    head: bool,
    range: Option<&str>,
    if_none_match: Option<&str>,
    if_range: Option<&str>,
    permit: crate::project::admission::Permit,
) -> Result<Response<Body>> {
    let etag = result.digest.as_ref().map(|digest| format!("\"{digest}\""));
    if if_none_match.is_some_and(|candidate| {
        candidate == "*"
            || etag
                .as_deref()
                .is_some_and(|etag| weak_etag_matches(candidate, etag))
    }) {
        return not_modified_response(etag.as_deref(), permit);
    }
    let effective_range =
        if if_range.is_none() || etag.as_deref().is_some_and(|etag| if_range == Some(etag)) {
            range
        } else {
            None
        };
    let selected = effective_range.and_then(|value| byte_range(value, result.size));
    if effective_range.is_some() && selected.is_none() {
        return range_not_satisfiable(result.size, permit);
    }
    let (start, end) = selected.unwrap_or((0, result.size.saturating_sub(1)));
    let response_size = if result.size == 0 {
        0
    } else {
        end.saturating_sub(start).saturating_add(1)
    };
    let body = if head {
        drop(permit);
        Body::empty()
    } else {
        match result.body {
            super::super::delivery::FetchBody::Bytes(mut bytes) => {
                drop(permit);
                if selected.is_some() {
                    let start = usize::try_from(start).map_err(|_| Error::Denied)?;
                    let end = usize::try_from(end.saturating_add(1)).map_err(|_| Error::Denied)?;
                    bytes = bytes.get(start..end).ok_or(Error::Denied)?.to_vec();
                }
                Body::from(bytes)
            }
            super::super::delivery::FetchBody::File(mut file) => {
                use futures_util::StreamExt as _;
                use tokio::io::AsyncReadExt as _;
                file.seek(SeekFrom::Start(start))?;
                let file = tokio::fs::File::from_std(file).take(response_size);
                let stream = tokio_util::io::ReaderStream::with_capacity(file, 65_536);
                let deadline = crate::budget::until(Duration::from_mins(10));
                let stream = futures_util::stream::unfold(
                    (stream, deadline, permit),
                    |(mut stream, deadline, permit)| async move {
                        match tokio::time::timeout_at(deadline, stream.next()).await {
                            Ok(Some(chunk)) => Some((chunk, (stream, deadline, permit))),
                            Ok(None) => None,
                            Err(_) => Some((
                                Err(std::io::Error::new(
                                    std::io::ErrorKind::TimedOut,
                                    "delivery download deadline",
                                )),
                                (stream, deadline, permit),
                            )),
                        }
                    },
                );
                Body::from_stream(stream)
            }
        }
    };
    let mut response = Response::builder()
        .status(if selected.is_some() {
            StatusCode::PARTIAL_CONTENT
        } else {
            StatusCode::OK
        })
        .header(header::CONTENT_TYPE, result.media)
        .header(header::CONTENT_LENGTH, response_size.to_string())
        .header(header::CACHE_CONTROL, "private, no-store")
        .header(header::ACCEPT_RANGES, "bytes")
        .header("x-content-type-options", "nosniff")
        .body(body)
        .map_err(|_| Error::Denied)?;
    if let Some((start, end)) = selected {
        response.headers_mut().insert(
            header::CONTENT_RANGE,
            format!("bytes {start}-{end}/{}", result.size)
                .parse()
                .map_err(|_| Error::Denied)?,
        );
    }
    if let Some(etag) = etag {
        response
            .headers_mut()
            .insert(header::ETAG, etag.parse().map_err(|_| Error::Denied)?);
    }
    if let Some(digest) = result.digest {
        response.headers_mut().insert(
            "docker-content-digest",
            digest.parse().map_err(|_| Error::Denied)?,
        );
    }
    Ok(response)
}

fn not_modified_response(
    etag: Option<&str>,
    permit: crate::project::admission::Permit,
) -> Result<Response<Body>> {
    drop(permit);
    let mut response = Response::builder()
        .status(StatusCode::NOT_MODIFIED)
        .header(header::CACHE_CONTROL, "private, no-store")
        .body(Body::empty())
        .map_err(|_| Error::Denied)?;
    if let Some(etag) = etag {
        response
            .headers_mut()
            .insert(header::ETAG, etag.parse().map_err(|_| Error::Denied)?);
    }
    Ok(response)
}

fn range_not_satisfiable(
    size: u64,
    permit: crate::project::admission::Permit,
) -> Result<Response<Body>> {
    drop(permit);
    Response::builder()
        .status(StatusCode::RANGE_NOT_SATISFIABLE)
        .header(header::CONTENT_RANGE, format!("bytes */{size}"))
        .header(header::CACHE_CONTROL, "private, no-store")
        .body(Body::empty())
        .map_err(|_| Error::Denied)
}

fn optional_header(request: &Request, name: &str) -> Result<Option<String>> {
    let mut values = request.headers().get_all(name).iter();
    let value = values
        .next()
        .map(|value| value.to_str().map(str::to_owned))
        .transpose()
        .map_err(|_| Error::Denied)?;
    if values.next().is_some() || value.as_ref().is_some_and(|value| value.len() > 256) {
        return Err(Error::Denied);
    }
    Ok(value)
}

fn byte_range(value: &str, size: u64) -> Option<(u64, u64)> {
    let value = value.strip_prefix("bytes=")?;
    if value.contains(',') || size == 0 {
        return None;
    }
    let (start, end) = value.split_once('-')?;
    if start.is_empty() {
        let suffix = end.parse::<u64>().ok()?;
        if suffix == 0 {
            return None;
        }
        return Some((size.saturating_sub(suffix.min(size)), size - 1));
    }
    let start = start.parse::<u64>().ok()?;
    if start >= size {
        return None;
    }
    let end = if end.is_empty() {
        size - 1
    } else {
        end.parse::<u64>().ok()?.min(size - 1)
    };
    (start <= end).then_some((start, end))
}

fn weak_etag_matches(value: &str, etag: &str) -> bool {
    value.split(',').any(|candidate| {
        let candidate = candidate.trim();
        candidate == etag || candidate.strip_prefix("W/") == Some(etag)
    })
}

fn parse(path: &str) -> Result<Route> {
    if let Some(value) = path.strip_prefix("/_diamond/delivery/") {
        let parts = value.split('/').collect::<Vec<_>>();
        return match parts.as_slice() {
            [binding, repository, "artifacts", id] => Ok(Route::Artifact {
                binding: (*binding).into(),
                repository: (*repository).into(),
                id: (*id).into(),
            }),
            _ => Err(Error::Denied),
        };
    }
    let parts = path
        .strip_prefix("/v2/")
        .ok_or(Error::Denied)?
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() < 5 || parts.iter().any(|part| part.is_empty()) {
        return Err(Error::Denied);
    }
    let binding = parts[0].to_owned();
    let repository = parts[1].to_owned();
    if parts.ends_with(&["tags", "list"]) && parts.len() >= 5 {
        return Ok(Route::Tags {
            binding,
            repository,
            name: parts[2..parts.len() - 2].join("/"),
        });
    }
    let action = parts[parts.len() - 2];
    let value = parts[parts.len() - 1].to_owned();
    let name = parts[2..parts.len() - 2].join("/");
    match action {
        "blobs" if !name.is_empty() => Ok(Route::Blob {
            binding,
            repository,
            digest: value,
        }),
        "manifests" if !name.is_empty() => Ok(Route::Manifest {
            binding,
            repository,
            name,
            reference: value,
        }),
        _ => Err(Error::Denied),
    }
}
