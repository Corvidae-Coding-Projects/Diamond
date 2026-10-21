//! Runtime-owned browser authentication and CSRF handling.

use std::fmt;
use std::path::Path;
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

use crate::accounts::LocalAccounts;
use crate::auth::SessionStore;
use crate::authority::Principal;
use crate::error::{Error, Result};
use crate::forms;
use crate::http::{HttpRequest, Response};

const LOGIN: &str = "/_diamond/login";
const LOGOUT: &str = "/_diamond/logout";
const SESSION_LIFETIME: Duration = Duration::from_hours(8);

pub(crate) struct BrowserSession {
    pub principal: Principal,
    pub csrf: String,
    pub new_cookie: Option<String>,
}

impl fmt::Debug for BrowserSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("BrowserSession")
            .field("principal", &self.principal)
            .finish_non_exhaustive()
    }
}

#[derive(Debug)]
pub(crate) struct BrowserAuth {
    accounts: LocalAccounts,
    sessions: SessionStore,
    window: Instant,
    attempts: usize,
}

impl BrowserAuth {
    pub fn open(path: &Path) -> Result<Self> {
        Ok(Self {
            accounts: LocalAccounts::open(path)?,
            sessions: SessionStore::default(),
            window: Instant::now(),
            attempts: 0,
        })
    }

    pub fn session(
        &mut self,
        request: &HttpRequest,
        create: bool,
    ) -> Result<Option<BrowserSession>> {
        if let Some(token) = session_cookie(request)?
            && let Some(principal) = self.sessions.principal(token)
        {
            return Ok(Some(BrowserSession {
                principal: principal.clone(),
                csrf: self.sessions.csrf(token).ok_or(Error::Denied)?.into(),
                new_cookie: None,
            }));
        }
        if !create {
            return Ok(None);
        }
        let (token, csrf) = self.sessions.issue_anonymous()?;
        Ok(Some(BrowserSession {
            principal: Principal::Anonymous,
            csrf,
            new_cookie: Some(cookie(token.cookie_value(), Duration::from_mins(5))),
        }))
    }

    pub fn verify_mutation(&self, request: &HttpRequest) -> Result<()> {
        let Some(token) = session_cookie(request)? else {
            return Ok(());
        };
        if !self.sessions.is_valid(token) {
            return Err(Error::Denied);
        }
        let fields = if forms::is_form(request.headers.get("content-type").map(String::as_str)) {
            forms::fields(&request.body)?
        } else {
            std::collections::BTreeMap::new()
        };
        self.check_csrf(request, &fields)
    }

    pub fn handle(&mut self, request: &HttpRequest) -> Result<Response> {
        match (request.method.as_str(), request.path.as_str()) {
            ("GET" | "HEAD", LOGIN) => {
                let session = self.session(request, true)?.ok_or(Error::Denied)?;
                Ok(login_page(200, &session, false))
            }
            ("POST", LOGIN) => self.login(request),
            ("POST", LOGOUT) => self.logout(request),
            (_, LOGIN | LOGOUT) => Ok(Response::text(405, "Method not allowed")),
            _ => Ok(Response::text(404, "Not found")),
        }
    }

    fn check_csrf(
        &self,
        request: &HttpRequest,
        fields: &std::collections::BTreeMap<String, String>,
    ) -> Result<()> {
        let token = session_cookie(request)?.ok_or(Error::Denied)?;
        let header = request.headers.get("x-diamond-csrf").map(String::as_str);
        let field = fields.get("_diamond_csrf").map(String::as_str);
        if header.zip(field).is_some_and(|(a, b)| a != b) {
            return Err(Error::Denied);
        }
        let csrf = header.or(field).ok_or(Error::Denied)?;
        if !self.sessions.check_csrf(token, csrf) {
            return Err(Error::Denied);
        }
        Ok(())
    }

