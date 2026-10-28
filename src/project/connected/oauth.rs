//! Google `OpenID` Connect and GitHub OAuth login with one-use PKCE transactions.

use super::{
    Arc, Error, Request, Response, Result, Server, StatusCode, cookie, failure, header, input,
    json, json_response, one_header, session_cookie, to_bytes,
};
use crate::project::{
    CheckedProject,
    accounts::{OAuthAdmission, OAuthIdentity, OAuthProvider},
    storage::PostgresStore,
};
use base64::Engine as _;
use futures_util::StreamExt as _;
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header, jwk::JwkSet};
use serde::{Deserialize, Serialize};
use sha2::Digest as _;
use std::{collections::BTreeSet, time::Duration};
use subtle::ConstantTimeEq as _;
use zeroize::Zeroizing;

const MAX_PROVIDER_RESPONSE: usize = 65_536;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Start {
    invite: String,
}

#[derive(Deserialize)]
struct GoogleToken {
    id_token: String,
}

#[derive(Deserialize)]
struct GitHubToken {
    access_token: String,
    token_type: String,
}

#[derive(Deserialize)]
struct GitHubUser {
    id: u64,
}

#[derive(Deserialize)]
struct GitHubEmail {
    email: String,
    verified: bool,
    primary: bool,
}

#[derive(Deserialize)]
struct GitHubMembership {
    state: String,
}

#[derive(Deserialize, Serialize)]
struct GoogleClaims {
    #[serde(rename = "iss")]
    issuer: String,
    sub: String,
    aud: Audience,
    exp: u64,
    iat: u64,
    nonce: String,
    email: String,
    email_verified: bool,
    hd: Option<String>,
    azp: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(untagged)]
enum Audience {
    One(String),
    Many(Vec<String>),
}

struct Evidence {
    identity: OAuthIdentity,
    tenant: Option<String>,
    organizations: BTreeSet<String>,
}

pub(super) fn validate_runtime(
    project: &CheckedProject,
    runtime: &crate::RuntimeGrants,
) -> Result<()> {
    let configured = project
        .manifest
        .identity
        .as_ref()
        .and_then(|identity| identity.oauth.as_ref());
    match (configured, runtime.oauth.as_ref()) {
        (None, None) => Ok(()),
        (Some(config), Some(grant)) => {
            let granted = [
                grant.google.as_ref().map(|_| OAuthProvider::Google),
                grant.github.as_ref().map(|_| OAuthProvider::Github),
            ]
            .into_iter()
            .flatten()
            .collect::<BTreeSet<_>>();
            if granted == config.providers {
                Ok(())
            } else {
                Err(input("OAuth runtime providers do not match the manifest"))
            }
        }
        _ => Err(input(
            "OAuth manifest and runtime configuration must be present together",
        )),
    }
}

pub(super) async fn request(
    server: Arc<Server>,
    request: Request,
    peer: String,
    permit: crate::project::admission::Permit,
) -> Response {
    let _permit = permit;
    match request_inner(server, request, peer).await {
        Ok(response) => response,
        Err(error) => failure(&error),
    }
}

async fn request_inner(server: Arc<Server>, request: Request, peer: String) -> Result<Response> {
    if request.headers().contains_key(header::AUTHORIZATION) {
        return Err(Error::Denied);
    }
    let path = request
        .uri()
        .path()
        .strip_prefix("/_diamond/oauth/")
        .ok_or(Error::Denied)?;
    let (provider_name, action) = path.split_once('/').ok_or(Error::Denied)?;
    let provider = OAuthProvider::parse(provider_name).ok_or(Error::Denied)?;
    let identity = server
        .project
        .manifest
        .identity
        .as_ref()
        .and_then(|identity| identity.oauth.as_ref())
        .ok_or(Error::Denied)?;
    if !identity.providers.contains(&provider) {
        return Err(Error::Denied);
    }
    let oauth = server.runtime.oauth.as_ref().ok_or(Error::Denied)?;
    let client = oauth.client(provider_name).ok_or(Error::Denied)?;
    match action {
        "start" => start(Arc::clone(&server), request, peer, provider, client, oauth).await,
        "callback" => callback(Arc::clone(&server), request, peer, provider, client, oauth).await,
        _ => Err(Error::Denied),
    }
}

