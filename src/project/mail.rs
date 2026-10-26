//! Manifest-bound, transactional plain-text mail outbox.

use super::{CheckedProject, Manifest, artifact, input, storage::PostgresStore};
use crate::{
    Error,
    authority::{Effect, Effects, Principal, Restrictions, Scope, random_hex},
    error::Result,
    hir::CheckedProgram,
    integrations::MailGrant,
    value::{Value, ValueKind},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{ErrorKind, Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use zeroize::Zeroizing;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Config {
    profile: String,
    pub(super) bindings: BTreeMap<String, Binding>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Binding {
    pub(super) resource: String,
    #[serde(default = "attempts")]
    max_attempts: i32,
    pub(super) templates: BTreeMap<String, Template>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Template {
    subject: String,
}

const fn attempts() -> i32 {
    3
}

impl Config {
    pub(super) fn check(&self, manifest: &Manifest, program: &CheckedProgram) -> Result<()> {
        if self.profile != "mail-v1"
            || manifest.identity.is_none()
            || program.storage.is_none()
            || self.bindings.is_empty()
            || self.bindings.len() > 16
            || self
                .bindings
                .values()
                .map(|binding| binding.templates.len())
                .sum::<usize>()
                > 128
        {
            return Err(input(
                "mail requires mail-v1, accounts, relational storage, and bounded bindings",
            ));
        }
        for (name, binding) in &self.bindings {
            if !super::resolve::identifier(name)
                || !program.resources.contains_key(&binding.resource)
                || !(1..=8).contains(&binding.max_attempts)
                || binding.templates.is_empty()
                || binding.templates.len() > 32
                || !manifest.provides.contains(&format!("Mail<{name}>"))
                || !manifest
                    .provides
                    .contains(&format!("DbRead<{}>", binding.resource))
                || !manifest
                    .provides
                    .contains(&format!("DbWrite<{}>", binding.resource))
            {
                return Err(input(
                    "invalid mail binding or absent mail/resource capabilities",
                ));
            }
            for (template, definition) in &binding.templates {
                if !super::resolve::identifier(template)
                    || !(1..=160).contains(&definition.subject.len())
                    || definition.subject.chars().any(char::is_control)
                {
                    return Err(input("invalid plain-text mail template"));
                }
            }
        }
        Ok(())
    }
}

const SCHEMA: &str = "CREATE SCHEMA IF NOT EXISTS diamond_mail;
CREATE TABLE IF NOT EXISTS diamond_mail.apps(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app), shape TEXT NOT NULL, paused BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS diamond_mail.stores(app TEXT NOT NULL REFERENCES diamond_mail.apps(app), binding TEXT NOT NULL, identity TEXT NOT NULL, PRIMARY KEY(app,binding), UNIQUE(identity));
CREATE TABLE IF NOT EXISTS diamond_mail.outbox(app TEXT NOT NULL REFERENCES diamond_mail.apps(app), id TEXT NOT NULL CHECK(id ~ '^[0-9a-f]{64}$'), binding TEXT NOT NULL, version TEXT NOT NULL, parent TEXT NOT NULL, owner TEXT NOT NULL, recipient TEXT NOT NULL, template TEXT NOT NULL, body TEXT NOT NULL CHECK(octet_length(body)<=16384), dedup TEXT NOT NULL CHECK(octet_length(dedup)<=160), restrictions TEXT NOT NULL, unscoped BOOLEAN NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','running','delivered','dead','cancelled','suppressed')), attempts INTEGER NOT NULL DEFAULT 0, fence BIGINT NOT NULL DEFAULT 0, lease TIMESTAMPTZ, due TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), outcome TEXT NOT NULL DEFAULT '', PRIMARY KEY(app,id), UNIQUE(app,binding,owner,dedup));
CREATE INDEX IF NOT EXISTS mail_due ON diamond_mail.outbox(app,due) WHERE state IN ('pending','running');
CREATE TABLE IF NOT EXISTS diamond_mail.suppressions(app TEXT NOT NULL REFERENCES diamond_mail.apps(app), binding TEXT NOT NULL, recipient TEXT NOT NULL, PRIMARY KEY(app,binding,recipient));";

pub(super) fn schema(store: &mut PostgresStore, config: &Config, initialize: bool) -> Result<()> {
    let shape = artifact::hash(&serde_json::to_vec(config).map_err(|_| Error::Denied)?);
    if initialize {
        store.connection.batch(SCHEMA)?;
        store.connection.query(
            "INSERT INTO diamond_mail.apps(app,shape) VALUES($1,$2) ON CONFLICT DO NOTHING",
            &[&store.app, &shape],
        )?;
    }
    let rows = store.connection.query(
        "SELECT shape FROM diamond_mail.apps WHERE app=$1",
        &[&store.app],
    )?;
    if rows
        .first()
        .map(|row| row.try_get::<_, String>(0))
        .transpose()
        .map_err(|_| Error::Denied)?
        != Some(shape)
    {
        return Err(input(
            "incompatible mail schema; stop instances and initialize the current profile",
        ));
    }
    Ok(())
}

fn version(project: &CheckedProject) -> Result<String> {
    Ok(artifact::hash(
        &serde_json::to_vec(&project.manifest.mail).map_err(|_| Error::Denied)?,
    ))
}

pub(crate) fn address(value: &str) -> Result<String> {
    if value.len() > 254
        || !value.is_ascii()
        || value
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return Err(input("invalid mail address"));
    }
    let (local, domain) = value
        .split_once('@')
        .ok_or_else(|| input("invalid mail address"))?;
    if local.is_empty()
        || local.len() > 64
        || domain.len() > 189
        || !domain.contains('.')
        || !local
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b".!#$%&'*+-/=?^_`{|}~".contains(&byte))
        || !domain.split('.').all(|part| {
            !part.is_empty()
                && !part.starts_with('-')
                && !part.ends_with('-')
                && part
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        })
    {
        return Err(input("invalid mail address"));
    }
    Ok(value.to_ascii_lowercase())
}

fn text(value: Option<&Value>) -> Result<&str> {
    match value {
        Some(Value(ValueKind::Text(value))) => Ok(value),
        _ => Err(Error::Denied),
    }
}

fn boolean(value: Option<&Value>) -> Result<bool> {
    match value {
        Some(Value(ValueKind::Bool(value))) => Ok(*value),
        _ => Err(Error::Denied),
    }
}

impl PostgresStore {
    pub(crate) fn mail_call(
        &mut self,
        scope: &Scope<'_>,
        method: &str,
        values: &[Value],
    ) -> Result<Value> {
        let binding = text(values.first())?;
        scope.require(&Effect::Host(format!("Mail<{binding}>")))?;
        let config = self
            .mail
            .as_ref()
            .and_then(|config| config.bindings.get(binding))
            .cloned()
            .ok_or(Error::Denied)?;
        if method == "mail_suppress" {
            return self.suppress_mail(scope, binding, boolean(values.get(1))?);
        }
        let id_or_parent = text(values.get(1))?;
        if method == "mail_submit" {
            return self.submit_mail(scope, binding, &config, id_or_parent, values);
        }
        self.mail_control(scope, method, binding, &config, id_or_parent)
    }

    fn suppress_mail(&mut self, scope: &Scope<'_>, binding: &str, value: bool) -> Result<Value> {
        let recipient = scope.principal().id().ok_or(Error::Unauthenticated)?;
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,23))",
            &[&format!("{}:{binding}:{recipient}", self.app)],
        )?;
        if value {
            self.connection.query("INSERT INTO diamond_mail.suppressions(app,binding,recipient) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", &[&self.app, &binding, &recipient])?;
        } else {
            self.connection.query("DELETE FROM diamond_mail.suppressions WHERE app=$1 AND binding=$2 AND recipient=$3", &[&self.app, &binding, &recipient])?;
        }
        Ok(Value(ValueKind::Text(if value {
            "suppressed".into()
        } else {
            "enabled".into()
        })))
    }

    fn submit_mail(
        &mut self,
        scope: &Scope<'_>,
        binding: &str,
        config: &Binding,
        parent: &str,
        values: &[Value],
    ) -> Result<Value> {
        self.attachment(scope, &config.resource, parent, true)?;
        let owner = scope.principal().id().ok_or(Error::Unauthenticated)?;
        let recipient = text(values.get(2))?;
        let template = text(values.get(3))?;
        let body = text(values.get(4))?;
        let dedup = text(values.get(5))?;
        if !crate::auth::valid_id(recipient)
            || !config.templates.contains_key(template)
            || body.len() > 16_384
            || body.contains('\0')
            || body
                .chars()
                .any(|character| character.is_control() && !matches!(character, '\n' | '\t'))
            || !(1..=160).contains(&dedup.len())
        {
            return Err(input("invalid mail submission"));
        }
        self.authorize_recipient(&config.resource, parent, recipient)?;
        let restrictions =
            serde_json::to_string(&scope.restrictions()).map_err(|_| Error::Denied)?;
        let current_version =
            artifact::hash(&serde_json::to_vec(&self.mail).map_err(|_| Error::Denied)?);
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,17))",
            &[&self.app],
        )?;
        let existing=self.connection.query("SELECT id,parent,recipient,template,body,version,restrictions,unscoped FROM diamond_mail.outbox WHERE app=$1 AND binding=$2 AND owner=$3 AND dedup=$4", &[&self.app,&binding,&owner,&dedup])?;
        if let Some(row) = existing.first() {
            let same = row.try_get::<_, String>(1).map_err(|_| Error::Denied)? == parent
                && row.try_get::<_, String>(2).map_err(|_| Error::Denied)? == recipient
                && row.try_get::<_, String>(3).map_err(|_| Error::Denied)? == template
                && row.try_get::<_, String>(4).map_err(|_| Error::Denied)? == body
                && row.try_get::<_, String>(5).map_err(|_| Error::Denied)? == current_version
                && row.try_get::<_, String>(6).map_err(|_| Error::Denied)? == restrictions
                && row.try_get::<_, bool>(7).map_err(|_| Error::Denied)? == scope.allows_unscoped();
            if !same {
                return Err(Error::Conflict);
            }
            return Ok(Value(ValueKind::Text(
                row.try_get(0).map_err(|_| Error::Denied)?,
            )));
        }
        let count = self.connection.query(
            "SELECT COUNT(*) FROM diamond_mail.outbox WHERE app=$1",
            &[&self.app],
        )?[0]
            .try_get::<_, i64>(0)
            .map_err(|_| Error::Denied)?;
        if count >= 10_000 {
            return Err(Error::Limit("mail outbox quota"));
        }
        let id = random_hex()?;
        self.connection.query("INSERT INTO diamond_mail.outbox(app,id,binding,version,parent,owner,recipient,template,body,dedup,restrictions,unscoped,state) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'pending')", &[&self.app,&id,&binding,&current_version,&parent,&owner,&recipient,&template,&body,&dedup,&restrictions,&scope.allows_unscoped()])?;
        Ok(Value(ValueKind::Text(id)))
    }

    fn authorize_recipient(&mut self, resource: &str, parent: &str, recipient: &str) -> Result<()> {
        let users = self.connection.query("SELECT username FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled", &[&self.app, &recipient])?;
        if users.is_empty() {
            return Err(Error::Denied);
        }
        let principal = Principal::User {
            id: recipient.into(),
            roles: BTreeSet::new(),
        };
        let grants = Effects::from([Effect::Read(resource.into())]);
        self.attachment(&Scope::new(&principal, &grants), resource, parent, false)
    }

    fn mail_control(
        &mut self,
        scope: &Scope<'_>,
        method: &str,
        binding: &str,
        config: &Binding,
        id: &str,
    ) -> Result<Value> {
        let owner = scope.principal().id().ok_or(Error::Unauthenticated)?;
        let rows=self.connection.query("SELECT parent,owner FROM diamond_mail.outbox WHERE app=$1 AND id=$2 AND binding=$3 FOR UPDATE", &[&self.app,&id,&binding])?;
        let row = rows.first().ok_or(Error::Denied)?;
        let parent: String = row.try_get(0).map_err(|_| Error::Denied)?;
        if row.try_get::<_, String>(1).map_err(|_| Error::Denied)? != owner {
            return Err(Error::Denied);
        }
        self.attachment(scope, &config.resource, &parent, method == "mail_cancel")?;
        if method == "mail_cancel" {
            self.connection.query("UPDATE diamond_mail.outbox SET state='cancelled',fence=fence+1,outcome='cancelled' WHERE app=$1 AND id=$2 AND state IN ('pending','running')", &[&self.app,&id])?;
        } else if method != "mail_status" {
            return Err(Error::Denied);
        }
        let rows = self.connection.query(
            "SELECT state,attempts,outcome FROM diamond_mail.outbox WHERE app=$1 AND id=$2",
            &[&self.app, &id],
        )?;
        let state: String = rows[0].try_get(0).map_err(|_| Error::Denied)?;
        let attempts: i32 = rows[0].try_get(1).map_err(|_| Error::Denied)?;
        let outcome: String = rows[0].try_get(2).map_err(|_| Error::Denied)?;
        Ok(Value(ValueKind::Text(
            serde_json::json!({"state":state,"attempts":attempts,"outcome":outcome}).to_string(),
        )))
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Marker {
    profile: String,
    app: String,
    binding: String,
    identity: String,
}

struct Store {
    root: PathBuf,
    from: String,
    marker: Marker,
}

impl Store {
    fn open(project: &CheckedProject, binding: &str, grant: &MailGrant) -> Result<Self> {
        private_directory(&grant.root)?;
        let mut file = file_options()
            .read(true)
            .open(grant.root.join(".diamond-mail-store"))?;
        let mut source = String::new();
        Read::by_ref(&mut file)
            .take(4097)
            .read_to_string(&mut source)?;
        if source.len() > 4096 {
            return Err(Error::Limit("mail marker"));
        }
        let marker: Marker = super::wire::strict_from_slice(source.as_bytes())
            .map_err(|_| input("invalid mail store marker"))?;
        if marker.profile != "mail-store-v1"
            || marker.app != artifact::hash(project.manifest.name.as_bytes())
            || marker.binding != binding
            || !hex_id(&marker.identity)
        {
            return Err(input(
                "mail store belongs to another application or binding",
            ));
        }
        Ok(Self {
            root: grant.root.clone(),
            from: address(&grant.from)?,
            marker,
        })
    }

    fn deliver(&self, claim: &Claim, recipient: &str, subject: &str) -> Result<()> {
        self.verify()?;
        let message = serde_json::to_vec(&serde_json::json!({
            "profile":"diamond-mail-v1",
            "id":claim.id,
            "from":self.from,
            "recipient":recipient,
            "subject":subject,
            "text":claim.body,
            "template":claim.template
        }))
        .map_err(|_| Error::Denied)?;
        if message.len() > 20_000 || fs::read_dir(&self.root)?.take(20_001).count() >= 20_000 {
            return Err(Error::Limit("mail delivery store"));
        }
        let destination = self.root.join(format!("{}.json", claim.id));
        if destination.exists() {
            return verify_message(&destination, &message);
        }
        let temporary = self.root.join(format!(
            ".{}.{}.{}.tmp",
            claim.id,
            claim.fence,
            random_hex()?
        ));
        let mut file = file_options()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        let result = (|| -> Result<()> {
            file.write_all(&message)?;
            file.sync_all()?;
            match fs::hard_link(&temporary, &destination) {
                Ok(()) => {}
                Err(error) if error.kind() == ErrorKind::AlreadyExists => {
                    verify_message(&destination, &message)?;
                }
                Err(error) => return Err(error.into()),
            }
            File::open(&self.root)?.sync_all()?;
            Ok(())
        })();
        let _cleanup = fs::remove_file(&temporary);
        result
    }

    fn verify(&self) -> Result<()> {
        private_directory(&self.root)?;
        let reopened = file_options()
            .read(true)
            .open(self.root.join(".diamond-mail-store"))?;
        let metadata = reopened.metadata()?;
        if !metadata.is_file() {
            return Err(Error::Denied);
        }
        let mut source = String::new();
        reopened.take(4097).read_to_string(&mut source)?;
        let marker: Marker =
            super::wire::strict_from_slice(source.as_bytes()).map_err(|_| Error::Denied)?;
        if marker.identity != self.marker.identity
            || marker.app != self.marker.app
            || marker.binding != self.marker.binding
        {
            return Err(input("mail store identity changed"));
        }
        Ok(())
    }
}

fn verify_message(path: &Path, expected: &[u8]) -> Result<()> {
    let mut bytes = Vec::new();
    file_options()
        .read(true)
        .open(path)?
        .take(20_001)
        .read_to_end(&mut bytes)?;
    if bytes == expected {
        Ok(())
    } else {
        Err(Error::Conflict)
    }
}

fn private_directory(path: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() || path.canonicalize()? != path {
        return Err(input("mail store must be a canonical private directory"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o077 != 0 {
            return Err(input("mail store directory must have mode 0700"));
        }
    }
    Ok(())
}

fn file_options() -> OpenOptions {
    let mut options = OpenOptions::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC);
    }
    options
}

fn hex_id(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(super) fn initialize(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
) -> Result<String> {
    let config = project.manifest.mail.as_ref().ok_or(Error::Denied)?;
    if runtime.mail.keys().collect::<BTreeSet<_>>()
        != config.bindings.keys().collect::<BTreeSet<_>>()
    {
        return Err(Error::Denied);
    }
    let app = artifact::hash(project.manifest.name.as_bytes());
    let mut db = PostgresStore::open(project, configuration, false)?;
    for (binding, grant) in &runtime.mail {
        private_directory(&grant.root)?;
        let marker_path = grant.root.join(".diamond-mail-store");
        if !marker_path.exists() {
            let marker = Marker {
                profile: "mail-store-v1".into(),
                app: app.clone(),
                binding: binding.clone(),
                identity: random_hex()?,
            };
            let bytes = serde_json::to_vec(&marker).map_err(|_| Error::Denied)?;
            let mut file = file_options()
                .write(true)
                .create_new(true)
                .open(&marker_path)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            File::open(&grant.root)?.sync_all()?;
        }
        let store = Store::open(project, binding, grant)?;
        db.connection.query("INSERT INTO diamond_mail.stores(app,binding,identity) VALUES($1,$2,$3) ON CONFLICT(app,binding) DO UPDATE SET identity=EXCLUDED.identity WHERE diamond_mail.stores.identity=EXCLUDED.identity", &[&db.app,&binding,&store.marker.identity])?;
        let rows = db.connection.query(
            "SELECT identity FROM diamond_mail.stores WHERE app=$1 AND binding=$2",
            &[&db.app, &binding],
        )?;
        if rows
            .first()
            .and_then(|row| row.try_get::<_, String>(0).ok())
            .as_deref()
            != Some(store.marker.identity.as_str())
        {
            return Err(input("mail store identity mismatch"));
        }
    }
    db.finish_request(Ok(()))?;
    Ok(serde_json::json!({"initialized":true,"bindings":config.bindings.len()}).to_string())
}

pub(super) fn validate(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
) -> Result<()> {
    let Some(config) = &project.manifest.mail else {
        return if runtime.mail.is_empty() {
            Ok(())
        } else {
            Err(Error::Denied)
        };
    };
    if runtime.mail.keys().collect::<BTreeSet<_>>()
        != config.bindings.keys().collect::<BTreeSet<_>>()
    {
        return Err(Error::Denied);
    }
    let mut db = PostgresStore::open_control(project, configuration, None)?;
    for (binding, grant) in &runtime.mail {
        let store = Store::open(project, binding, grant)?;
        let rows = db.connection.query(
            "SELECT identity FROM diamond_mail.stores WHERE app=$1 AND binding=$2",
            &[&db.app, &binding],
        )?;
        if rows
            .first()
            .and_then(|row| row.try_get::<_, String>(0).ok())
            .as_deref()
            != Some(store.marker.identity.as_str())
        {
            return Err(input("mail store identity mismatch"));
        }
    }
    db.finish_request(Ok(()))
}

#[derive(Debug)]
struct Claim {
    id: String,
    binding: String,
    parent: String,
    owner: String,
    recipient: String,
    template: String,
    body: String,
    restrictions: String,
    unscoped: bool,
    fence: i64,
    attempts: i32,
}

fn claim(project: &CheckedProject, configuration: &str) -> Result<Option<Claim>> {
    let mut db = PostgresStore::open_control(project, configuration, None)?;
    let current_version = version(project)?;
    for (binding, config) in &project
        .manifest
        .mail
        .as_ref()
        .ok_or(Error::Denied)?
        .bindings
    {
        db.connection.query("UPDATE diamond_mail.outbox SET state='dead',outcome='attempts_exhausted',fence=fence+1 WHERE app=$1 AND binding=$2 AND state='running' AND lease<clock_timestamp() AND attempts >= $3", &[&db.app,&binding,&config.max_attempts])?;
    }
    let rows=db.connection.query("WITH candidate AS (SELECT o.id FROM diamond_mail.outbox o JOIN diamond_mail.apps a ON o.app=a.app WHERE o.app=$1 AND o.version=$2 AND NOT a.paused AND (o.state='pending' AND o.due<=clock_timestamp() OR o.state='running' AND o.lease<clock_timestamp()) ORDER BY o.due,o.id FOR UPDATE OF o SKIP LOCKED LIMIT 1) UPDATE diamond_mail.outbox o SET state='running',attempts=o.attempts+1,fence=o.fence+1,lease=clock_timestamp()+INTERVAL '30 seconds' FROM candidate c WHERE o.app=$1 AND o.id=c.id RETURNING o.id,o.binding,o.parent,o.owner,o.recipient,o.template,o.body,o.restrictions,o.unscoped,o.fence,o.attempts", &[&db.app,&current_version])?;
    let claim = rows
        .first()
        .map(|row| -> Result<Claim> {
            Ok(Claim {
                id: row.try_get(0).map_err(|_| Error::Denied)?,
                binding: row.try_get(1).map_err(|_| Error::Denied)?,
                parent: row.try_get(2).map_err(|_| Error::Denied)?,
                owner: row.try_get(3).map_err(|_| Error::Denied)?,
                recipient: row.try_get(4).map_err(|_| Error::Denied)?,
                template: row.try_get(5).map_err(|_| Error::Denied)?,
                body: row.try_get(6).map_err(|_| Error::Denied)?,
                restrictions: row.try_get(7).map_err(|_| Error::Denied)?,
                unscoped: row.try_get(8).map_err(|_| Error::Denied)?,
                fence: row.try_get(9).map_err(|_| Error::Denied)?,
                attempts: row.try_get(10).map_err(|_| Error::Denied)?,
            })
        })
        .transpose()?;
    db.finish_request(Ok(claim))
}

fn execute(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
    claim: &Claim,
) -> Result<()> {
    let mut db = PostgresStore::open_with(project, configuration, false, None)?;
    // Serialize cancellation with the external effect. Without holding this row
    // lock through delivery, a concurrent cancel could report `cancelled` while
    // the already-claimed worker was still able to write the message.
    let rows=db.connection.query("SELECT id FROM diamond_mail.outbox WHERE app=$1 AND id=$2 AND fence=$3 AND state='running' AND lease>clock_timestamp() FOR UPDATE", &[&db.app,&claim.id,&claim.fence])?;
    if rows.is_empty() {
        return Err(Error::Conflict);
    }
    let binding = project
        .manifest
        .mail
        .as_ref()
        .and_then(|config| config.bindings.get(&claim.binding))
        .ok_or(Error::Denied)?;
    let template = binding
        .templates
        .get(&claim.template)
        .ok_or(Error::Denied)?;
    let owners=db.connection.query("SELECT username FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled FOR SHARE", &[&db.app,&claim.owner])?;
    if owners.is_empty() {
        return Err(Error::Unauthenticated);
    }
    let recipients=db.connection.query("SELECT email FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled FOR SHARE", &[&db.app,&claim.recipient])?;
    let recipient: String = recipients
        .first()
        .ok_or(Error::Denied)?
        .try_get(0)
        .map_err(|_| Error::Denied)?;
    db.connection.query(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,23))",
        &[&format!("{}:{}:{}", db.app, claim.binding, claim.recipient)],
    )?;
    let suppressed = !db.connection.query("SELECT recipient FROM diamond_mail.suppressions WHERE app=$1 AND binding=$2 AND recipient=$3", &[&db.app,&claim.binding,&claim.recipient])?.is_empty();
    if suppressed {
        let rows=db.connection.query("UPDATE diamond_mail.outbox SET state='suppressed',outcome='recipient_suppressed',lease=NULL WHERE app=$1 AND id=$2 AND fence=$3 AND state='running' RETURNING id", &[&db.app,&claim.id,&claim.fence])?;
        return if rows.is_empty() {
            db.finish_request(Err(Error::Conflict))
        } else {
            db.finish_request(Ok(()))
        };
    }
    reauthorize(&mut db, binding, claim)?;
    let grant = runtime.mail.get(&claim.binding).ok_or(Error::Denied)?;
    let store = Store::open(project, &claim.binding, grant)?;
    store.deliver(claim, &address(&recipient)?, &template.subject)?;
    let rows=db.connection.query("UPDATE diamond_mail.outbox SET state='delivered',outcome='delivered',lease=NULL WHERE app=$1 AND id=$2 AND fence=$3 AND state='running' AND lease>clock_timestamp() RETURNING id", &[&db.app,&claim.id,&claim.fence])?;
    if rows.is_empty() {
        return db.finish_request(Err(Error::Conflict));
    }
    db.finish_request(Ok(()))
}