    fn login(&mut self, request: &HttpRequest) -> Result<Response> {
        if !forms::is_form(request.headers.get("content-type").map(String::as_str)) {
            return Err(Error::Input("login requires a URL-encoded form".into()));
        }
        let mut fields = forms::fields(&request.body)?;
        self.check_csrf(request, &fields)?;
        let previous = session_cookie(request)?.ok_or(Error::Denied)?;
        let password = Zeroizing::new(
            fields
                .remove("password")
                .ok_or_else(|| Error::Input("missing login credentials".into()))?,
        );
        let username = fields
            .remove("username")
            .ok_or_else(|| Error::Input("missing login credentials".into()))?;
        fields.remove("_diamond_csrf");
        if !fields.is_empty() {
            return Err(Error::Input("unknown login field".into()));
        }
        if self.window.elapsed() >= Duration::from_mins(1) {
            self.window = Instant::now();
            self.attempts = 0;
        }
        if self.attempts >= 64 {
            return Ok(Response::text(429, "Too many login attempts; retry later"));
        }
        self.attempts += 1;
        if !self.accounts.verify(&username, &password)? {
            let session = self.session(request, false)?.ok_or(Error::Denied)?;
            return Ok(login_page(401, &session, true));
        }
        let (token, _) = self
            .sessions
            .rotate(previous, &username, &[], SESSION_LIFETIME)?;
        let mut response = Response::redirect("/");
        response
            .cookies
            .push(cookie(token.cookie_value(), SESSION_LIFETIME));
        Ok(response)
    }

    fn logout(&mut self, request: &HttpRequest) -> Result<Response> {
        let token = session_cookie(request)?.ok_or(Error::Denied)?;
        if self
            .sessions
            .principal(token)
            .and_then(Principal::id)
            .is_none()
        {
            return Err(Error::Denied);
        }
        let fields = if forms::is_form(request.headers.get("content-type").map(String::as_str)) {
            forms::fields(&request.body)?
        } else {
            std::collections::BTreeMap::new()
        };
        if fields.keys().any(|name| name != "_diamond_csrf") {
            return Err(Error::Input("unknown logout field".into()));
        }
        self.check_csrf(request, &fields)?;
        self.sessions.revoke(token);
        let mut response = Response::redirect("/");
        response.cookies.push(cookie("", Duration::ZERO));
        Ok(response)
    }
}

pub(crate) fn session_cookie(request: &HttpRequest) -> Result<Option<&str>> {
    let mut found = None;
    if let Some(cookies) = request.headers.get("cookie") {
        for cookie in cookies.split(';') {
            if let Some((name, value)) = cookie.trim().split_once('=')
                && name == "diamond_session"
                && found.replace(value).is_some()
            {
                return Err(Error::Input("duplicate session cookies".into()));
            }
        }
    }
    Ok(found)
}

fn cookie(value: &str, lifetime: Duration) -> String {
    format!(
        "diamond_session={value}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={}",
        lifetime.as_secs()
    )
}

fn login_page(status: u16, session: &BrowserSession, failed: bool) -> Response {
    let message = if failed {
        "<p>Invalid credentials.</p>"
    } else {
        ""
    };
    let body = format!(
        "<!doctype html><html><head><title>Sign in — Diamond</title></head><body><main><h1>Sign in</h1>{message}<form method=\"POST\" action=\"{LOGIN}\">{}<p><label for=\"username\">Username</label><input id=\"username\" name=\"username\" autocomplete=\"username\" maxlength=\"128\" required></p><p><label for=\"password\">Password</label><input id=\"password\" name=\"password\" type=\"password\" autocomplete=\"current-password\" required></p><button type=\"submit\">Sign in</button></form></main></body></html>",
        forms::csrf_hidden(&session.csrf)
    );
    let mut response = Response::html(status, body);
    if let Some(cookie) = &session.new_cookie {
        response.cookies.push(cookie.clone());
    }
    response
}
