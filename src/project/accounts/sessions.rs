use super::{digest, email, password, stored, valid_secret};
use crate::{
    authority::{Principal, random_hex},
    error::{Error, Result},
    project::{
        identity::{Session, constant_equal, csrf},
        storage::PostgresStore,
    },
};
use std::collections::BTreeSet;
use zeroize::Zeroizing;
struct SessionIdentity {
    user: Option<String>,
}
pub(in crate::project) struct Account {
    pub username: String,
    pub epoch: i64,
    pub administrator: bool,
}
impl PostgresStore {
    pub(in crate::project) fn provision_account(
        &mut self,
        username: &str,
        address: &str,
        secret: &str,
        administrator: bool,
    ) -> Result<()> {
        if self.identity.is_none() || !crate::auth::valid_id(username) {
            return Err(Error::Denied);
        }
        password(secret)?;
        let address = email(address)?;
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{}:account-capacity", self.app)],
        )?;
        self.account_capacity()?;
        let hash = Zeroizing::new(crate::accounts::hash_password(secret)?);
        self.connection.query("INSERT INTO diamond_identity.users(app,username,email,password_hash,verified,administrator) VALUES($1,$2,$3,$4,true,$5)",&[&self.app,&username,&address,&hash.as_str(),&administrator])?;
        Ok(())
    }
    pub(in crate::project) fn account_capacity(&mut self) -> Result<()> {
        let rows = self.connection.query(
            "SELECT count(*) FROM diamond_identity.users WHERE app=$1",
            &[&self.app],
        )?;
        if stored(rows[0].try_get::<_, i64>(0))? >= 10000 {
            return Err(Error::Limit("account count"));
        }
        Ok(())
    }
    pub(in crate::project) fn account(
        &mut self,
        username: &str,
        exclusive: bool,
    ) -> Result<Account> {
        let rows=self.connection.query(&format!("SELECT epoch,administrator FROM diamond_identity.users WHERE app=$1 AND username=$2 AND verified AND NOT disabled FOR {}",if exclusive {"UPDATE"}else{"SHARE"}),&[&self.app,&username])?;
        let row = rows.first().ok_or(Error::Unauthenticated)?;
        Ok(Account {
            username: username.into(),
            epoch: stored(row.try_get(0))?,
            administrator: stored(row.try_get(1))?,
        })
    }
    fn account_session_identity(&mut self, token: &str) -> Result<Option<SessionIdentity>> {
        if !valid_secret(token) {
            return Ok(None);
        }
        let rows=self.connection.query("SELECT username,epoch FROM diamond_identity.sessions WHERE app=$1 AND digest=$2 AND expires>pg_catalog.clock_timestamp()",&[&self.app,&digest(&self.app,"session",token)])?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let user: Option<String> = stored(row.try_get(0))?;
        if let Some(username) = &user {
            let epoch: i64 = stored(row.try_get(1))?;
            match self.account(username, false) {
                Ok(account) if account.epoch == epoch => {}
                Ok(_) | Err(Error::Unauthenticated) => return Ok(None),
                Err(e) => return Err(e),
            }
        }
        Ok(Some(SessionIdentity { user }))
    }
    fn create_account_session(&mut self, user: Option<&Account>) -> Result<Session> {
        self.connection.query("DELETE FROM diamond_identity.sessions WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()",&[&self.app])?;
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{}:session-capacity", self.app)],
        )?;
        let rows = self.connection.query(
            "SELECT count(*) FROM diamond_identity.sessions WHERE app=$1",
            &[&self.app],
        )?;
        if stored(rows[0].try_get::<_, i64>(0))? >= 4096 {
            return Err(Error::Limit("account sessions"));
        }
        let token = Zeroizing::new(random_hex()?);
        let username = user.map(|u| u.username.clone());
        let epoch = user.map_or(0, |u| u.epoch);
        self.connection.query("INSERT INTO diamond_identity.sessions(app,digest,username,epoch,expires) VALUES($1,$2,$3,$4,pg_catalog.clock_timestamp()+interval '30 minutes')",&[&self.app,&digest(&self.app,"session",&token),&username,&epoch])?;
        Ok(Session {
            csrf: csrf(&self.app, &token),
            token,
            user: username,
        })
    }
    pub(in crate::project) fn account_session(&mut self, token: Option<&str>) -> Result<Session> {
        if let Some(token) = token
            && let Some(identity) = self.account_session_identity(token)?
        {
            return Ok(Session {
                token: Zeroizing::new(token.into()),
                user: identity.user,
                csrf: csrf(&self.app, token),
            });
        }
        self.create_account_session(None)
    }
    pub(in crate::project) fn account_principal(
        &mut self,
        token: Option<&str>,
        supplied: &str,
    ) -> Result<Principal> {
        let token = token.ok_or(Error::Unauthenticated)?;
        if !constant_equal(&csrf(&self.app, token), supplied) {
            return Err(Error::Denied);
        }
        let username = self
            .account_session_identity(token)?
            .ok_or(Error::Unauthenticated)?;
        Ok(if let Some(username) = username.user {
            let account = self.account(&username, false)?;
            Principal::User {
                id: username,
                roles: if account.administrator {
                    BTreeSet::from(["account_admin".into()])
                } else {
                    BTreeSet::new()
                },
            }
        } else {
            Principal::Anonymous
        })
    }
    pub(in crate::project) fn account_login(
        &mut self,
        token: &str,
        supplied: &str,
        username: &str,
        secret: &str,
        dummy: &str,
    ) -> Result<Session> {
        self.account_principal(Some(token), supplied)?;
        if secret.len() > 256 || !crate::auth::valid_id(username) {
            return Err(Error::Denied);
        }
        let rows=self.connection.query("SELECT password_hash,verified,disabled FROM diamond_identity.users WHERE app=$1 AND username=$2 FOR UPDATE",&[&self.app,&username])?;
        let hash = Zeroizing::new(
            rows.first()
                .map(|r| stored(r.try_get::<_, String>(0)))
                .transpose()?
                .unwrap_or_else(|| dummy.into()),
        );
        let valid = crate::accounts::verify_password(&hash, secret)?;
        let active = rows
            .first()
            .map(|r| {
                Ok::<_, Error>(
                    stored(r.try_get::<_, bool>(1))? && !stored(r.try_get::<_, bool>(2))?,
                )
            })
            .transpose()?
            .unwrap_or(false);
        if !valid || !active {
            return Err(Error::Denied);
        }
        let account = self.account(username, false)?;
        self.delete_account_session(token)?;
        self.create_account_session(Some(&account))
    }
    fn delete_account_session(&mut self, token: &str) -> Result<()> {
        self.connection.query(
            "DELETE FROM diamond_identity.sessions WHERE app=$1 AND digest=$2",
            &[&self.app, &digest(&self.app, "session", token)],
        )?;
        Ok(())
    }
    pub(in crate::project) fn account_logout(&mut self, token: &str, supplied: &str) -> Result<()> {
        self.account_principal(Some(token), supplied)?;
        self.delete_account_session(token)
    }
    pub(in crate::project) fn logout_all(&mut self, user: &str) -> Result<()> {
        self.account(user, true)?;
        self.connection.query(
            "DELETE FROM diamond_identity.sessions WHERE app=$1 AND username=$2",
            &[&self.app, &user],
        )?;
        Ok(())
    }
    pub(in crate::project) fn set_disabled(&mut self, user: &str, disabled: bool) -> Result<()> {
        let rows=self.connection.query("UPDATE diamond_identity.users SET disabled=$3,epoch=epoch+1 WHERE app=$1 AND username=$2 RETURNING username",&[&self.app,&user,&disabled])?;
        if rows.is_empty() {
            return Err(Error::Denied);
        }
        self.connection.query(
            "DELETE FROM diamond_identity.sessions WHERE app=$1 AND username=$2",
            &[&self.app, &user],
        )?;
        Ok(())
    }
    pub(in crate::project) fn change_password(
        &mut self,
        user: &str,
        current: &str,
        new: &str,
    ) -> Result<()> {
        password(new)?;
        if current.len() > 256 {
            return Err(Error::Denied);
        }
        self.account(user, true)?;
        let rows = self.connection.query(
            "SELECT password_hash FROM diamond_identity.users WHERE app=$1 AND username=$2",
            &[&self.app, &user],
        )?;
        let hash = Zeroizing::new(stored(rows[0].try_get::<_, String>(0))?);
        if !crate::accounts::verify_password(&hash, current)? {
            return Err(Error::Denied);
        }
        self.replace_password(user, new)
    }
    pub(in crate::project) fn replace_password(&mut self, user: &str, new: &str) -> Result<()> {
        password(new)?;
        let hash = Zeroizing::new(crate::accounts::hash_password(new)?);
        self.connection.query("UPDATE diamond_identity.users SET password_hash=$3,epoch=epoch+1 WHERE app=$1 AND username=$2",&[&self.app,&user,&hash.as_str()])?;
        self.connection.query(
            "DELETE FROM diamond_identity.sessions WHERE app=$1 AND username=$2",
            &[&self.app, &user],
        )?;
        Ok(())
    }
}
