//! A bounded loopback-only development runtime for checked applications.

use std::collections::BTreeMap;
use std::net::{SocketAddr, TcpListener};
use std::path::Path;
use std::time::Duration;

use crate::authority::{Effects, Principal, Scope};
use crate::browser_auth::{BrowserAuth, BrowserSession};
use crate::data::Database;
use crate::error::{Error, Result};
use crate::eval::{Interpreter, Request};
use crate::hir::{CheckedProgram, Route, Segment};
use crate::http::{self, HttpRequest, Response};
use crate::value::{Type, Value, ValueKind};

pub(crate) fn serve(
    program: &CheckedProgram,
    address: SocketAddr,
    database_path: Option<&Path>,
    account_path: Option<&Path>,
    maximum_requests: Option<usize>,
) -> Result<()> {
    if !address.ip().is_loopback() {
        return Err(Error::unsupported(
            crate::Location::default(),
            "non-loopback or production serving; TLS, deployment validation, and concurrent serving are not implemented",
        ));
    }
    if program.routes.is_empty() {
        return Err(Error::Input("program has no routes".into()));
    }
    if database_path.is_some() && !program.app.as_ref().is_some_and(|app| app.database) {
        return Err(Error::Input(
            "a database path requires a SQLite database declaration".into(),
        ));
    }
    if account_path.is_some() && !program.app.as_ref().is_some_and(|app| app.sessions) {
        return Err(Error::Input(
            "--auth requires a session SecureCookie declaration".into(),
        ));
    }
    if database_path.is_none() && program.routes.iter().any(|route| !route.effects.is_empty()) {
        return Err(Error::Input(
            "database-backed routes require an explicit --database PATH host grant".into(),
        ));
    }
    if let (Some(database), Some(accounts)) = (database_path, account_path) {
        distinct_databases(database, accounts)?;
    }
    let listener = TcpListener::bind(address)?;
    let address = listener.local_addr()?;
    let browser = account_path.map(BrowserAuth::open).transpose()?;
    let database = database_path
        .map(|path| Database::open(path, program))
        .transpose()?;
    let grants = if database.is_some() {
        program.grants.clone()
    } else {
        Effects::new()
    };
    let mut runtime = Runtime {
        program,
        database,
        grants,
        browser,
    };
    eprintln!(
        "Diamond checkpoint-six development server: http://localhost:{} (http://{address})",
        address.port()
    );
    for stream in listener
        .incoming()
        .take(maximum_requests.unwrap_or(usize::MAX))
    {
        let mut stream = stream?;
        let request = http::read_request(&mut stream, address);
        let head = request
            .as_ref()
            .is_ok_and(|request| request.method == "HEAD");
        let result = request.and_then(|request| runtime.dispatch(&request));
        let response = result.unwrap_or_else(|error| http::failure(&error));
        stream.set_write_timeout(Some(Duration::from_secs(5)))?;
        if let Err(error) = http::write_response(&mut stream, &response, head) {
            eprintln!("response transport failed: {}", error.kind());
        }
    }
    Ok(())
}

struct Runtime<'a> {
    program: &'a CheckedProgram,
    database: Option<Database>,
    grants: Effects,
    browser: Option<BrowserAuth>,
}

