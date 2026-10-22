//! `PostgreSQL`-backed, explicitly provisioned development identities and sessions.

use super::{artifact, input, storage::PostgresStore};
use crate::authority::{Principal, random_hex};
use crate::error::{Error, Result};
use std::collections::BTreeSet;
use zeroize::Zeroizing;

pub(super) struct Session {
    pub token: Zeroizing<String>,
    pub user: Option<String>,
    pub csrf: String,
}

struct VerifiedSession {
    user: Option<String>,
}

impl PostgresStore {
    pub(super) fn add_user(&mut self, username: &str, password: &str) -> Result<()> {
        if self.identity.is_some() {
            return self.provision_account(
                username,
                &format!("{username}@development.invalid"),
                password,
                false,
            );
        }
        if !crate::auth::valid_id(username) || !(12..=256).contains(&password.len()) {
            return Err(input(
                "use a valid 1..128-character username and a 12..256-byte password",
            ));
        }
        let count = self.connection.query(
            "SELECT count(*) FROM diamond_w2.users WHERE app=$1",
            &[&self.app],
        )?;
        if count[0].try_get::<_, i64>(0).map_err(|_| Error::Denied)? >= 10_000 {
            return Err(Error::Limit("W2 accounts"));
        }
        let hash = Zeroizing::new(crate::accounts::hash_password(password)?);
        self.connection.query(
            "INSERT INTO diamond_w2.users(app,username,password_hash) VALUES ($1,$2,$3)",
            &[&self.app, &username, &hash.as_str()],
        )?;
        Ok(())
    }

    fn find_session(&mut self, token: &str) -> Result<Option<VerifiedSession>> {
        if token.len() != 64
            || !token
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        {
            return Ok(None);
        }
        let digest = artifact::hash(token.as_bytes());
        let rows=self.connection.query("SELECT s.username FROM diamond_w2.sessions s WHERE app=$1 AND digest=$2 AND expires>pg_catalog.clock_timestamp() AND (username IS NULL OR EXISTS(SELECT 1 FROM diamond_w2.users u WHERE u.app=s.app AND u.username=s.username))", &[&self.app,&digest])?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let user: Option<String> = row
            .try_get(0)
            .map_err(|_| input("invalid session identity"))?;
        if user.as_ref().is_some_and(|v| !crate::auth::valid_id(v)) {
            return Err(input("invalid session identity"));
        }
        Ok(Some(VerifiedSession { user }))
    }

    pub(super) fn session(&mut self, token: Option<&str>) -> Result<Session> {
        if self.identity.is_some() {
            return self.account_session(token);
        }
        if let Some(token) = token
            && let Some(user) = self.find_session(token)?
        {
            return Ok(Session {
                token: Zeroizing::new(token.into()),
                user: user.user,
                csrf: csrf(&self.app, token),
            });
        }
        self.create_session(None)
    }

    fn create_session(&mut self, user: Option<String>) -> Result<Session> {
        self.connection.query("DELETE FROM diamond_w2.sessions WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()", &[&self.app])?;
        let count = self.connection.query(
            "SELECT count(*) FROM diamond_w2.sessions WHERE app=$1",
            &[&self.app],
        )?;
        if count[0].try_get::<_, i64>(0).map_err(|_| Error::Denied)? >= 4096 {
            return Err(Error::Limit("W2 shared sessions"));
        }
        let token = Zeroizing::new(random_hex()?);
        let digest = artifact::hash(token.as_bytes());
        self.connection.query("INSERT INTO diamond_w2.sessions(app,digest,username,expires) VALUES ($1,$2,$3,pg_catalog.clock_timestamp()+interval '30 minutes')", &[&self.app,&digest,&user])?;
        let csrf = csrf(&self.app, &token);
        Ok(Session { token, user, csrf })
    }

    pub(super) fn principal(
        &mut self,
        token: Option<&str>,
        supplied_csrf: &str,
    ) -> Result<Principal> {
        if self.identity.is_some() {
            return self.account_principal(token, supplied_csrf);
        }
        let token = token.ok_or(Error::Denied)?;
        let user = self.find_session(token)?.ok_or(Error::Denied)?;
        if !constant_equal(&csrf(&self.app, token), supplied_csrf) {
            return Err(Error::Denied);
        }
        Ok(user
            .user
            .map_or(Principal::Anonymous, |id| Principal::User {
                id,
                roles: BTreeSet::new(),
            }))
    }

    pub(super) fn login(
        &mut self,
        token: &str,
        supplied_csrf: &str,
        username: &str,
        password: &str,
        dummy_hash: &str,
    ) -> Result<Session> {
        if self.identity.is_some() {
            return self.account_login(token, supplied_csrf, username, password, dummy_hash);
        }
        self.principal(Some(token), supplied_csrf)?;
        if password.len() > 256 || !crate::auth::valid_id(username) {
            return Err(Error::Denied);
        }
        self.connection.query(
            "DELETE FROM diamond_w2.rates WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()",
            &[&self.app],
        )?;
        for (bucket, limit) in [
            ("global".to_owned(), 100),
            (format!("user:{}", artifact::hash(username.as_bytes())), 10),
        ] {
            let result=self.connection.query("INSERT INTO diamond_w2.rates(app,bucket,attempts,expires) VALUES ($1,$2,1,pg_catalog.clock_timestamp()+interval '1 minute') ON CONFLICT(app,bucket) DO UPDATE SET attempts=diamond_w2.rates.attempts+1 RETURNING attempts", &[&self.app,&bucket])?;
            if result[0].try_get::<_, i32>(0).map_err(|_| Error::Denied)? > limit {
                return Err(Error::Limit("shared login rate"));
            }
        }
        let rows = self.connection.query(
            "SELECT password_hash FROM diamond_w2.users WHERE app=$1 AND username=$2",
            &[&self.app, &username],
        )?;
        let hash = Zeroizing::new(
            rows.first()
                .map(|r| r.try_get::<_, String>(0))
                .transpose()
                .map_err(|_| input("invalid account data"))?
                .unwrap_or_else(|| dummy_hash.into()),
        );
        let valid = crate::accounts::verify_password(&hash, password)?;
        if rows.is_empty() || !valid {
            return Err(Error::Denied);
        }
        let digest = artifact::hash(token.as_bytes());
        self.connection.query(
            "DELETE FROM diamond_w2.sessions WHERE app=$1 AND digest=$2",
            &[&self.app, &digest],
        )?;
        self.create_session(Some(username.into()))
    }

    pub(super) fn logout(&mut self, token: &str, supplied_csrf: &str) -> Result<()> {
        if self.identity.is_some() {
            return self.account_logout(token, supplied_csrf);
        }
        self.principal(Some(token), supplied_csrf)?;
        self.connection.query(
            "DELETE FROM diamond_w2.sessions WHERE app=$1 AND digest=$2",
            &[&self.app, &artifact::hash(token.as_bytes())],
        )?;
        Ok(())
    }
}

pub(super) fn csrf(app: &str, token: &str) -> String {
    artifact::hash(format!("diamond-w2-csrf:{app}:{token}").as_bytes())
}

pub(super) fn constant_equal(a: &str, b: &str) -> bool {
    use subtle::ConstantTimeEq;
    a.as_bytes().ct_eq(b.as_bytes()).into()
}