async fn start(
    server: Arc<Server>,
    request: Request,
    peer: String,
    provider: OAuthProvider,
    client: &crate::integrations::OAuthClient,
    oauth: &crate::integrations::OAuthRuntime,
) -> Result<Response> {
    if request.method() != "POST"
        || request.uri().query().is_some()
        || one_header(&request, header::ORIGIN.as_str()) != Some(server.origin.as_str())
        || one_header(&request, header::CONTENT_TYPE.as_str()) != Some("application/json")
    {
        return Err(Error::Denied);
    }
    let csrf = one_header(&request, "x-diamond-csrf")
        .ok_or(Error::Denied)?
        .to_owned();
    let token = cookie(&request, &server.cookie)?.ok_or(Error::Unauthenticated)?;
    let body = tokio::time::timeout_at(
        crate::budget::until(Duration::from_secs(3)),
        to_bytes(request.into_body(), 1024),
    )
    .await
    .map_err(|_| Error::Limit("OAuth start body"))?
    .map_err(|_| Error::Limit("OAuth start body"))?;
    let body: Start = super::super::wire::strict_from_slice(&body)
        .map_err(|_| input("invalid OAuth start request"))?;
    let work_server = Arc::clone(&server);
    let work_provider = provider.name().to_owned();
    let key = Zeroizing::new(*oauth.transaction_key);
    let work_token = token.clone();
    let (state, secrets) = crate::project::admission::blocking(move || {
        super::super::accounts::charge(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
            "oauth",
            &peer,
            &work_provider,
        )?;
        let mut store = PostgresStore::open_control(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
        )?;
        let principal = store.principal(Some(&work_token), &csrf)?;
        if principal.id().is_some() {
            return Err(Error::Denied);
        }
        let result = store.begin_oauth(&work_token, &work_provider, &body.invite, &key);
        store.finish_request(result)
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))??;

    let redirect = callback_url(&server, provider);
    let challenge = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .encode(sha2::Sha256::digest(secrets.verifier.as_bytes()));
    let mut authorization = match provider {
        OAuthProvider::Google => url::Url::parse("https://accounts.google.com/o/oauth2/v2/auth"),
        OAuthProvider::Github => url::Url::parse("https://github.com/login/oauth/authorize"),
    }
    .map_err(|_| Error::Denied)?;
    let scope = match provider {
        OAuthProvider::Google => "openid email",
        OAuthProvider::Github => {
            let organizations = &server
                .project
                .manifest
                .identity
                .as_ref()
                .and_then(|identity| identity.oauth.as_ref())
                .ok_or(Error::Denied)?
                .github_organizations;
            if organizations.is_empty() {
                "user:email"
            } else {
                "user:email read:org"
            }
        }
    };
    authorization
        .query_pairs_mut()
        .append_pair("client_id", &client.client_id)
        .append_pair("redirect_uri", &redirect)
        .append_pair("response_type", "code")
        .append_pair("scope", scope)
        .append_pair("state", &state)
        .append_pair("code_challenge", &challenge)
        .append_pair("code_challenge_method", "S256");
    if provider == OAuthProvider::Google {
        authorization
            .query_pairs_mut()
            .append_pair("nonce", &secrets.nonce);
    }
    let mut response = json_response(
        StatusCode::OK,
        &json!({"authorization_url":authorization.as_str()}),
    );
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
    );
    Ok(response)
}

