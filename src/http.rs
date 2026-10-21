//! Bounded HTTP transport. Sensitive request and response contents are redacted.

use crate::error::{Error, Result};
use std::collections::BTreeMap;
use std::fmt;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};
use zeroize::Zeroize;

const MAX_HEADER_BYTES: usize = 16_384;
const MAX_BODY_BYTES: usize = 65_536;
// Drain a small, bounded oversize tail so ordinary clients receive the 413 at
// the documented boundary. Arbitrarily large or incomplete bodies still stop
// at this cap and the existing request deadline.
const MAX_REJECTED_BODY_DRAIN: usize = MAX_BODY_BYTES + 1_024;

pub(crate) struct HttpRequest {
    pub method: String,
    pub path: String,
    pub headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
}
impl fmt::Debug for HttpRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HttpRequest")
            .field("method", &self.method)
            .field("path", &self.path)
            .field("body_bytes", &self.body.len())
            .finish_non_exhaustive()
    }
}
impl Drop for HttpRequest {
    fn drop(&mut self) {
        self.body.zeroize();
    }
}

pub(crate) struct Response {
    pub status: u16,
    pub content_type: &'static str,
    pub body: String,
    pub location: Option<String>,
    pub cookies: Vec<String>,
}
impl fmt::Debug for Response {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Response")
            .field("status", &self.status)
            .field("body_bytes", &self.body.len())
            .finish_non_exhaustive()
    }
}
impl Response {
    pub fn text(status: u16, body: impl Into<String>) -> Self {
        Self {
            status,
            content_type: "text/plain; charset=utf-8",
            body: body.into(),
            location: None,
            cookies: Vec::new(),
        }
    }
    pub fn html(status: u16, body: String) -> Self {
        Self {
            content_type: "text/html; charset=utf-8",
            ..Self::text(status, body)
        }
    }
    pub fn redirect(location: &str) -> Self {
        Self {
            location: Some(location.into()),
            ..Self::text(303, "See other")
        }
    }
}

pub(crate) fn read_request(stream: &mut TcpStream, address: SocketAddr) -> Result<HttpRequest> {
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut bytes = zeroize::Zeroizing::new(Vec::with_capacity(4096));
    let header_end = loop {
        if let Some(end) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            break end + 4;
        }
        if bytes.len() >= MAX_HEADER_BYTES {
            return Err(Error::Limit("HTTP headers"));
        }
        read_chunk(stream, &mut bytes, deadline, MAX_HEADER_BYTES)?;
    };
    let headers = std::str::from_utf8(&bytes[..header_end])
        .map_err(|_| Error::Input("HTTP headers must be UTF-8 ASCII syntax".into()))?;
    if !headers.is_ascii() {
        return Err(Error::Input("non-ASCII HTTP header syntax".into()));
    }
    let mut lines = headers.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| Error::Input("missing request line".into()))?;
    let fields: Vec<_> = request_line.split(' ').collect();
    if fields.len() != 3 || fields[2] != "HTTP/1.1" {
        return Err(Error::Input("expected HTTP/1.1 origin-form request".into()));
    }
    let method = fields[0].to_string();
    let path = fields[1].to_string();
    if !["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].contains(&method.as_str()) {
        return Err(Error::unsupported(
            crate::Location::default(),
            "HTTP method",
        ));
    }
    if !path.starts_with('/')
        || path.starts_with("//")
        || path.contains(['\\', '#'])
        || path.chars().any(char::is_control)
    {
        return Err(Error::Input("invalid request path".into()));
    }
    if path.contains(['?', '%']) {
        return Err(Error::unsupported(
            crate::Location::default(),
            "query strings and percent-encoded route paths",
        ));
    }
    let map = parse_headers(headers)?;
    let length = match map.get("content-length") {
        Some(value) if !value.is_empty() && value.bytes().all(|c| c.is_ascii_digit()) => value
            .parse::<usize>()
            .map_err(|_| Error::Limit("HTTP body"))?,
        Some(_) => return Err(Error::Input("invalid Content-Length".into())),
        None => 0,
    };
    if length > MAX_BODY_BYTES {
        let target = header_end + length.min(MAX_REJECTED_BODY_DRAIN);
        while bytes.len() < target {
            read_chunk(stream, &mut bytes, deadline, target)?;
        }
        return Err(Error::Limit("HTTP body"));
    }
    if ["GET", "HEAD"].contains(&method.as_str()) && length != 0 {
        return Err(Error::Input(
            "safe-method request body is unsupported".into(),
        ));
    }
    let target = header_end + length;
    while bytes.len() < target {
        read_chunk(stream, &mut bytes, deadline, target)?;
    }
    if bytes.len() != target {
        return Err(Error::Input(
            "pipelined or trailing request data is unsupported".into(),
        ));
    }
    // Consume the bounded, completely framed body before rejecting its Host or
    // Origin. Closing with unread body bytes can reset TCP and erase the error
    // response while a normal client is still sending the form.
    validate_authority(&map, address)?;
    Ok(HttpRequest {
        method,
        path,
        headers: map,
        body: bytes[header_end..].to_vec(),
    })
}

