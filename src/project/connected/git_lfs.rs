//! Authenticated Git LFS basic transfers and locking for a Git binding.

use super::{Arc, Error, Request, Response, Result, Server, StatusCode, git, header, one_header};
use axum::body::{Body, to_bytes};
use futures_util::StreamExt as _;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::{
    fmt::Write as _,
    fs::{File, OpenOptions},
    path::Path,
    time::Duration,
};
use tokio::io::AsyncWriteExt as _;

const MAX_OBJECT_BYTES: u64 = 1024 * 1024 * 1024;
const MAX_BATCH_OBJECTS: usize = 1000;
const TRANSFER_DEADLINE: Duration = Duration::from_mins(15);

#[derive(Debug)]
struct Route {
    binding: String,
    repository: String,
    action: Action,
}

#[derive(Debug)]
enum Action {
    Batch,
    Object(String),
    Locks,
    VerifyLocks,
    Unlock(String),
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BatchRequest {
    operation: String,
    #[serde(default)]
    transfers: Vec<String>,
    #[serde(default)]
    hash_algo: Option<String>,
    #[serde(default)]
    r#ref: Option<LfsRef>,
    objects: Vec<ObjectRequest>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct LfsRef {
    name: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ObjectRequest {
    oid: String,
    size: u64,
}

#[derive(Debug, Serialize)]
struct BatchResponse {
    transfer: &'static str,
    objects: Vec<ObjectResponse>,
}

#[derive(Debug, Serialize)]
struct ObjectResponse {
    oid: String,
    size: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    authenticated: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    actions: Option<Actions>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ObjectError>,
}

#[derive(Debug, Serialize)]
struct Actions {
    #[serde(skip_serializing_if = "Option::is_none")]
    upload: Option<ActionLink>,
    #[serde(skip_serializing_if = "Option::is_none")]
    download: Option<ActionLink>,
}

#[derive(Debug, Serialize)]
struct ActionLink {
    href: String,
}

#[derive(Debug, Serialize)]
struct ObjectError {
    code: u16,
    message: &'static str,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateLock {
    path: String,
    #[serde(default)]
    r#ref: Option<LfsRef>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct VerifyLocks {
    #[serde(default)]
    cursor: Option<String>,
    #[serde(default)]
    limit: Option<u32>,
    #[serde(default)]
    r#ref: Option<LfsRef>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UnlockRequest {
    #[serde(default)]
    force: bool,
}

pub(super) async fn request(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
) -> Response {
    match dispatch(server, request, peer, permit).await {
        Ok(response) => response,
        Err(error) => failure(&error),
    }
}

async fn dispatch(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
) -> Result<Response> {
    if request.headers().contains_key(header::COOKIE) || request.uri().query().is_some() {
        return Err(Error::Denied);
    }
    let route = route(&request)?;
    let token = git::authorization(&request)?;
    let write = match &route.action {
        Action::Batch => false,
        Action::Object(_) => request.method() == "PUT",
        Action::Locks | Action::VerifyLocks | Action::Unlock(_) => true,
    };
    if write && token.is_none() {
        return Err(Error::Unauthenticated);
    }
    match &route.action {
        Action::Batch => batch(server, request, peer, permit, route, token).await,
        Action::Object(oid) if request.method() == "PUT" => {
            let oid = oid.clone();
            upload(server, request, peer, permit, route, token, oid).await
        }
        Action::Object(oid) if request.method() == "GET" => {
            let oid = oid.clone();
            download(server, request, peer, permit, route, token, oid).await
        }
        Action::Locks | Action::VerifyLocks | Action::Unlock(_) => {
            locks(server, request, peer, permit, route, token).await
        }
        Action::Object(_) => Err(Error::Denied),
    }
}

async fn read_batch(request: Request) -> Result<BatchRequest> {
    require_json(&request)?;
    let body = tokio::time::timeout_at(
        crate::budget::until(Duration::from_secs(3)),
        to_bytes(request.into_body(), 1024 * 1024),
    )
    .await
    .map_err(|_| Error::Limit("LFS batch body"))?
    .map_err(|_| Error::Limit("LFS batch body"))?;
    let batch: BatchRequest = super::super::wire::strict_from_slice(&body)
        .map_err(|_| super::input("invalid LFS batch request"))?;
    if !matches!(batch.operation.as_str(), "upload" | "download")
        || batch.objects.is_empty()
        || batch.objects.len() > MAX_BATCH_OBJECTS
        || batch.transfers.iter().any(|value| value != "basic")
        || batch
            .hash_algo
            .as_deref()
            .is_some_and(|value| value != "sha256")
        || batch
            .r#ref
            .as_ref()
            .is_some_and(|value| !valid_ref_name(&value.name))
        || batch
            .objects
            .iter()
            .any(|object| !valid_oid(&object.oid) || object.size > MAX_OBJECT_BYTES)
    {
        return Err(super::input("invalid LFS batch request"));
    }
    Ok(batch)
}

fn batch_responses(
    objects: Vec<ObjectRequest>,
    upload: bool,
    authenticated: bool,
    existing: &std::collections::BTreeSet<String>,
    base: &str,
) -> Vec<ObjectResponse> {
    objects
        .into_iter()
        .map(|object| {
            if upload && existing.contains(&object.oid) {
                ObjectResponse {
                    oid: object.oid,
                    size: object.size,
                    authenticated: Some(true),
                    actions: None,
                    error: None,
                }
            } else if upload {
                ObjectResponse {
                    oid: object.oid.clone(),
                    size: object.size,
                    authenticated: Some(true),
                    actions: Some(Actions {
                        upload: Some(ActionLink {
                            href: format!("{base}{}", object.oid),
                        }),
                        download: None,
                    }),
                    error: None,
                }
            } else if existing.contains(&object.oid) {
                ObjectResponse {
                    oid: object.oid.clone(),
                    size: object.size,
                    authenticated: authenticated.then_some(true),
                    actions: Some(Actions {
                        upload: None,
                        download: Some(ActionLink {
                            href: format!("{base}{}", object.oid),
                        }),
                    }),
                    error: None,
                }
            } else {
                ObjectResponse {
                    oid: object.oid,
                    size: object.size,
                    authenticated: authenticated.then_some(true),
                    actions: None,
                    error: Some(ObjectError {
                        code: 404,
                        message: "Object does not exist",
                    }),
                }
            }
        })
        .collect()
}

async fn batch(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
    route: Route,
    token: Option<zeroize::Zeroizing<String>>,
) -> Result<Response> {
    let batch = read_batch(request).await?;
    let upload = batch.operation == "upload";
    if upload && token.is_none() {
        return Err(Error::Unauthenticated);
    }
    let work = Arc::clone(&server);
    let binding = route.binding.clone();
    let repository = route.repository.clone();
    let work_token = token.clone();
    let objects = batch.objects.clone();
    let existing = crate::project::admission::blocking(move || {
        let _permit = permit;
        charge_and_authorize(
            &work,
            &peer,
            work_token.as_deref().map(String::as_str),
            &binding,
            &repository,
            upload,
        )?;
        existing_objects(&work, &binding, &repository, &objects)
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))??;
    let base = format!(
        "{}/_diamond/git/{}/{}.git/info/lfs/objects/",
        server.origin, route.binding, route.repository
    );
    let replies = batch_responses(batch.objects, upload, token.is_some(), &existing, &base);
    lfs_json(
        StatusCode::OK,
        &BatchResponse {
            transfer: "basic",
            objects: replies,
        },
    )
}

async fn upload(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
    route: Route,
    token: Option<zeroize::Zeroizing<String>>,
    oid: String,
) -> Result<Response> {
    if one_header(&request, header::CONTENT_TYPE.as_str()) != Some("application/octet-stream") {
        return Err(Error::Denied);
    }
    let length = one_header(&request, header::CONTENT_LENGTH.as_str())
        .and_then(|value| value.parse::<u64>().ok())
        .ok_or(Error::Denied)?;
    if length > MAX_OBJECT_BYTES {
        return Err(Error::Limit("LFS object"));
    }
    let transport = authorize(
        &server,
        &peer,
        token.as_deref().map(String::as_str),
        &route,
        true,
    )
    .await?;
    let final_path = super::super::git::lfs_object_path(&transport, &oid)?;
    let temporary = final_path.with_extension(format!("part-{}", crate::authority::random_hex()?));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(&temporary)?;
    let mut file = tokio::fs::File::from_std(file);
    let mut body = request.into_body().into_data_stream();
    let mut digest = Sha256::new();
    let mut received = 0_u64;
    let transfer = async {
        while let Some(chunk) = body.next().await {
            let chunk = chunk.map_err(|_| super::input("interrupted LFS upload"))?;
            received =
                received.saturating_add(u64::try_from(chunk.len()).map_err(|_| Error::Denied)?);
            if received > length {
                return Err(Error::Limit("LFS object"));
            }
            digest.update(&chunk);
            file.write_all(&chunk).await?;
        }
        if received != length {
            return Err(super::input("incomplete LFS upload"));
        }
        file.sync_all().await?;
        Ok(())
    };
    if let Err(error) = tokio::time::timeout_at(crate::budget::until(TRANSFER_DEADLINE), transfer)
        .await
        .map_err(|_| Error::Limit("LFS upload deadline"))?
    {
        let _removed = tokio::fs::remove_file(&temporary).await;
        return Err(error);
    }
    drop(file);
    if hex(&digest.finalize()) != oid {
        let _removed = tokio::fs::remove_file(&temporary).await;
        return Err(super::input("LFS object digest mismatch"));
    }
    let work = Arc::clone(&server);
    let binding = route.binding.clone();
    let repository = route.repository.clone();
    let committed = crate::project::admission::blocking(move || {
        let _permit = permit;
        let created = publish_object(&temporary, &final_path, length)?;
        let result = record_object(&work, &binding, &repository, &oid, length);
        if result.is_err() && created {
            let _removed = std::fs::remove_file(&final_path);
        }
        result
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))?;
    committed?;
    empty_response(StatusCode::OK)
}

async fn download(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
    route: Route,
    token: Option<zeroize::Zeroizing<String>>,
    oid: String,
) -> Result<Response> {
    if request.headers().contains_key(header::CONTENT_LENGTH)
        || request.headers().contains_key(header::TRANSFER_ENCODING)
    {
        return Err(Error::Denied);
    }
    let transport = authorize(
        &server,
        &peer,
        token.as_deref().map(String::as_str),
        &route,
        false,
    )
    .await?;
    let path = super::super::git::lfs_object_path(&transport, &oid)?;
    let work = Arc::clone(&server);
    let binding = route.binding;
    let repository = route.repository;
    let (file, length) = crate::project::admission::blocking(move || {
        let expected = object_size(&work, &binding, &repository, &oid)?.ok_or(Error::Denied)?;
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt as _;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let file = options.open(&path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() || metadata.len() != expected {
            return Err(Error::Denied);
        }
        Ok::<_, Error>((file, expected))
    })
    .await
    .map_err(|_| Error::Denied)??;
    let stream =
        tokio_util::io::ReaderStream::with_capacity(tokio::fs::File::from_std(file), 65_536);
    let deadline = crate::budget::until(TRANSFER_DEADLINE);
    let stream = futures_util::stream::unfold(
        (stream, deadline, permit),
        |(mut stream, deadline, permit)| async move {
            match tokio::time::timeout_at(deadline, stream.next()).await {
                Ok(Some(chunk)) => Some((chunk, (stream, deadline, permit))),
                _ => None,
            }
        },
    );
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/octet-stream")
        .header(header::CONTENT_LENGTH, length)
        .header(header::CACHE_CONTROL, "no-store")
        .header("x-content-type-options", "nosniff")
        .body(Body::from_stream(stream))
        .map_err(|_| Error::Denied)
}

async fn locks(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
    route: Route,
    token: Option<zeroize::Zeroizing<String>>,
) -> Result<Response> {
    require_json(&request)?;
    let body = tokio::time::timeout_at(
        crate::budget::until(Duration::from_secs(3)),
        to_bytes(request.into_body(), 16_384),
    )
    .await
    .map_err(|_| Error::Limit("LFS lock body"))?
    .map_err(|_| Error::Limit("LFS lock body"))?;
    let transport = authorize(
        &server,
        &peer,
        token.as_deref().map(String::as_str),
        &route,
        true,
    )
    .await?;
    let actor = transport.actor;
    let work = Arc::clone(&server);
    let binding = route.binding;
    let repository = route.repository;
    let action = route.action;
    let value = crate::project::admission::blocking(move || {
        let _permit = permit;
        match action {
            Action::Locks => {
                let create: CreateLock = super::super::wire::strict_from_slice(&body)
                    .map_err(|_| super::input("invalid LFS lock request"))?;
                if !valid_lock_path(&create.path)
                    || create
                        .r#ref
                        .as_ref()
                        .is_some_and(|value| !valid_ref_name(&value.name))
                {
                    return Err(super::input("invalid LFS lock request"));
                }
                create_lock(&work, &binding, &repository, &actor, &create.path)
            }
            Action::VerifyLocks => {
                let verify: VerifyLocks = super::super::wire::strict_from_slice(&body)
                    .map_err(|_| super::input("invalid LFS lock verification"))?;
                if verify
                    .cursor
                    .as_deref()
                    .is_some_and(|value| !valid_lock_id(value))
                    || verify
                        .limit
                        .is_some_and(|value| !(1..=100).contains(&value))
                    || verify
                        .r#ref
                        .as_ref()
                        .is_some_and(|value| !valid_ref_name(&value.name))
                {
                    return Err(super::input("invalid LFS lock verification"));
                }
                verify_locks(
                    &work,
                    &binding,
                    &repository,
                    &actor,
                    verify.cursor.as_deref(),
                    verify.limit.unwrap_or(100),
                )
            }
            Action::Unlock(id) => {
                let unlock: UnlockRequest = super::super::wire::strict_from_slice(&body)
                    .map_err(|_| super::input("invalid LFS unlock request"))?;
                unlock_lock(&work, &binding, &repository, &actor, &id, unlock.force)
            }
            _ => Err(Error::Denied),
        }
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))??;
    lfs_json(StatusCode::OK, &value)
}

async fn authorize(
    server: &Arc<Server>,
    peer: &str,
    token: Option<&str>,
    route: &Route,
    write: bool,
) -> Result<super::super::git::Transport> {
    let work = Arc::clone(server);
    let peer = peer.to_owned();
    let token = token.map(str::to_owned);
    let binding = route.binding.clone();
    let repository = route.repository.clone();
    crate::project::admission::blocking(move || {
        charge_and_authorize(&work, &peer, token.as_deref(), &binding, &repository, write)
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))?
}

fn charge_and_authorize(
    server: &Server,
    peer: &str,
    token: Option<&str>,
    binding: &str,
    repository: &str,
    write: bool,
) -> Result<super::super::git::Transport> {
    super::super::accounts::charge(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
        "git_lfs",
        peer,
        &format!("{binding}:{repository}"),
    )?;
    super::super::git::authorize_transport(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
        &server.runtime,
        super::super::git::TransportRequest {
            token,
            binding,
            repository,
            write,
        },
    )
}

fn existing_objects(
    server: &Server,
    binding: &str,
    repository: &str,
    objects: &[ObjectRequest],
) -> Result<std::collections::BTreeSet<String>> {
    let mut database = super::super::storage::PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let mut existing = std::collections::BTreeSet::new();
    for object in objects {
        let rows = database.connection.query(
            "SELECT size FROM diamond_git.lfs_objects WHERE app=$1 AND binding=$2 AND repository=$3 AND oid=$4",
            &[&database.app, &binding, &repository, &object.oid],
        )?;
        if let Some(row) = rows.first() {
            let size: i64 = row.try_get(0).map_err(|_| Error::Denied)?;
            if size < 0 || u64::try_from(size).map_err(|_| Error::Denied)? != object.size {
                return database.finish_request(Err(Error::Conflict));
            }
            existing.insert(object.oid.clone());
        }
    }
    database.finish_request(Ok(existing))
}

fn record_object(
    server: &Server,
    binding: &str,
    repository: &str,
    oid: &str,
    size: u64,
) -> Result<()> {
    let size = i64::try_from(size).map_err(|_| Error::Denied)?;
    let mut database = super::super::storage::PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let rows = database.connection.query(
        "INSERT INTO diamond_git.lfs_objects(app,binding,repository,oid,size) VALUES($1,$2,$3,$4,$5) ON CONFLICT(app,binding,repository,oid) DO UPDATE SET size=EXCLUDED.size WHERE diamond_git.lfs_objects.size=EXCLUDED.size RETURNING oid",
        &[&database.app, &binding, &repository, &oid, &size],
    )?;
    database.finish_request(if rows.len() == 1 {
        Ok(())
    } else {
        Err(Error::Conflict)
    })
}

fn object_size(server: &Server, binding: &str, repository: &str, oid: &str) -> Result<Option<u64>> {
    let mut database = super::super::storage::PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let rows = database.connection.query(
        "SELECT size FROM diamond_git.lfs_objects WHERE app=$1 AND binding=$2 AND repository=$3 AND oid=$4",
        &[&database.app, &binding, &repository, &oid],
    )?;
    let value = rows
        .first()
        .map(|row| row.try_get::<_, i64>(0).map_err(|_| Error::Denied))
        .transpose()?
        .map(|value| u64::try_from(value).map_err(|_| Error::Denied))
        .transpose()?;
    database.finish_request(Ok(value))
}

fn publish_object(temporary: &Path, final_path: &Path, length: u64) -> Result<bool> {
    if let Ok(metadata) = std::fs::symlink_metadata(final_path) {
        if !metadata.is_file() || metadata.len() != length {
            return Err(Error::Conflict);
        }
        std::fs::remove_file(temporary)?;
        return Ok(false);
    }
    std::fs::rename(temporary, final_path)?;
    File::open(final_path.parent().ok_or(Error::Denied)?)?.sync_all()?;
    Ok(true)
}

fn create_lock(
    server: &Server,
    binding: &str,
    repository: &str,
    actor: &str,
    path: &str,
) -> Result<serde_json::Value> {
    let id = crate::authority::random_hex()?;
    let mut database = super::super::storage::PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let rows = database.connection.query(
        "INSERT INTO diamond_git.lfs_locks(app,binding,repository,id,path,owner) VALUES($1,$2,$3,$4,$5,$6) RETURNING id,path,created::text,owner",
        &[&database.app, &binding, &repository, &id, &path, &actor],
    )?;
    let value = rows
        .first()
        .map(lock_json)
        .transpose()?
        .ok_or(Error::Denied)?;
    database.finish_request(Ok(serde_json::json!({"lock":value})))
}

fn verify_locks(
    server: &Server,
    binding: &str,
    repository: &str,
    actor: &str,
    cursor: Option<&str>,
    limit: u32,
) -> Result<serde_json::Value> {
    let cursor = cursor.unwrap_or("");
    let limit = i64::from(limit);
    let mut database = super::super::storage::PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let rows = database.connection.query(
        "SELECT id,path,created::text,owner FROM diamond_git.lfs_locks WHERE app=$1 AND binding=$2 AND repository=$3 AND id>$4 ORDER BY id LIMIT $5",
        &[&database.app, &binding, &repository, &cursor, &limit],
    )?;
    let mut ours = Vec::new();
    let mut theirs = Vec::new();
    for row in &rows {
        let value = lock_json(row)?;
        if value["owner"]["name"].as_str() == Some(actor) {
            ours.push(value);
        } else {
            theirs.push(value);
        }
    }
    let next_cursor = rows.last().and_then(|row| row.try_get::<_, String>(0).ok());
    database.finish_request(Ok(serde_json::json!({
        "ours":ours,
        "theirs":theirs,
        "next_cursor":next_cursor
    })))
}

fn unlock_lock(
    server: &Server,
    binding: &str,
    repository: &str,
    actor: &str,
    id: &str,
    _force: bool,
) -> Result<serde_json::Value> {
    if !valid_lock_id(id) {
        return Err(Error::Denied);
    }
    let mut database = super::super::storage::PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    // A transport write credential proves repository write access, not lock-administration
    // authority. Until Diamond has a separately checked maintainer capability, `force`
    // remains owner-bounded instead of letting any writer delete another user's lock.
    let rows = database.connection.query(
        "DELETE FROM diamond_git.lfs_locks WHERE app=$1 AND binding=$2 AND repository=$3 AND id=$4 AND owner=$5 RETURNING id,path,created::text,owner",
        &[&database.app, &binding, &repository, &id, &actor],
    )?;
    let value = rows
        .first()
        .map(lock_json)
        .transpose()?
        .ok_or(Error::Denied)?;
    database.finish_request(Ok(serde_json::json!({"lock":value})))
}

fn lock_json(row: &tokio_postgres::Row) -> Result<serde_json::Value> {
    Ok(serde_json::json!({
        "id":row.try_get::<_, String>(0).map_err(|_| Error::Denied)?,
        "path":row.try_get::<_, String>(1).map_err(|_| Error::Denied)?,
        "locked_at":row.try_get::<_, String>(2).map_err(|_| Error::Denied)?,
        "owner":{"name":row.try_get::<_, String>(3).map_err(|_| Error::Denied)?}
    }))
}

fn route(request: &Request) -> Result<Route> {
    let path = request
        .uri()
        .path()
        .strip_prefix("/_diamond/git/")
        .ok_or(Error::Denied)?;
    let (binding, rest) = path.split_once('/').ok_or(Error::Denied)?;
    let (repository, suffix) = rest.split_once(".git/info/lfs/").ok_or(Error::Denied)?;
    if !super::super::resolve::identifier(binding) || !crate::auth::valid_id(repository) {
        return Err(Error::Denied);
    }
    let action = match (request.method().as_str(), suffix) {
        ("POST", "objects/batch") => Action::Batch,
        ("PUT" | "GET", object) if object.starts_with("objects/") => {
            let oid = object.strip_prefix("objects/").ok_or(Error::Denied)?;
            if !valid_oid(oid) {
                return Err(Error::Denied);
            }
            Action::Object(oid.into())
        }
        ("POST", "locks") => Action::Locks,
        ("POST", "locks/verify") => Action::VerifyLocks,
        ("POST", value) if value.starts_with("locks/") && value.ends_with("/unlock") => {
            let id = value
                .strip_prefix("locks/")
                .and_then(|value| value.strip_suffix("/unlock"))
                .ok_or(Error::Denied)?;
            if !valid_lock_id(id) {
                return Err(Error::Denied);
            }
            Action::Unlock(id.into())
        }
        _ => return Err(Error::Denied),
    };
    Ok(Route {
        binding: binding.into(),
        repository: repository.into(),
        action,
    })
}

fn require_json(request: &Request) -> Result<()> {
    if !matches!(
        one_header(request, header::CONTENT_TYPE.as_str()),
        Some("application/vnd.git-lfs+json" | "application/json")
    ) {
        return Err(Error::Denied);
    }
    Ok(())
}

fn valid_oid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_lock_id(value: &str) -> bool {
    valid_oid(value)
}

fn valid_ref_name(value: &str) -> bool {
    value.len() <= 255
        && value.starts_with("refs/")
        && !value.contains("..")
        && !value
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte == b' ' || byte == b'\\')
}

fn valid_lock_path(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 1024
        && !value.starts_with('/')
        && !value.contains("..")
        && !value.contains('\\')
        && !value.bytes().any(|byte| byte.is_ascii_control())
}

fn hex(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        let _written = write!(output, "{byte:02x}");
    }
    output
}

fn lfs_json<T: Serialize>(status: StatusCode, value: &T) -> Result<Response> {
    let body = serde_json::to_vec(value).map_err(|_| Error::Denied)?;
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/vnd.git-lfs+json")
        .header(header::CACHE_CONTROL, "no-store")
        .header("x-content-type-options", "nosniff")
        .body(Body::from(body))
        .map_err(|_| Error::Denied)
}

fn empty_response(status: StatusCode) -> Result<Response> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_LENGTH, "0")
        .header(header::CACHE_CONTROL, "no-store")
        .body(Body::empty())
        .map_err(|_| Error::Denied)
}

fn failure(error: &Error) -> Response {
    let (status, message) = match error {
        Error::Unauthenticated => (StatusCode::UNAUTHORIZED, "Authentication required"),
        Error::Denied => (StatusCode::FORBIDDEN, "Access denied"),
        Error::Conflict | Error::Aborted => (StatusCode::CONFLICT, "Conflict"),
        Error::Input(_) => (StatusCode::BAD_REQUEST, "Invalid request"),
        Error::Limit(_) => (StatusCode::PAYLOAD_TOO_LARGE, "Limit exceeded"),
        _ => (StatusCode::SERVICE_UNAVAILABLE, "Service unavailable"),
    };
    let mut response = lfs_json(status, &serde_json::json!({"message":message}))
        .unwrap_or_else(|_| Response::new(Body::empty()));
    if status == StatusCode::UNAUTHORIZED {
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            header::HeaderValue::from_static("Basic realm=\"diamond-lfs\", charset=\"UTF-8\""),
        );
    }
    response
}