async fn callback(
    server: Arc<Server>,
    request: Request,
    peer: String,
    provider: OAuthProvider,
    client: &crate::integrations::OAuthClient,
    oauth: &crate::integrations::OAuthRuntime,
) -> Result<Response> {
    if request.method() != "GET"
        || request.headers().contains_key(header::ORIGIN)
        || request.uri().query().is_none_or(|query| query.len() > 4096)
    {
        return Err(Error::Denied);
    }
    let token = cookie(&request, &server.cookie)?.ok_or(Error::Unauthenticated)?;
    let query = callback_query(request.uri().query().ok_or(Error::Denied)?, provider)?;
    let secrets = consume_transaction(
        Arc::clone(&server),
        &token,
        provider,
        &query.state,
        oauth,
        peer,
    )
    .await?;
    if query.error {
        return Err(Error::Denied);
    }
    let code = query.code.ok_or(Error::Denied)?;
    let evidence = match provider {
        OAuthProvider::Google => {
            google(client, &callback_url(&server, provider), &code, &secrets).await?
        }
        OAuthProvider::Github => {
            github(
                client,
                &callback_url(&server, provider),
                &code,
                &secrets,
                &server
                    .project
                    .manifest
                    .identity
                    .as_ref()
                    .and_then(|identity| identity.oauth.as_ref())
                    .ok_or(Error::Denied)?
                    .github_organizations,
            )
            .await?
        }
    };
    let config = server
        .project
        .manifest
        .identity
        .as_ref()
        .and_then(|identity| identity.oauth.as_ref())
        .ok_or(Error::Denied)?;
    let admitted = admitted(config, &evidence);
    let allow_existing_when_unadmitted = config.admission == OAuthAdmission::Closed;
    let require_invite = config.admission == OAuthAdmission::Invite;
    let work_server = Arc::clone(&server);
    let work_token = token.clone();
    let invite = secrets.invite.clone();
    let session = crate::project::admission::blocking(move || {
        let mut store = PostgresStore::open_control(
            &work_server.project,
            &work_server.secrets.configuration,
            work_server.pool.as_ref(),
        )?;
        let result = store.finish_oauth(
            &work_token,
            &evidence.identity,
            admitted,
            allow_existing_when_unadmitted,
            require_invite,
            &invite,
        );
        store.finish_request(result)
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))??;
    let mut response = Response::builder()
        .status(StatusCode::SEE_OTHER)
        .header(header::LOCATION, "/")
        .header(header::CACHE_CONTROL, "no-store")
        .header(
            header::SET_COOKIE,
            session_cookie(&server.cookie, &session.token, false),
        )
        .body(axum::body::Body::empty())
        .map_err(|_| Error::Denied)?;
    response.headers_mut().insert(
        "referrer-policy",
        header::HeaderValue::from_static("no-referrer"),
    );
    Ok(response)
}

async fn consume_transaction(
    server: Arc<Server>,
    token: &str,
    provider: OAuthProvider,
    state: &str,
    oauth: &crate::integrations::OAuthRuntime,
    peer: String,
) -> Result<super::super::accounts::OAuthSecrets> {
    let provider = provider.name().to_owned();
    let token = Zeroizing::new(token.to_owned());
    let state = state.to_owned();
    let key = Zeroizing::new(*oauth.transaction_key);
    crate::project::admission::blocking(move || {
        super::super::accounts::charge(
            &server.project,
            &server.secrets.configuration,
            server.pool.as_ref(),
            "oauth",
            &peer,
            &provider,
        )?;
        let mut store = PostgresStore::open_control(
            &server.project,
            &server.secrets.configuration,
            server.pool.as_ref(),
        )?;
        let result = store.take_oauth(&token, &provider, &state, &key);
        store.finish_request(result)
    })
    .await
    .map_err(|_| Error::OutcomeUnknown(String::new()))?
}

struct CallbackQuery {
    state: String,
    code: Option<Zeroizing<String>>,
    error: bool,
}

fn callback_query(query: &str, provider: OAuthProvider) -> Result<CallbackQuery> {
    let mut state = None;
    let mut code = None;
    let mut error = false;
    let mut seen = BTreeSet::new();
    for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
        if !seen.insert(key.to_string()) || value.len() > 2048 {
            return Err(Error::Denied);
        }
        match key.as_ref() {
            "state" => state = Some(value.into_owned()),
            "code" => code = Some(Zeroizing::new(value.into_owned())),
            "error" => error = true,
            "error_description" | "error_uri" | "scope" | "authuser" | "prompt" | "hd" => {}
            "iss"
                if provider == OAuthProvider::Google
                    && matches!(
                        value.as_ref(),
                        "https://accounts.google.com" | "accounts.google.com"
                    ) => {}
            _ => return Err(Error::Denied),
        }
    }
    let state = state.ok_or(Error::Denied)?;
    if !valid_hex_secret(&state) || error == code.is_some() {
        return Err(Error::Denied);
    }
    Ok(CallbackQuery { state, code, error })
}

