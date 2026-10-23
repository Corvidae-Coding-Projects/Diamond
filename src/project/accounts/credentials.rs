use super::{Restrictions, digest, stored, valid_secret};
use crate::{
    authority::{Principal, random_hex},
    error::{Error, Result},
    project::{CheckedProject, api::Auth, input, storage::PostgresStore},
};
use std::collections::BTreeSet;
use zeroize::Zeroizing;
pub(in crate::project) struct Grant {
    pub principal: Principal,
    pub restrictions: Restrictions,
}
impl PostgresStore {
    pub(in crate::project) fn create_credential(
        &mut self,
        user: &str,
        label: &str,
        operations: &[String],
        scopes: &Restrictions,
        lifetime: &str,
        project: &CheckedProject,
    ) -> Result<(Zeroizing<String>, String)> {
        let seconds = lifetime
            .parse::<i64>()
            .map_err(|_| input("invalid credential lifetime"))?;
        if !(60..=2_592_000).contains(&seconds)
            || seconds.to_string() != lifetime
            || label.is_empty()
            || label.len() > 160
            || label.chars().any(char::is_control)
        {
            return Err(input("invalid credential metadata"));
        }
        validate_grant(operations, scopes, project)?;
        let account = self.account(user, true)?;
        self.connection.query("DELETE FROM diamond_identity.credentials WHERE app=$1 AND username=$2 AND (expires<=pg_catalog.clock_timestamp() OR revoked OR epoch<>$3)",&[&self.app,&user,&account.epoch])?;
        let count = self.connection.query(
            "SELECT count(*) FROM diamond_identity.credentials WHERE app=$1 AND username=$2",
            &[&self.app, &user],
        )?;
        if stored(count[0].try_get::<_, i64>(0))? >= 64 {
            return Err(Error::Limit("API credential count"));
        }
        let token = Zeroizing::new(format!("dia_{}", random_hex()?));
        let id = random_hex()?;
        let scopes =
            serde_json::to_string(scopes).map_err(|_| input("invalid credential scopes"))?;
        self.connection.query("INSERT INTO diamond_identity.credentials(app,id,digest,username,epoch,label,operations,scopes,expires) VALUES($1,$2,$3,$4,$5,$6,$7,$8,pg_catalog.clock_timestamp()+$9::bigint*interval '1 second')",&[&self.app,&id,&digest(&self.app,"api",&token),&user,&account.epoch,&label,&operations,&scopes,&seconds])?;
        Ok((token, id))
    }
    pub(in crate::project) fn list_credentials(&mut self, user: &str) -> Result<String> {
        self.account(user, false)?;
        let rows=self.connection.query("SELECT id,label,operations,scopes,expires::text,revoked,epoch FROM diamond_identity.credentials WHERE app=$1 AND username=$2 ORDER BY id LIMIT 64",&[&self.app,&user])?;
        let mut result = Vec::new();
        for r in rows {
            result.push(serde_json::json!({"id":stored(r.try_get::<_,String>(0))?,"label":stored(r.try_get::<_,String>(1))?,"operations":stored(r.try_get::<_,Vec<String>>(2))?,"scopes":serde_json::from_str::<Restrictions>(&stored(r.try_get::<_,String>(3))?).map_err(|_|Error::Postgres)?,"expires":stored(r.try_get::<_,String>(4))?,"revoked":stored(r.try_get::<_,bool>(5))?}));
        }
        Ok(serde_json::Value::Array(result).to_string())
    }
    pub(in crate::project) fn revoke_credential(&mut self, user: &str, id: &str) -> Result<()> {
        if !valid_secret(id) {
            return Err(input("invalid credential id"));
        }
        self.account(user, true)?;
        let rows=self.connection.query("UPDATE diamond_identity.credentials SET revoked=true WHERE app=$1 AND username=$2 AND id=$3 RETURNING id",&[&self.app,&user,&id])?;
        if rows.is_empty() {
            return Err(Error::Denied);
        }
        Ok(())
    }
    pub(in crate::project) fn api_grant(
        &mut self,
        token: &str,
        name: &str,
        project: &CheckedProject,
    ) -> Result<Grant> {
        self.credential_grant(token, name, project)
    }

    pub(in crate::project) fn git_grant(
        &mut self,
        token: &str,
        binding: &str,
        write: bool,
        project: &CheckedProject,
    ) -> Result<Grant> {
        let operation = super::super::git::credential_operation(binding, write);
        self.credential_grant(token, &operation, project)
    }

    fn credential_grant(
        &mut self,
        token: &str,
        operation: &str,
        project: &CheckedProject,
    ) -> Result<Grant> {
        if !token.strip_prefix("dia_").is_some_and(valid_secret) {
            return Err(Error::Unauthenticated);
        }
        let rows=self.connection.query("SELECT username,epoch,operations,scopes FROM diamond_identity.credentials WHERE app=$1 AND digest=$2 AND NOT revoked AND expires>pg_catalog.clock_timestamp()",&[&self.app,&digest(&self.app,"api",token)])?;
        let row = rows.first().ok_or(Error::Unauthenticated)?;
        let user: String = stored(row.try_get(0))?;
        let epoch: i64 = stored(row.try_get(1))?;
        let operations: Vec<String> = stored(row.try_get(2))?;
        let scopes: Restrictions = serde_json::from_str(&stored(row.try_get::<_, String>(3))?)
            .map_err(|_| Error::Denied)?;
        let account = self.account(&user, false)?;
        if epoch != account.epoch {
            return Err(Error::Unauthenticated);
        }
        validate_grant(&operations, &scopes, project)?;
        if !operations.iter().any(|op| op == operation) {
            return Err(Error::Denied);
        }
        let principal = Principal::User {
            id: user,
            roles: if account.administrator {
                BTreeSet::from(["account_admin".into()])
            } else {
                BTreeSet::new()
            },
        };
        Ok(Grant {
            principal,
            restrictions: scopes,
        })
    }
}
fn validate_grant(
    operations: &[String],
    scopes: &Restrictions,
    project: &CheckedProject,
) -> Result<()> {
    let unique = operations.iter().collect::<BTreeSet<_>>();
    if operations.is_empty()
        || operations.len() > 32
        || unique.len() != operations.len()
        || scopes.len() > 32
    {
        return Err(input("invalid credential allowlist"));
    }
    let mut needed = BTreeSet::new();
    for name in operations {
        if let Some(op) = project.api.get(name) {
            if op.auth != Auth::Token {
                return Err(Error::Denied);
            }
            needed.extend(op.scope_tags.iter().cloned());
        } else {
            needed.insert(super::super::git::credential_scope(project, name).ok_or(Error::Denied)?);
        }
    }
    if scopes.keys().cloned().collect::<BTreeSet<_>>() != needed
        || scopes.values().any(|ids| {
            ids.is_empty() || ids.len() > 32 || ids.iter().any(|id| !crate::auth::valid_id(id))
        })
    {
        return Err(input("credential scope must match operation scope tags"));
    }
    Ok(())
}
