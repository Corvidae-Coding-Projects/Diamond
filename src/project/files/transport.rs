//! Bounded, authenticated raw-body streams; owned files close on cancellation.
use super::super::connected::{self, Server};
use super::{Store, operations::Access};
use crate::{Error, error::Result};
use axum::{
    body::{Body, to_bytes},
    extract::Request,
    http::{StatusCode, header},
    response::Response,
};
use futures_util::StreamExt as _;
use sha2::{Digest as _, Sha256};
use std::{path::PathBuf, sync::Arc, time::Duration};
use tokio::io::AsyncWriteExt as _;
use zeroize::Zeroizing;

struct Partial {
    path: PathBuf,
    keep: bool,
}
impl Drop for Partial {
    fn drop(&mut self) {
        if !self.keep {
            let _removed = std::fs::remove_file(&self.path);
        }
    }
}
struct Job {
    server: Arc<Server>,
    binding: String,
    id: String,
    action: String,
    token: Option<Zeroizing<String>>,
    csrf: String,
    upload: Zeroizing<String>,
    write: bool,
    permit: Arc<crate::project::admission::Permit>,
}
impl Job {
    fn access(&self) -> Access<'_> {
        Access {
            server: &self.server,
            binding: &self.binding,
            token: self.token.as_deref().map(String::as_str),
            csrf: &self.csrf,
            upload: &self.upload,
            write: self.write,
        }
    }
}
pub(in crate::project) async fn request(
    server: Arc<Server>,
    request: Request,
    permit: crate::project::admission::Permit,
) -> Response {
    match dispatch(server, request, Arc::new(permit)).await {
        Ok(v) => v,
        Err(e) => connected::failure(&e),
    }
}
async fn dispatch(
    server: Arc<Server>,
    request: Request,
    permit: Arc<crate::project::admission::Permit>,
) -> Result<Response> {
    let path = request
        .uri()
        .path()
        .strip_prefix("/_diamond/files/")
        .ok_or(Error::Denied)?;
    let parts: Vec<_> = path.split('/').collect();
    if !(2..=3).contains(&parts.len())
        || !server
            .project
            .manifest
            .files
            .as_ref()
            .is_some_and(|c| c.bindings.contains_key(parts[0]))
    {
        return Err(Error::Denied);
    }
    let write = request.method() != "GET";
    if write && connected::one_header(&request, "origin") != Some(server.origin.as_str()) {
        return Err(Error::Denied);
    }
    let token = connected::cookie(&request, &server.cookie)?;
    let job = Job {
        server: Arc::clone(&server),
        binding: parts[0].into(),
        id: parts[1].into(),
        action: parts.get(2).copied().unwrap_or_default().into(),
        token,
        csrf: connected::one_header(&request, "x-diamond-csrf")
            .unwrap_or_default()
            .into(),
        upload: Zeroizing::new(
            connected::one_header(&request, "x-diamond-upload")
                .unwrap_or_default()
                .into(),
        ),
        write,
        permit,
    };
    if request.method() == "PUT" && job.action.is_empty() && job.id != "begin" {
        if connected::one_header(&request, "content-type") != Some("application/octet-stream") {
            return Err(Error::Denied);
        }
        let length = connected::one_header(&request, "content-length")
            .and_then(|s| s.parse::<i64>().ok())
            .ok_or(Error::Denied)?;
        return upload(job, request.into_body(), length).await;
    }
    if request.method() == "GET" && job.action.is_empty() && job.id != "begin" {
        return download(job).await;
    }
    let method = request.method().as_str().to_owned();
    if !((method == "GET" && job.action == "status")
        || (method == "POST"
            && ((job.id == "begin" && job.action.is_empty())
                || ["finish", "delete"].contains(&job.action.as_str()))))
    {
        return Err(Error::Denied);
    }
    if method == "POST"
        && connected::one_header(&request, "content-type") != Some("application/json")
    {
        return Err(Error::Denied);
    }
    let bytes = tokio::time::timeout_at(
        crate::budget::until(Duration::from_secs(3)),
        to_bytes(request.into_body(), 16384),
    )
    .await
    .map_err(|_| Error::Limit("file request body"))?
    .map_err(|_| Error::Limit("file request body"))?;
    let result = crate::project::admission::blocking(move || {
        let _permit = &job.permit;
        let access = job.access();
        if job.id == "begin" {
            return access.begin(&bytes);
        }
        if method == "GET" {
            if !bytes.is_empty() {
                return Err(Error::Denied);
            }
            return access.status(&job.id);
        }
        let empty: std::collections::BTreeMap<String, serde_json::Value> =
            super::super::wire::strict_from_slice(&bytes).map_err(|_| Error::Denied)?;
        if !empty.is_empty() {
            return Err(Error::Denied);
        }
        if job.action == "finish" {
            access.finish(&job.id)
        } else {
            access.delete(&job.id)
        }
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))??;
    Ok(connected::json_response(StatusCode::OK, &result))
}
async fn upload(job: Job, body: Body, length: i64) -> Result<Response> {
    let job = Arc::new(job);
    let work = Arc::clone(&job);
    let (file, path) = crate::project::admission::blocking(move || {
        let _permit = &work.permit;
        work.access().claim(&work.id, length)?;
        let store: &Store = work.server.objects.as_ref().ok_or(Error::Denied)?;
        let path = store.path(&work.id, true)?;
        let file = super::options().write(true).create_new(true).open(&path)?;
        Ok::<_, Error>((file, path))
    })
    .await
    .map_err(|_| Error::Denied)??;
    let mut partial = Partial { path, keep: false };
    let mut file = tokio::fs::File::from_std(file);
    let mut stream = body.into_data_stream();
    let mut sha = Sha256::new();
    let mut received = 0i64;
    tokio::time::timeout_at(crate::budget::until(Duration::from_mins(10)), async {
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| Error::Input("interrupted upload".into()))?;
            received =
                received.saturating_add(i64::try_from(chunk.len()).map_err(|_| Error::Denied)?);
            if received > length {
                return Err(Error::Limit("upload length"));
            }
            sha.update(&chunk);
            file.write_all(&chunk).await?;
        }
        if received != length {
            return Err(Error::Input("incomplete upload".into()));
        }
        file.sync_all().await?;
        Ok(())
    })
    .await
    .map_err(|_| Error::Limit("upload deadline"))??;
    drop(file);
    let hash = super::hex(&sha.finalize());
    let work = Arc::clone(&job);
    crate::project::admission::blocking(move || work.access().staged(&work.id, &hash))
        .await
        .map_err(|_| Error::OutcomeUnknown(String::new()))??;
    partial.keep = true;
    Ok(connected::json_response(
        StatusCode::OK,
        &serde_json::json!({"id":job.id,"state":"staged"}),
    ))
}