fn parse_headers(headers: &str) -> Result<BTreeMap<String, String>> {
    let lines = headers.split("\r\n").skip(1);
    let mut map = BTreeMap::new();
    for line in lines.filter(|line| !line.is_empty()) {
        let (key, value) = line
            .split_once(':')
            .ok_or_else(|| Error::Input("invalid HTTP header".into()))?;
        if key.is_empty()
            || !key.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
            || value.chars().any(|c| c.is_control() && c != '\t')
        {
            return Err(Error::Input("invalid HTTP header syntax".into()));
        }
        if map
            .insert(key.to_ascii_lowercase(), value.trim().to_string())
            .is_some()
        {
            return Err(Error::Input("duplicate HTTP header".into()));
        }
    }
    if map.contains_key("transfer-encoding")
        || map.contains_key("upgrade")
        || map.contains_key("expect")
    {
        return Err(Error::unsupported(
            crate::Location::default(),
            "chunked requests, protocol upgrades, or Expect",
        ));
    }
    if map
        .keys()
        .any(|name| name == "forwarded" || name.starts_with("x-forwarded-"))
    {
        return Err(Error::Input("trusted proxies are not configured".into()));
    }
    Ok(map)
}

fn validate_authority(map: &BTreeMap<String, String>, address: SocketAddr) -> Result<()> {
    let host = map
        .get("host")
        .ok_or_else(|| Error::Input("Host is required".into()))?;
    if host != &address.to_string() && host != &format!("localhost:{}", address.port()) {
        return Err(Error::Input(
            "Host does not match the loopback listener".into(),
        ));
    }
    if let Some(origin) = map.get("origin")
        && origin != &format!("http://{host}")
    {
        return Err(Error::Denied);
    }
    Ok(())
}

fn read_chunk(
    stream: &mut TcpStream,
    output: &mut Vec<u8>,
    deadline: Instant,
    maximum: usize,
) -> Result<()> {
    let remaining = deadline
        .checked_duration_since(Instant::now())
        .filter(|value| !value.is_zero())
        .ok_or(Error::Limit("HTTP request deadline"))?;
    stream.set_read_timeout(Some(remaining))?;
    let mut buffer = [0_u8; 2048];
    let size = buffer.len().min(maximum.saturating_sub(output.len()));
    if size == 0 {
        return Err(Error::Limit("HTTP input"));
    }
    let received = stream.read(&mut buffer[..size])?;
    if received == 0 {
        return Err(Error::Input("incomplete HTTP request".into()));
    }
    output.extend_from_slice(&buffer[..received]);
    Ok(())
}

pub(crate) fn failure(error: &Error) -> Response {
    if let Error::OutcomeUnknown(operation) = error {
        return Response::text(
            503,
            format!("Operation outcome unknown; operation {operation}; do not automatically retry"),
        );
    }
    let (status, message) = match error {
        Error::Denied => (404, "Not found"),
        Error::Input(_) | Error::Propagation(_) => (400, "Invalid input"),
        Error::Limit(_) => (413, "Resource limit exceeded"),
        Error::Unsupported { .. } => (501, "Unsupported in this prototype"),
        Error::OutcomeUnknown(_) => (503, "Operation outcome unknown; do not automatically retry"),
        Error::Aborted => (409, "Transaction aborted"),
        _ => (500, "Internal error"),
    };
    Response {
        status,
        content_type: "text/plain; charset=utf-8",
        body: message.into(),
        location: None,
        cookies: Vec::new(),
    }
}

pub(crate) fn write_response(
    stream: &mut TcpStream,
    response: &Response,
    head: bool,
) -> std::io::Result<()> {
    let reason = match response.status {
        200 => "OK",
        303 => "See Other",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        429 => "Too Many Requests",
        413 => "Content Too Large",
        501 => "Not Implemented",
        503 => "Service Unavailable",
        _ => "Internal Server Error",
    };
    write!(
        stream,
        "HTTP/1.1 {} {reason}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: same-origin\r\nContent-Security-Policy: default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'\r\n",
        response.status,
        response.content_type,
        response.body.len()
    )?;
    if let Some(location) = &response.location {
        write!(stream, "Location: {location}\r\n")?;
    }
    for cookie in &response.cookies {
        write!(stream, "Set-Cookie: {cookie}\r\n")?;
    }
    stream.write_all(b"\r\n")?;
    if !head {
        stream.write_all(response.body.as_bytes())?;
    }
    stream.flush()
}