async fn google(
    client: &crate::integrations::OAuthClient,
    redirect: &str,
    code: &str,
    secrets: &super::super::accounts::OAuthSecrets,
) -> Result<Evidence> {
    let http = provider_client()?;
    let body = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("code", code)
        .append_pair("client_id", &client.client_id)
        .append_pair("client_secret", &client.client_secret)
        .append_pair("redirect_uri", redirect)
        .append_pair("grant_type", "authorization_code")
        .append_pair("code_verifier", &secrets.verifier)
        .finish();
    let token: GoogleToken = provider_json(
        http.post("https://oauth2.googleapis.com/token")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(body),
    )
    .await?;
    let id_token = Zeroizing::new(token.id_token);
    if id_token.len() > 16_384 {
        return Err(provider_failure());
    }
    let keys: JwkSet =
        provider_json(http.get("https://www.googleapis.com/oauth2/v3/certs")).await?;
    if keys.keys.len() > 16 {
        return Err(provider_failure());
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| Error::Denied)?
        .as_secs();
    verify_google_token(&id_token, &keys, &client.client_id, &secrets.nonce, now)
}

fn verify_google_token(
    token: &str,
    keys: &JwkSet,
    client_id: &str,
    nonce: &str,
    now: u64,
) -> Result<Evidence> {
    let header = decode_header(token).map_err(|_| Error::Denied)?;
    if header.alg != Algorithm::RS256 || header.typ.as_deref().is_some_and(|value| value != "JWT") {
        return Err(Error::Denied);
    }
    let kid = header.kid.as_deref().ok_or(Error::Denied)?;
    let key = keys.find(kid).ok_or(Error::Denied)?;
    let decoding = DecodingKey::from_jwk(key).map_err(|_| Error::Denied)?;
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_audience(&[client_id]);
    validation.set_issuer(&["https://accounts.google.com", "accounts.google.com"]);
    validation.set_required_spec_claims(&["exp", "iss", "aud", "sub"]);
    // The caller supplies the clock so verification remains deterministic and
    // all time checks use the same instant. The production caller gets that
    // instant from `SystemTime` immediately before verification.
    validation.validate_exp = false;
    let claims = decode::<GoogleClaims>(token, &decoding, &validation)
        .map_err(|_| Error::Denied)?
        .claims;
    if !claims.email_verified
        || !matches!(
            claims.issuer.as_str(),
            "https://accounts.google.com" | "accounts.google.com"
        )
        || claims.iat > now.saturating_add(60)
        || now.saturating_sub(claims.iat) > 600
        || claims.exp <= now
        || claims.sub.is_empty()
        || claims.sub.len() > 255
        || claims.nonce.as_bytes().ct_eq(nonce.as_bytes()).unwrap_u8() != 1
        || claims
            .azp
            .as_ref()
            .is_some_and(|authorized| authorized != client_id)
        || matches!(&claims.aud, Audience::Many(values) if values.len() > 1 && claims.azp.as_deref() != Some(client_id))
    {
        return Err(Error::Denied);
    }
    match &claims.aud {
        Audience::One(value) if value != client_id => return Err(Error::Denied),
        Audience::Many(values)
            if values.is_empty() || !values.iter().any(|value| value == client_id) =>
        {
            return Err(Error::Denied);
        }
        _ => {}
    }
    Ok(Evidence {
        identity: OAuthIdentity {
            provider: "google".into(),
            subject: claims.sub,
            email: claims.email,
        },
        tenant: claims.hd.map(|value| value.to_ascii_lowercase()),
        organizations: BTreeSet::new(),
    })
}