fn reauthorize(db: &mut PostgresStore, binding: &Binding, claim: &Claim) -> Result<()> {
    let grants = Effects::from([
        Effect::Read(binding.resource.clone()),
        Effect::Write(binding.resource.clone()),
    ]);
    let owner = Principal::User {
        id: claim.owner.clone(),
        roles: BTreeSet::new(),
    };
    let restrictions: Option<Restrictions> =
        serde_json::from_str(&claim.restrictions).map_err(|_| Error::Denied)?;
    let scope = restrictions.as_ref().map_or_else(
        || Scope::new(&owner, &grants),
        |restrictions| Scope::restricted(&owner, &grants, restrictions, claim.unscoped),
    );
    db.attachment(&scope, &binding.resource, &claim.parent, true)?;
    db.authorize_recipient(&binding.resource, &claim.parent, &claim.recipient)
}

pub(super) fn step(
    project: &CheckedProject,
    configuration: &str,
    runtime: &crate::RuntimeGrants,
) -> Result<bool> {
    let Some(claim) = claim(project, configuration)? else {
        return Ok(false);
    };
    if let Err(error) = execute(project, configuration, runtime, &claim) {
        eprintln!(
            "{}",
            serde_json::json!({"event":"mail_attempt_failed","class":match &error {Error::Denied=>"denied",Error::Unauthenticated=>"unauthenticated",Error::Conflict=>"conflict",Error::OutcomeUnknown(_)=>"unknown",_=>"failure"}})
        );
        let mut db = PostgresStore::open_control(project, configuration, None)?;
        let maximum = project
            .manifest
            .mail
            .as_ref()
            .ok_or(Error::Denied)?
            .bindings[&claim.binding]
            .max_attempts;
        let terminal =
            matches!(error, Error::Denied | Error::Unauthenticated) || claim.attempts >= maximum;
        let state = if terminal { "dead" } else { "pending" };
        let outcome = if terminal {
            "denied_or_exhausted"
        } else {
            "retry_after_failure_or_unknown"
        };
        let delay = 1_i32
            .checked_shl(u32::try_from(claim.attempts.saturating_sub(1)).unwrap_or(8))
            .unwrap_or(60)
            .min(60);
        db.connection.query("UPDATE diamond_mail.outbox SET state=$4,outcome=$5,lease=NULL,due=clock_timestamp()+$6::integer*INTERVAL '1 second' WHERE app=$1 AND id=$2 AND fence=$3 AND state='running'", &[&db.app,&claim.id,&claim.fence,&state,&outcome,&delay])?;
        db.finish_request(Ok(()))?;
    }
    Ok(true)
}