async fn download(job: Job) -> Result<Response> {
    let (file, length, permit) = crate::project::admission::blocking(move || {
        let result = job.access().download(&job.id)?;
        Ok::<_, Error>((result.0, result.1, job.permit))
    })
    .await
    .map_err(|_| Error::Denied)??;
    let deadline = crate::budget::until(Duration::from_mins(10));
    let stream =
        tokio_util::io::ReaderStream::with_capacity(tokio::fs::File::from_std(file), 65536);
    let stream = futures_util::stream::unfold(
        (stream, deadline, permit),
        |(mut stream, deadline, permit)| async move {
            match tokio::time::timeout_at(deadline, stream.next()).await {
                Ok(Some(chunk)) => Some((chunk, (stream, deadline, permit))),
                Ok(None) => None,
                Err(_) => Some((
                    Err(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "download deadline",
                    )),
                    (stream, tokio::time::Instant::now(), permit),
                )),
            }
        },
    );
    // take_until ends an otherwise repeatedly timed-out stream and drops its file.
    let stream = stream.take_until(tokio::time::sleep_until(deadline));
    Response::builder()
        .status(200)
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .header(header::CONTENT_LENGTH, length)
        .header(
            header::CONTENT_DISPOSITION,
            "attachment; filename=attachment.bin",
        )
        .header("x-content-type-options", "nosniff")
        .header(header::CACHE_CONTROL, "no-store")
        .header("content-security-policy", "default-src 'none'; sandbox")
        .body(Body::from_stream(stream))
        .map_err(|_| Error::Denied)
}