async fn github(
    client: &crate::integrations::OAuthClient,
    redirect: &str,
    code: &str,
    secrets: &super::super::accounts::OAuthSecrets,
    organizations: &BTreeSet<String>,
) -> Result<Evidence> {
    let http = provider_client()?;
    let body = url::form_urlencoded::Serializer::new(String::new())
        .append_pair("client_id", &client.client_id)
        .append_pair("client_secret", &client.client_secret)
        .append_pair("code", code)
        .append_pair("redirect_uri", redirect)
        .append_pair("code_verifier", &secrets.verifier)
        .finish();
    let token: GitHubToken = provider_json(
        http.post("https://github.com/login/oauth/access_token")
            .header(header::ACCEPT, "application/json")
            .header(header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(body),
    )
    .await?;
    if !token.token_type.eq_ignore_ascii_case("bearer") || token.access_token.len() > 4096 {
        return Err(Error::Denied);
    }
    let access = Zeroizing::new(token.access_token);
    let user: GitHubUser = github_json(&http, "/user", &access).await?;
    let mut emails: Vec<GitHubEmail> = github_json(&http, "/user/emails", &access).await?;
    emails.retain(|email| email.verified);
    emails.sort_by_key(|email| !email.primary);
    let address = emails.first().ok_or(Error::Denied)?.email.clone();
    let mut active = BTreeSet::new();
    for organization in organizations {
        let path = format!("/user/memberships/orgs/{organization}");
        if let Some(membership) = github_optional::<GitHubMembership>(&http, &path, &access).await?
            && membership.state == "active"
        {
            active.insert(organization.clone());
        }
    }
    Ok(Evidence {
        identity: OAuthIdentity {
            provider: "github".into(),
            subject: user.id.to_string(),
            email: address,
        },
        tenant: None,
        organizations: active,
    })
}

fn admitted(config: &super::super::accounts::OAuthConfig, evidence: &Evidence) -> bool {
    match config.admission {
        OAuthAdmission::Public | OAuthAdmission::Invite => true,
        OAuthAdmission::Closed => false,
        OAuthAdmission::Restricted => {
            let domain = evidence
                .identity
                .email
                .rsplit_once('@')
                .map(|(_, domain)| domain.to_ascii_lowercase());
            let identity = format!(
                "{}:{}",
                evidence.identity.provider, evidence.identity.subject
            );
            let mut checks = Vec::new();
            if !config.email_domains.is_empty() {
                checks.push(
                    domain
                        .as_ref()
                        .is_some_and(|value| config.email_domains.contains(value)),
                );
            }
            if !config.google_tenants.is_empty() {
                checks.push(
                    evidence
                        .tenant
                        .as_ref()
                        .is_some_and(|value| config.google_tenants.contains(value)),
                );
            }
            if !config.github_organizations.is_empty() {
                checks.push(
                    config
                        .github_organizations
                        .iter()
                        .any(|value| evidence.organizations.contains(value)),
                );
            }
            if !config.identities.is_empty() {
                checks.push(config.identities.contains(&identity));
            }
            !checks.is_empty()
                && if config.require_all {
                    checks.into_iter().all(|value| value)
                } else {
                    checks.into_iter().any(|value| value)
                }
        }
    }
}

fn callback_url(server: &Server, provider: OAuthProvider) -> String {
    format!(
        "{}/_diamond/oauth/{}/callback",
        server.origin,
        provider.name()
    )
}

fn provider_client() -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .referer(false)
        .https_only(true)
        .http1_only()
        .pool_max_idle_per_host(0)
        .no_gzip()
        .no_brotli()
        .no_deflate()
        .no_zstd()
        .connect_timeout(crate::budget::remaining(Duration::from_secs(5))?)
        .timeout(crate::budget::remaining(Duration::from_secs(10))?)
        .user_agent("Diamond OAuth")
        .build()
        .map_err(|_| provider_failure())
}

async fn provider_json<T: serde::de::DeserializeOwned>(
    request: reqwest::RequestBuilder,
) -> Result<T> {
    let response = request.send().await.map_err(|_| provider_failure())?;
    if !response.status().is_success()
        || response.status().is_redirection()
        || response
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_none_or(|value| value.split(';').next() != Some("application/json"))
    {
        return Err(provider_failure());
    }
    let bytes = Zeroizing::new(bounded_response(response).await?);
    serde_json::from_slice(&bytes).map_err(|_| provider_failure())
}

async fn github_json<T: serde::de::DeserializeOwned>(
    client: &reqwest::Client,
    path: &str,
    token: &str,
) -> Result<T> {
    github_optional(client, path, token)
        .await?
        .ok_or_else(provider_failure)
}

