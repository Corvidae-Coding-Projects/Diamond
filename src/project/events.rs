//! Durable typed event delivery with exact-origin authenticated `WebSockets`.
use super::{
    Manifest, Module, Target,
    connected::{self, Server},
    identity, input,
    storage::PostgresStore,
    wire,
};
use crate::{
    authority::{Effect, Scope},
    error::{Error, Result},
    hir::CheckedProgram,
    value::{Type, Value, ValueKind},
};
use axum::{
    extract::{
        FromRequestParts, Request,
        ws::{Message, WebSocket, WebSocketUpgrade},
    },
    response::{IntoResponse, Response},
};
use futures_util::SinkExt as _;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::{Duration, Instant},
};
use zeroize::Zeroizing;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub resource: String,
    pub payload: String,
}
pub(super) fn payload(
    b: &Binding,
    modules: &BTreeMap<String, Module>,
    m: &Manifest,
    p: &CheckedProgram,
) -> Result<Type> {
    if b.payload == "Text" {
        return Ok(Type::Text);
    }
    let (module, name) = b.payload.split_once('.').unwrap_or((&m.server, &b.payload));
    let module = modules.get(module).ok_or(Error::Denied)?;
    if module.config.target == Target::Browser {
        return Err(Error::Denied);
    }
    let name = module
        .symbols
        .get(name)
        .cloned()
        .unwrap_or_else(|| name.into());
    let ty = if p.records.contains_key(&name) {
        Type::Record(name)
    } else if p.enums.contains_key(&name) {
        Type::Enum(name)
    } else {
        return Err(input(
            "event payload requires Text or a checked shared record/enum",
        ));
    };
    if !wire::safe_type(&ty, p) {
        return Err(Error::Denied);
    }
    Ok(ty)
}
pub(super) fn checked(
    m: &Manifest,
    modules: &BTreeMap<String, Module>,
    p: &CheckedProgram,
) -> Result<()> {
    for (n, b) in &m.events {
        if !super::resolve::identifier(n) || !p.resources.contains_key(&b.resource) {
            return Err(input("invalid event binding or parent resource"));
        }
        payload(b, modules, m, p)?;
    }
    Ok(())
}
impl PostgresStore {
    pub(super) fn emit(
        &mut self,
        scope: &Scope<'_>,
        binding: &str,
        parent: &str,
        value: &Value,
    ) -> Result<Value> {
        let b = self.events.get(binding).cloned().ok_or(Error::Denied)?;
        self.attachment(scope, &b.resource, parent, true)?;
        wire::decode(
            self.event_types.get(binding).ok_or(Error::Denied)?,
            &wire::encode(value)?,
            &self.program,
        )?;
        let encoded = wire::encode(value)?.to_string();
        if encoded.len() > 16384 {
            return Err(Error::Limit("event payload"));
        }
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,8))",
            &[&self.app],
        )?;
        let rows=self.connection.query("INSERT INTO diamond_w7.events(app,binding,parent,payload) VALUES($1,$2,$3,$4) RETURNING cursor", &[&self.app,&binding,&parent,&encoded])?;
        let cursor: i64 = rows[0].try_get(0).map_err(|_| Error::Denied)?;
        self.connection.query("DELETE FROM diamond_w7.events WHERE app=$1 AND cursor IN (SELECT cursor FROM diamond_w7.events WHERE app=$1 ORDER BY cursor DESC OFFSET 10000 LIMIT 32)", &[&self.app])?;
        Ok(Value(ValueKind::Text(cursor.to_string())))
    }
}
fn read(
    server: &Server,
    token: &str,
    binding: &str,
    parent: &str,
    cursor: i64,
) -> Result<Vec<serde_json::Value>> {
    let mut db = PostgresStore::open_control(
        &server.project,
        &server.secrets.configuration,
        server.pool.as_ref(),
    )?;
    let principal = db.principal(Some(token), &identity::csrf(&db.app, token))?;
    if principal.id().is_none() {
        return Err(Error::Unauthenticated);
    }
    let b = server
        .project
        .manifest
        .events
        .get(binding)
        .ok_or(Error::Denied)?;
    let effects = BTreeSet::from([Effect::Read(b.resource.clone())]);
    db.attachment(
        &Scope::new(&principal, &effects),
        &b.resource,
        parent,
        false,
    )?;
    let bounds=db.connection.query("SELECT COALESCE(MIN(cursor),0),COALESCE(MAX(cursor),0) FROM diamond_w7.events WHERE app=$1", &[&db.app])?;
    let minimum: i64 = bounds[0].try_get(0).map_err(|_| Error::Denied)?;
    let maximum: i64 = bounds[0].try_get(1).map_err(|_| Error::Denied)?;
    if cursor > maximum || cursor > 0 && cursor < minimum {
        return db.finish_request(Ok(vec![
            serde_json::json!({"resync":true,"cursor":maximum.to_string()}),
        ]));
    }
    let rows=db.connection.query("SELECT cursor,payload FROM diamond_w7.events WHERE app=$1 AND binding=$2 AND parent=$3 AND cursor>$4 ORDER BY cursor LIMIT 32", &[&db.app,&binding,&parent,&cursor])?;
    let ty = payload(
        b,
        &server.project.modules,
        &server.project.manifest,
        &server.project.server,
    )?;
    let values = rows
        .iter()
        .map(|r| -> Result<_> {
            let cursor: i64 = r.try_get(0).map_err(|_| Error::Denied)?;
            let body: String = r.try_get(1).map_err(|_| Error::Denied)?;
            let value: serde_json::Value =
                serde_json::from_str(&body).map_err(|_| Error::Denied)?;
            wire::decode(&ty, &value, &server.project.server)?;
            Ok(serde_json::json!({"cursor":cursor.to_string(),"value":value}))
        })
        .collect::<Result<Vec<_>>>()?;
    db.finish_request(Ok(values))
}
pub(super) async fn request(
    server: Arc<Server>,
    request: Request,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> Response {
    if request.method() != "GET"
        || connected::one_header(&request, "origin") != Some(server.origin.as_str())
        || request.headers().contains_key("authorization")
    {
        return connected::failure(&Error::Denied);
    }
    let parts = request
        .uri()
        .path()
        .trim_start_matches("/_diamond/events/")
        .split('/')
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if parts.len() != 3
        || !server.project.manifest.events.contains_key(&parts[0])
        || parts[1].is_empty()
        || parts[1].len() > 128
        || !parts[1]
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    {
        return connected::failure(&Error::Denied);
    }
    let Ok(cursor) = parts[2].parse::<i64>() else {
        return connected::failure(&Error::Denied);
    };
    if cursor < 0 || parts[2] != cursor.to_string() {
        return connected::failure(&Error::Denied);
    }
    let Ok(Some(token)) = connected::cookie(&request, &server.cookie) else {
        return connected::failure(&Error::Unauthenticated);
    };
    let (mut request_parts, _body) = request.into_parts();
    let Ok(ws) = WebSocketUpgrade::from_request_parts(&mut request_parts, &()).await else {
        return connected::failure(&Error::Denied);
    };
    let check_server = Arc::clone(&server);
    let check_token = token.clone();
    let check_parts = parts.clone();
    let check = tokio::task::spawn_blocking(move || {
        read(
            &check_server,
            &check_token,
            &check_parts[0],
            &check_parts[1],
            cursor,
        )
    })
    .await;
    if !matches!(check, Ok(Ok(_))) {
        return connected::failure(&Error::Denied);
    }
    ws.max_message_size(1024)
        .max_frame_size(1024)
        .on_upgrade(move |socket| stream(socket, server, token, parts, cursor, permit))
        .into_response()
}
async fn stream(
    mut socket: WebSocket,
    server: Arc<Server>,
    token: Zeroizing<String>,
    parts: Vec<String>,
    mut cursor: i64,
    _permit: tokio::sync::OwnedSemaphorePermit,
) {
    let expires = Instant::now() + Duration::from_mins(30);
    while Instant::now() < expires && !server.draining.load(std::sync::atomic::Ordering::Relaxed) {
        let authorized_before = Instant::now();
        let p = parts.clone();
        let s = Arc::clone(&server);
        let t = token.clone();
        let read = tokio::task::spawn_blocking(move || read(&s, &t, &p[0], &p[1], cursor));
        let Ok(Ok(batch)) = read.await else {
            break;
        };
        for event in batch {
            if authorized_before.elapsed() >= Duration::from_secs(3) {
                break;
            }
            let resync = event.get("resync").is_some();
            if let Some(n) = event
                .get("cursor")
                .and_then(|n| n.as_str())
                .and_then(|n| n.parse().ok())
            {
                cursor = n;
            }
            if !matches!(
                tokio::time::timeout(
                    Duration::from_secs(2),
                    socket.send(Message::Text(event.to_string().into()))
                )
                .await,
                Ok(Ok(()))
            ) {
                return;
            }
            if resync {
                let _closed = socket.close().await;
                return;
            }
        }
        tokio::select! { ()=tokio::time::sleep(Duration::from_millis(500))=>{}, received=socket.recv()=>{match received {Some(Ok(Message::Ping(data)))=>{if !matches!(tokio::time::timeout(Duration::from_secs(1),socket.send(Message::Pong(data))).await,Ok(Ok(()))){break;}},_=>break}} }
    }
    let _closed = tokio::time::timeout(Duration::from_secs(1), socket.close()).await;
}
