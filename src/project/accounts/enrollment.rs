use super::{Delivery, Enrollment, digest, email, password, stored, valid_secret};
use crate::{
    authority::random_hex,
    error::{Error, Result},
    project::{input, storage::PostgresStore},
};
use zeroize::Zeroizing;
impl PostgresStore {
    pub(in crate::project) fn challenge(
        &mut self,
        purpose: &'static str,
        address: &str,
        user: Option<&str>,
        epoch: i64,
    ) -> Result<Delivery> {
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{}:challenge-capacity", self.app)],
        )?;
        self.connection.query("DELETE FROM diamond_identity.challenges WHERE app=$1 AND expires<=pg_catalog.clock_timestamp()",&[&self.app])?;
        let rows = self.connection.query(
            "SELECT count(*) FROM diamond_identity.challenges WHERE app=$1",
            &[&self.app],
        )?;
        if stored(rows[0].try_get::<_, i64>(0))? >= 20000 {
            return Err(Error::Limit("account challenges"));
        }
        self.connection.query("UPDATE diamond_identity.challenges SET consumed=true WHERE app=$1 AND purpose=$2 AND email=$3 AND NOT consumed",&[&self.app,&purpose,&address])?;
        let token = Zeroizing::new(random_hex()?);
        self.connection.query("INSERT INTO diamond_identity.challenges(app,digest,purpose,email,username,epoch,expires) VALUES($1,$2,$3,$4,$5,$6,pg_catalog.clock_timestamp()+interval '30 minutes')",&[&self.app,&digest(&self.app,purpose,&token),&purpose,&address,&user,&epoch])?;
        Ok(Delivery {
            email: address.into(),
            purpose,
            token,
        })
    }
    pub(in crate::project) fn invite_account(&mut self, address: &str) -> Result<Delivery> {
        let address = email(address)?;
        self.challenge("invite", &address, None, 0)
    }
    pub(in crate::project) fn register(
        &mut self,
        username: &str,
        address: &str,
        secret: &str,
        invite: &str,
    ) -> Result<Option<Delivery>> {
        if !crate::auth::valid_id(username) {
            return Err(input("invalid username"));
        }
        let address = email(address)?;
        password(secret)?;
        let config = self.identity.as_ref().ok_or(Error::Denied)?;
        let enrollment = config.enrollment;
        if enrollment == Enrollment::Closed {
            return Err(Error::Denied);
        }
        self.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
            &[&format!("{}:account-capacity", self.app)],
        )?;
        let exists=self.connection.query("SELECT username FROM diamond_identity.users WHERE app=$1 AND (username=$2 OR (email=$3 AND password_hash IS NOT NULL))",&[&self.app,&username,&address])?;
        // Hash known and unknown registration inputs alike before generic acceptance.
        let hash = Zeroizing::new(crate::accounts::hash_password(secret)?);
        if enrollment == Enrollment::Invite {
            if !valid_secret(invite) {
                return Err(Error::Denied);
            }
            let rows=self.connection.query("SELECT email FROM diamond_identity.challenges WHERE app=$1 AND digest=$2 AND purpose='invite' AND NOT consumed AND expires>pg_catalog.clock_timestamp() FOR UPDATE",&[&self.app,&digest(&self.app,"invite",invite)])?;
            if rows
                .first()
                .map(|r| stored(r.try_get::<_, String>(0)))
                .transpose()?
                .as_deref()
                != Some(address.as_str())
            {
                return Err(Error::Denied);
            }
            self.connection.query(
                "UPDATE diamond_identity.challenges SET consumed=true WHERE app=$1 AND digest=$2",
                &[&self.app, &digest(&self.app, "invite", invite)],
            )?;
        }
        if !exists.is_empty() {
            return Ok(None);
        }
        self.account_capacity()?;
        self.connection.query("INSERT INTO diamond_identity.users(app,username,email,password_hash,verified) VALUES($1,$2,$3,$4,false)",&[&self.app,&username,&address,&hash.as_str()])?;
        self.challenge("verify", &address, Some(username), 1)
            .map(Some)
    }
    pub(in crate::project) fn request_challenge(
        &mut self,
        address: &str,
        purpose: &'static str,
    ) -> Result<Option<Delivery>> {
        let address = email(address)?;
        let rows=self.connection.query("SELECT username,epoch,verified FROM diamond_identity.users WHERE app=$1 AND email=$2 AND password_hash IS NOT NULL AND NOT disabled FOR UPDATE",&[&self.app,&address])?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let verified: bool = stored(row.try_get(2))?;
        if purpose == "verify" && verified || purpose == "recover" && !verified {
            return Ok(None);
        }
        let username: String = stored(row.try_get(0))?;
        let epoch: i64 = stored(row.try_get(1))?;
        self.challenge(purpose, &address, Some(&username), epoch)
            .map(Some)
    }
    pub(in crate::project) fn redeem(
        &mut self,
        secret: &str,
        purpose: &'static str,
        new_password: Option<&str>,
    ) -> Result<()> {
        if !valid_secret(secret) {
            return Err(Error::Denied);
        }
        if let Some(password_) = new_password {
            password(password_)?;
        }
        let hash = digest(&self.app, purpose, secret);
        let rows=self.connection.query("SELECT username,email,epoch FROM diamond_identity.challenges WHERE app=$1 AND digest=$2 AND purpose=$3 AND NOT consumed AND expires>pg_catalog.clock_timestamp()",&[&self.app,&hash,&purpose])?;
        let row = rows.first().ok_or(Error::Denied)?;
        let username: Option<String> = stored(row.try_get(0))?;
        let username = username.ok_or(Error::Denied)?;
        let address: String = stored(row.try_get(1))?;
        let epoch: i64 = stored(row.try_get(2))?;
        let user=self.connection.query("SELECT epoch,email,verified FROM diamond_identity.users WHERE app=$1 AND username=$2 AND NOT disabled FOR UPDATE",&[&self.app,&username])?;
        let user = user.first().ok_or(Error::Denied)?;
        if stored(user.try_get::<_, i64>(0))? != epoch
            || stored(user.try_get::<_, String>(1))? != address
            || (purpose == "recover" && !stored(user.try_get::<_, bool>(2))?)
        {
            return Err(Error::Denied);
        }
        let consumed=self.connection.query("UPDATE diamond_identity.challenges SET consumed=true WHERE app=$1 AND digest=$2 AND NOT consumed AND expires>pg_catalog.clock_timestamp() RETURNING digest",&[&self.app,&hash])?;
        if consumed.is_empty() {
            return Err(Error::Denied);
        }
        if purpose == "verify" {
            self.connection.query(
                "UPDATE diamond_identity.users SET verified=true WHERE app=$1 AND username=$2",
                &[&self.app, &username],
            )?;
            Ok(())
        } else {
            self.replace_password(&username, new_password.ok_or(Error::Denied)?)
        }
    }
}