pub(super) fn work(
    project: CheckedProject,
    configuration: Zeroizing<String>,
    runtime: crate::RuntimeGrants,
) -> Result<()> {
    let project = Arc::new(project);
    let configuration = Arc::new(configuration);
    let runtime = Arc::new(runtime);
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(1)
        .max_blocking_threads(1)
        .enable_all()
        .build()?;
    rt.block_on(async {
        let stop = tokio::signal::ctrl_c();
        tokio::pin!(stop);
        loop {
            let project = Arc::clone(&project);
            let configuration = Arc::clone(&configuration);
            let runtime = Arc::clone(&runtime);
            let task = tokio::task::spawn_blocking(move || {
                if !project.manifest.jobs.is_empty() {
                    super::jobs::step(&project, &configuration, &runtime)?;
                }
                if !project.manifest.workflows.is_empty() {
                    super::workflows::step(&project, &configuration, &runtime)?;
                }
                if project.manifest.mail.is_some() {
                    step(&project, &configuration, &runtime)
                } else {
                    Ok(false)
                }
            });
            tokio::pin!(task);
            tokio::select! {
                result = &mut task => { result.map_err(|_| Error::Denied)??; },
                _ = &mut stop => { task.await.map_err(|_| Error::Denied)??; break; }
            }
            tokio::select! {
                () = tokio::time::sleep(Duration::from_millis(250)) => {},
                _ = &mut stop => break,
            }
        }
        Ok(())
    })
}

pub(super) fn contracts() -> serde_json::Value {
    serde_json::json!({
        "target":"server and durable worker",
        "capability":"Mail<static binding>",
        "submission":"transactional outbox with stable owner deduplication key",
        "recipient":"account username resolved to a currently verified address at delivery",
        "templates":"manifest-bound static subject and plain-text dynamic body; no raw headers, HTML, SMTP, or address input",
        "authority":"sender write and recipient read policy rechecked at delivery; suppression rechecked before output",
        "replay":"bounded at-least-once worker with idempotent delivery id and byte-identical reconciliation",
        "limits":{"bindings":16,"templates":128,"body_bytes":16384,"attempts":8,"outbox_rows":10000,"messages":20000},
        "failure":"redacted status and logs; uncertain completion retries the same immutable delivery id"
    })
}
