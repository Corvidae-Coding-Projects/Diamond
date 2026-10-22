//! Closed account route table shared by the HTTP and checked browser boundaries.
use super::{Delivery, Restrictions};
use crate::{
    authority::Principal,
    error::{Error, Result},
    project::{CheckedProject, input, storage::PostgresStore, wire},
    value::Type,
};
use serde::Deserialize;
use serde_json::{Value as Json, json};
use zeroize::Zeroize;
pub(in crate::project) fn route(path: &str) -> bool {
    path.strip_prefix("/_diamond/accounts/")
        .is_some_and(|name| signature(name).is_some())
}
pub(in crate::project) fn signature(name: &str) -> Option<Vec<Type>> {
    let text = Type::Text;
    Some(match name {
        "register" => vec![text.clone(), text.clone(), text.clone(), text],
        "verify" | "resend" | "recover" | "invite" | "revoke_token" => vec![text],
        "reset" | "change_password" => vec![text.clone(), text],
        "disable" => vec![text, Type::Bool],
        "logout_all" | "list_tokens" => vec![],
        "create_token" => vec![
            text.clone(),
            Type::List(Box::new(text.clone())),
            text,
            Type::Int,
        ],
        _ => return None,
    })
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Register {
    username: String,
    email: String,
    password: String,
    invite: String,
}
impl Drop for Register {
    fn drop(&mut self) {
        self.password.zeroize();
        self.invite.zeroize();
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Token {
    token: String,
}
impl Drop for Token {
    fn drop(&mut self) {
        self.token.zeroize();
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Email {
    email: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reset {
    token: String,
    password: String,
}
impl Drop for Reset {
    fn drop(&mut self) {
        self.token.zeroize();
        self.password.zeroize();
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Change {
    current_password: String,
    password: String,
}
impl Drop for Change {
    fn drop(&mut self) {
        self.current_password.zeroize();
        self.password.zeroize();
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Disable {
    username: String,
    disabled: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Create {
    label: String,
    operations: Vec<String>,
    scopes: Restrictions,
    lifetime_seconds: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Revoke {
    id: String,
}
fn parse<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T> {
    wire::strict_from_slice(body).map_err(|_| input("invalid account request shape"))
}
fn empty(body: &[u8]) -> Result<()> {
    let v: std::collections::BTreeMap<String, Json> = parse(body)?;
    if !v.is_empty() {
        return Err(input("account operation has no arguments"));
    }
    Ok(())
}
fn user(principal: &Principal) -> Result<&str> {
    principal.id().ok_or(Error::Unauthenticated)
}
fn admin(principal: &Principal) -> Result<()> {
    if !principal.has_role("account_admin") {
        return Err(Error::Denied);
    }
    Ok(())
}
pub(in crate::project) struct Response {
    pub value: Json,
    pub delivery: Option<Delivery>,
    pub clear: bool,
}
pub(in crate::project) fn dispatch(
    store: &mut PostgresStore,
    project: &CheckedProject,
    path: &str,
    principal: &Principal,
    body: &[u8],
) -> Result<Response> {
    let name = path
        .strip_prefix("/_diamond/accounts/")
        .ok_or(Error::Denied)?;
    let mut delivery = None;
    let mut clear = false;
    let mut value = json!({"value":""});
    match name {
        "register" => {
            let r: Register = parse(body)?;
            delivery = store.register(&r.username, &r.email, &r.password, &r.invite)?;
            value = accepted();
        }
        "resend" | "recover" => {
            let r: Email = parse(body)?;
            delivery = store.request_challenge(
                &r.email,
                if name == "resend" {
                    "verify"
                } else {
                    "recover"
                },
            )?;
            value = accepted();
        }
        "verify" => {
            let r: Token = parse(body)?;
            store.redeem(&r.token, "verify", None)?;
        }
        "reset" => {
            let r: Reset = parse(body)?;
            store.redeem(&r.token, "recover", Some(&r.password))?;
            clear = true;
        }
        "change_password" => {
            let r: Change = parse(body)?;
            store.change_password(user(principal)?, &r.current_password, &r.password)?;
            clear = true;
        }
        "logout_all" => {
            empty(body)?;
            store.logout_all(user(principal)?)?;
            clear = true;
        }
        "invite" => {
            admin(principal)?;
            let r: Email = parse(body)?;
            delivery = Some(store.invite_account(&r.email)?);
            value = accepted();
        }
        "disable" => {
            admin(principal)?;
            let r: Disable = parse(body)?;
            store.set_disabled(&r.username, r.disabled)?;
            clear = principal.id() == Some(r.username.as_str());
        }
        "create_token" => {
            let r: Create = parse(body)?;
            let (token, id) = store.create_credential(
                user(principal)?,
                &r.label,
                &r.operations,
                &r.scopes,
                &r.lifetime_seconds,
                &project.api,
            )?;
            value = json!({"value":token.as_str(),"id":id});
        }
        "list_tokens" => {
            empty(body)?;
            value = json!({"value":store.list_credentials(user(principal)?)?});
        }
        "revoke_token" => {
            let r: Revoke = parse(body)?;
            store.revoke_credential(user(principal)?, &r.id)?;
        }
        _ => return Err(Error::Denied),
    }
    Ok(Response {
        value,
        delivery,
        clear,
    })
}
fn accepted() -> Json {
    json!({"value":"If eligible, instructions have been delivered."})
}
/// Limit subject keys do not include passwords or raw one-use credentials.
pub(in crate::project) fn subject(path: &str, body: &[u8]) -> String {
    let Ok(value) = wire::strict_from_slice::<Json>(body) else {
        return String::new();
    };
    if path == "/_diamond/login" {
        return value
            .get("username")
            .and_then(Json::as_str)
            .unwrap_or_default()
            .to_owned();
    }
    value
        .get("email")
        .and_then(Json::as_str)
        .unwrap_or_default()
        .to_ascii_lowercase()
}