impl Runtime<'_> {
    fn dispatch(&mut self, request: &HttpRequest) -> Result<Response> {
        if request.path == "/_diamond" || request.path.starts_with("/_diamond/") {
            return match &mut self.browser {
                Some(browser) => browser.handle(request),
                None => Ok(Response::text(404, "Not found")),
            };
        }
        let Some((route, mut locals)) = find_route(self.program, request)? else {
            return Ok(Response::text(404, "Not found"));
        };
        let mut session = self
            .browser
            .as_mut()
            .map(|browser| browser.session(request, false))
            .transpose()?
            .flatten();
        if !route.public
            && session
                .as_ref()
                .and_then(|session| session.principal.id())
                .is_none()
        {
            return Ok(if self.browser.is_some() {
                Response::html(401, "<!doctype html><main><h1>Authentication required</h1><a href=\"/_diamond/login\">Sign in</a></main>".into())
            } else {
                Response::text(401, "Authentication required")
            });
        }
        if let Some(browser) = &mut self.browser {
            if ["GET", "HEAD"].contains(&request.method.as_str()) {
                session = browser.session(request, true)?;
            } else {
                browser.verify_mutation(request)?;
            }
        }
        let result = self.execute(route, request, &mut locals, session.as_ref());
        let mut response = result.unwrap_or_else(|error| http::failure(&error));
        if let Some(cookie) = session
            .as_ref()
            .and_then(|session| session.new_cookie.as_ref())
        {
            response.cookies.push(cookie.clone());
        }
        Ok(response)
    }

    fn execute(
        &mut self,
        route: &Route,
        request: &HttpRequest,
        locals: &mut BTreeMap<String, Value>,
        session: Option<&BrowserSession>,
    ) -> Result<Response> {
        let anonymous = Principal::Anonymous;
        let principal = session.map_or(&anonymous, |session| &session.principal);
        let scope = Scope::new(principal, &self.grants);
        for effect in &route.effects {
            scope.require(effect)?;
        }
        let context = Request {
            body: &request.body,
            content_type: request.headers.get("content-type").map(String::as_str),
            csrf: session.map(|session| session.csrf.as_str()),
        };
        let mut interpreter =
            Interpreter::new(self.program, &scope, self.database.as_mut(), Some(context));
        let value = interpreter.block(&route.body, locals)?;
        let response = match value.0 {
            ValueKind::Html(body) => Response::html(200, body),
            ValueKind::Response(status, body) => Response::html(status, body),
            ValueKind::Url(url) => Response::redirect(&url),
            _ => Response::text(200, value.display_text()?),
        };
        Ok(response)
    }
}

fn find_route<'a>(
    program: &'a CheckedProgram,
    request: &HttpRequest,
) -> Result<Option<(&'a Route, BTreeMap<String, Value>)>> {
    for method in [request.method.as_str(), "GET"] {
        for route in program.routes.iter().filter(|route| route.method == method) {
            if let Some(locals) = parameters(&route.segments, &request.path)? {
                return Ok(Some((route, locals)));
            }
        }
        if request.method != "HEAD" {
            break;
        }
    }
    Ok(None)
}

fn parameters(pattern: &[Segment], path: &str) -> Result<Option<BTreeMap<String, Value>>> {
    let values: Vec<_> = path.trim_start_matches('/').split('/').collect();
    if values.len() != pattern.len()
        || pattern.iter().zip(&values).any(
            |(segment, value)| matches!(segment, Segment::Literal(expected) if expected != value),
        )
    {
        return Ok(None);
    }
    let mut parameters = BTreeMap::new();
    for (segment, value) in pattern.iter().zip(values) {
        if let Segment::Parameter(name, ty) = segment {
            let value = match ty {
                Type::Int => {
                    Value(ValueKind::Int(value.parse().map_err(|_| {
                        Error::Input("invalid Int path parameter".into())
                    })?))
                }
                Type::Id(tag) if crate::auth::valid_id(value) => {
                    Value(ValueKind::Id(tag.clone(), value.into()))
                }
                _ => return Err(Error::Input("invalid typed path parameter".into())),
            };
            parameters.insert(name.clone(), value);
        }
    }
    Ok(Some(parameters))
}

fn distinct_databases(database: &Path, accounts: &Path) -> Result<()> {
    let accounts = accounts.canonicalize()?;
    let database = if database.exists() {
        database.canonicalize()?
    } else {
        let parent = database
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        parent.canonicalize()?.join(
            database
                .file_name()
                .ok_or_else(|| Error::Input("invalid database path".into()))?,
        )
    };
    if accounts == database {
        return Err(Error::Input(
            "account and application databases must be separate files".into(),
        ));
    }
    Ok(())
}