async fn github_optional<T: serde::de::DeserializeOwned>(
    client: &reqwest::Client,
    path: &str,
    token: &str,
) -> Result<Option<T>> {
    let response = client
        .get(format!("https://api.github.com{path}"))
        .header(header::AUTHORIZATION, format!("Bearer {token}"))
        .header(header::ACCEPT, "application/vnd.github+json")
        .header("x-github-api-version", "2022-11-28")
        .send()
        .await
        .map_err(|_| provider_failure())?;
    if response.status() == StatusCode::NOT_FOUND {
        return Ok(None);
    }
    if !response.status().is_success() {
        return Err(provider_failure());
    }
    let bytes = Zeroizing::new(bounded_response(response).await?);
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| provider_failure())
}

async fn bounded_response(response: reqwest::Response) -> Result<Vec<u8>> {
    if response
        .content_length()
        .is_some_and(|length| length > MAX_PROVIDER_RESPONSE as u64)
    {
        return Err(provider_failure());
    }
    let mut result = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| provider_failure())?;
        if result.len().saturating_add(chunk.len()) > MAX_PROVIDER_RESPONSE {
            return Err(provider_failure());
        }
        result.extend_from_slice(&chunk);
    }
    Ok(result)
}

fn valid_hex_secret(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn provider_failure() -> Error {
    Error::Propagation("OAuth provider unavailable.".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::engine::general_purpose::STANDARD;
    use jsonwebtoken::{EncodingKey, Header, encode};

    const CLIENT: &str = "diamond-oauth-client";
    const NONCE: &str = "0123456789abcdef0123456789abcdef";
    const NOW: u64 = 1_800_000_000;
    const KID: &str = "diamond-test-key";
    const MODULUS: &str = "07FAP0vYXC7SKRHOHaP52syUOMj3KP_5LzoeyB2SELtuW_qlzoQQqLSiP3N4LpoIMocabAL7mdv0Xp3oeoZaKNPxNVZF0m9BMZztFNEFd6JWNE4oq9SgwHedO2_lwa5h0X-uOes2H_XA0XxD0FSDDHxnDqTobAkEaRV5rFP_hogC0V0qTQoRn7c4xgLs8vKSE0jTdwribBPMOtThRhLEhgmh04mMUdf1nL_K1uJigMn2HJuSt-Wrnj-oKXXhTddKTiy4gA9hz3wbRv_Dt1O1dwQJU6VawCzYOdt9n16QOlP5tvZL4IYzf3F9Ffgmmqe9-lsW5tg61ks6U_boNyugtw";
    const PRIVATE_KEY_DER: &str = "MIIEpAIBAAKCAQEA07FAP0vYXC7SKRHOHaP52syUOMj3KP/5LzoeyB2SELtuW/qlzoQQqLSiP3N4LpoIMocabAL7mdv0Xp3oeoZaKNPxNVZF0m9BMZztFNEFd6JWNE4oq9SgwHedO2/lwa5h0X+uOes2H/XA0XxD0FSDDHxnDqTobAkEaRV5rFP/hogC0V0qTQoRn7c4xgLs8vKSE0jTdwribBPMOtThRhLEhgmh04mMUdf1nL/K1uJigMn2HJuSt+Wrnj+oKXXhTddKTiy4gA9hz3wbRv/Dt1O1dwQJU6VawCzYOdt9n16QOlP5tvZL4IYzf3F9Ffgmmqe9+lsW5tg61ks6U/boNyugtwIDAQABAoIBAFNbrDdgd9GQ1Eu8yaONUtEwFrunkv/PkAFjSwS09DPngmTwtYK1KVQtUOhYYT5EhC7VWXen0wg6fycU2hsJNY53Kre8gaEEEzFkoEy+o/8Aax6MmmJTR3uSflUN0nSqZLVYqDQBZV4/aM50tsVdyf7MK86t7CHCiTyR6sgLiYag58wCJkDAmfduLRWRVuo//UOdrQHn+lP+/jYFaSjCXy/0lkdAYol12rQmeHE19uYkXKG8ylAaPVzxz7OygXve+5A+Y+uqerrnqFD+OcvBgfRKqKe0xDGZBHgeSYbZwL/cz/bBzD4w8tQtOzbR3/T2GAQ8huEExrrMC0Xdz4tOjNkCgYEA7kV8P6XspC4EBK2kVZB1MGmHghYbzw/URcqihssp4kXsFx4IbZUPWriDDtPEpXHc77/C7CBKwBkiOX0sFxiJNkr/RPoEWmB00ARB+Q+FsJjj5j7qeP6CfkMkC3KgwozCXVHy0oc8YaEdV7QWYuTHUShzkFzlefyVg3GMmddbO/8CgYEA43F/Nk21JYEOAsfR69Un4YumCqsPo/5yUsUYfDxyv0ySeevI7tNRY9m5rPjALpx8g8FWI1UDBUfEoI99DLFy7TVJ7g3+EdIx1/MIrf3sDLdkUAji5mpQHRfoA0XhfpmHohU/1kyS8dqtc6VB7RGIJbrmR4lfES/JqawCH1ese0kCgYEApJGnlIp0nJ0Pt297ToHc/xgUOPwuLuC++4lOog0Wm6sxunHK3m0S0tUX6BjJ/H9dWtY2ukI/ftidM9nJpxYSR0abcze/4/A1HiuiqXRYITYoSjHwXqti0/Rpo+KDOHxiDnR4Y4J2aPQPwTCoOZvMMrjewLn44T1cwxGdS/Bbml8CgYEAy/LHPtuZlyLNfILP42iFVYOTBbf003pj64KffS/7OT0hj8B1smq9bgsbq1Z3hpR+NutUIWSmy8JaOBvS52Hri3egbp4PeM2CMoHVA6hRVenRQs9lH3avlhcMznqIRKzJafciAjkx/zu3E59ZqYpyCls5CWbOX6v5anM6h/5nRTkCgYAVhwsw6wXURntNaddFpX9Bjs+r0g72zAEzFjhhRjF/jC5zPMvSv4Myg8PPg67BcWbmg+HLhCLCm/m8dEH024pwO+vtC8OdIK2zK+2kmZGIX4XzEGMU3T9Pp11nt9RzzN1xNr1OfcDP41rI/OY1pxIRYujHHE4irAA2I7Q5Sj90dw==";

    fn claims() -> GoogleClaims {
        GoogleClaims {
            issuer: "https://accounts.google.com".into(),
            sub: "google-subject-123".into(),
            aud: Audience::One(CLIENT.into()),
            exp: NOW + 300,
            iat: NOW,
            nonce: NONCE.into(),
            email: "user@example.com".into(),
            email_verified: true,
            hd: Some("Example.COM".into()),
            azp: None,
        }
    }

    fn keys(kid: &str, modulus: &str) -> JwkSet {
        let parsed = serde_json::from_value(serde_json::json!({
            "keys": [{
                "kty": "RSA",
                "kid": kid,
                "alg": "RS256",
                "use": "sig",
                "n": modulus,
                "e": "AQAB"
            }]
        }));
        let Ok(keys) = parsed else {
            panic!("synthetic JWK must be valid");
        };
        keys
    }

    fn token(claims: &GoogleClaims) -> String {
        let mut header = Header::new(Algorithm::RS256);
        header.kid = Some(KID.into());
        let Ok(der) = STANDARD.decode(PRIVATE_KEY_DER) else {
            panic!("synthetic private key must be valid base64");
        };
        let signed = encode(&header, claims, &EncodingKey::from_rsa_der(&der));
        let Ok(signed) = signed else {
            panic!("synthetic token must be signable");
        };
        signed
    }

    fn denied(claims: &GoogleClaims) {
        assert!(
            verify_google_token(&token(claims), &keys(KID, MODULUS), CLIENT, NONCE, NOW).is_err()
        );
    }

    #[test]
    fn google_verifier_accepts_bound_signed_identity() {
        let result =
            verify_google_token(&token(&claims()), &keys(KID, MODULUS), CLIENT, NONCE, NOW);
        let Ok(evidence) = result else {
            panic!("valid signed token must be accepted");
        };
        assert_eq!(evidence.identity.provider, "google");
        assert_eq!(evidence.identity.subject, "google-subject-123");
        assert_eq!(evidence.identity.email, "user@example.com");
        assert_eq!(evidence.tenant.as_deref(), Some("example.com"));
    }

    #[test]
    fn google_verifier_rejects_claim_substitution_and_bad_time() {
        let mut value = claims();
        value.nonce = "attacker-nonce".into();
        denied(&value);

        let mut value = claims();
        value.issuer = "https://attacker.example".into();
        denied(&value);

        let mut value = claims();
        value.aud = Audience::One("another-client".into());
        denied(&value);

        let mut value = claims();
        value.email_verified = false;
        denied(&value);

        let mut value = claims();
        value.exp = NOW;
        denied(&value);

        let mut value = claims();
        value.iat = NOW + 61;
        denied(&value);

        let mut value = claims();
        value.iat = NOW - 601;
        denied(&value);

        let mut value = claims();
        value.sub.clear();
        denied(&value);
    }

    #[test]
    fn google_verifier_enforces_authorized_party_for_multiple_audiences() {
        let mut value = claims();
        value.aud = Audience::Many(vec![CLIENT.into(), "another-client".into()]);
        denied(&value);

        value.azp = Some("another-client".into());
        denied(&value);

        value.azp = Some(CLIENT.into());
        assert!(
            verify_google_token(&token(&value), &keys(KID, MODULUS), CLIENT, NONCE, NOW).is_ok()
        );
    }

    #[test]
    fn google_verifier_rejects_unknown_key_bad_signature_and_algorithm() {
        let signed = token(&claims());
        assert!(
            verify_google_token(&signed, &keys("rotated-key", MODULUS), CLIENT, NONCE, NOW)
                .is_err()
        );

        let mut corrupted = signed.into_bytes();
        assert!(!corrupted.is_empty());
        let index = corrupted.len() - 1;
        corrupted[index] = if corrupted[index] == b'A' { b'B' } else { b'A' };
        let Ok(corrupted) = String::from_utf8(corrupted) else {
            panic!("JWT must remain ASCII");
        };
        assert!(verify_google_token(&corrupted, &keys(KID, MODULUS), CLIENT, NONCE, NOW).is_err());

        let mut header = Header::new(Algorithm::HS256);
        header.kid = Some(KID.into());
        let wrong_algorithm = encode(
            &header,
            &claims(),
            &EncodingKey::from_secret(b"synthetic-test-secret"),
        );
        let Ok(wrong_algorithm) = wrong_algorithm else {
            panic!("synthetic HMAC token must be signable");
        };
        assert!(
            verify_google_token(&wrong_algorithm, &keys(KID, MODULUS), CLIENT, NONCE, NOW).is_err()
        );
    }

    #[test]
    fn admission_rules_fail_closed_and_combine_explicit_evidence() {
        let evidence = Evidence {
            identity: OAuthIdentity {
                provider: "google".into(),
                subject: "subject-1".into(),
                email: "person@example.com".into(),
            },
            tenant: Some("workspace.example".into()),
            organizations: BTreeSet::from(["diamond-team".into()]),
        };
        let mut config = super::super::super::accounts::OAuthConfig {
            providers: BTreeSet::from([OAuthProvider::Google]),
            admission: OAuthAdmission::Restricted,
            require_all: false,
            email_domains: BTreeSet::new(),
            google_tenants: BTreeSet::new(),
            github_organizations: BTreeSet::new(),
            identities: BTreeSet::new(),
        };
        assert!(!admitted(&config, &evidence));

        config.email_domains.insert("example.com".into());
        assert!(admitted(&config, &evidence));
        config.email_domains = BTreeSet::from(["elsewhere.example".into()]);
        assert!(!admitted(&config, &evidence));

        config.google_tenants.insert("workspace.example".into());
        assert!(admitted(&config, &evidence));
        config.require_all = true;
        assert!(!admitted(&config, &evidence));
        config.email_domains = BTreeSet::from(["example.com".into()]);
        assert!(admitted(&config, &evidence));

        config.google_tenants.clear();
        config.github_organizations = BTreeSet::from(["diamond-team".into()]);
        config.identities = BTreeSet::from(["google:subject-1".into()]);
        assert!(admitted(&config, &evidence));
        config.identities = BTreeSet::from(["google:different".into()]);
        assert!(!admitted(&config, &evidence));
    }
}
