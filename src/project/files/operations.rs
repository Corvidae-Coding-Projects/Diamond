//! Database transitions and reference authorization; files are never SQL rollback effects.
use super::super::{connected::Server, identity};
use super::{
    Binding, Deserialize, Error, File, Path, PostgresStore, Read, Result, Scope, artifact, fs,
    grants, identifier, input, options,
};
use serde_json::{Value as Json, json};
use sha2::{Digest as _, Sha256};
use std::io::{Seek as _, SeekFrom};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Begin {
    parent: String,
    filename: String,
    length: String,
    media_type: String,
}
pub(super) struct Access<'a> {
    pub server: &'a Server,
    pub binding: &'a str,
    pub token: Option<&'a str>,
    pub csrf: &'a str,
    pub upload: &'a str,
    pub write: bool,
}
impl Access<'_> {
    pub fn database(&self) -> Result<PostgresStore> {
        PostgresStore::open_control(
            &self.server.project,
            &self.server.secrets.configuration,
            self.server.pool.as_ref(),
        )
    }
    fn principal(&self, db: &mut PostgresStore) -> Result<crate::authority::Principal> {
        let csrf = if self.write {
            self.csrf.to_owned()
        } else {
            identity::csrf(&db.app, self.token.ok_or(Error::Unauthenticated)?)
        };
        let principal = db.principal(self.token, &csrf)?;
        if principal.id().is_none() {
            return Err(Error::Unauthenticated);
        }
        Ok(principal)
    }
    fn binding(&self) -> Result<&Binding> {
        self.server
            .project
            .manifest
            .files
            .as_ref()
            .and_then(|c| c.bindings.get(self.binding))
            .ok_or(Error::Denied)
    }
    fn authorize(
        &self,
        db: &mut PostgresStore,
        principal: &crate::authority::Principal,
        parent: &str,
    ) -> Result<()> {
        let binding = self.binding()?;
        let effects = grants(binding, self.write);
        db.attachment(
            &Scope::new(principal, &effects),
            &binding.resource,
            parent,
            self.write,
        )
    }
    pub fn begin(&self, body: &[u8]) -> Result<Json> {
        self.server
            .objects
            .as_ref()
            .ok_or(Error::Denied)?
            .verify_root()?;
        let b: Begin = super::super::wire::strict_from_slice(body)
            .map_err(|_| input("invalid upload reservation"))?;
        let length: i64 = b
            .length
            .parse()
            .map_err(|_| input("invalid upload length"))?;
        let binding = self.binding()?;
        if b.length != length.to_string()
            || length < 0
            || u64::try_from(length).map_err(|_| Error::Denied)? > binding.max_bytes
            || b.filename.is_empty()
            || b.filename.len() > 160
            || b.filename.contains(['/', '\\', ':', '"'])
            || b.filename.chars().any(char::is_control)
            || [".", ".."].contains(&b.filename.as_str())
            || b.media_type.len() > 128
            || b.media_type.chars().any(char::is_control)
        {
            return Err(input("invalid upload metadata or size"));
        }
        let mut db = self.database()?;
        let principal = self.principal(&mut db)?;
        self.authorize(&mut db, &principal, &b.parent)?;
        db.connection.query(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,6))",
            &[&db.app],
        )?;
        let rows = db.connection.query("SELECT COUNT(*) FILTER (WHERE owner=$2 AND state IN ('reserved','receiving','staged')), COALESCE(SUM(length) FILTER (WHERE state NOT IN ('deleted','failed')),0)::BIGINT, COUNT(*) FROM diamond_w6.objects WHERE app=$1", &[&db.app,&principal.id().ok_or(Error::Denied)?])?;
        let count: i64 = rows[0].try_get(0).map_err(|_| Error::Denied)?;
        let bytes: i64 = rows[0].try_get(1).map_err(|_| Error::Denied)?;
        let objects: i64 = rows[0].try_get(2).map_err(|_| Error::Denied)?;
        if count >= 4 || objects >= 10_000 || bytes.saturating_add(length) > 512 * 1024 * 1024 {
            return Err(Error::Limit("shared upload quota"));
        }
        let id = crate::authority::random_hex()?;
        let upload = zeroize::Zeroizing::new(crate::authority::random_hex()?);
        db.connection.query("INSERT INTO diamond_w6.objects(app,id,binding,parent,owner,name,media,length,state,token_hash) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'reserved',$9)", &[&db.app,&id,&self.binding,&b.parent,&principal.id().ok_or(Error::Denied)?,&b.filename,&b.media_type,&length,&artifact::hash(upload.as_bytes())])?;
        db.finish_request(Ok(()))?;
        Ok(json!({"id":id,"upload":upload.as_str(),"state":"reserved"}))
    }
    fn row(&self, db: &mut PostgresStore, id: &str, upload: bool) -> Result<Object> {
        if !identifier(id) {
            return Err(Error::Denied);
        }
        let principal = self.principal(db)?;
        let rows = db.connection.query("SELECT parent,owner,name,media,length,state,token_hash,sha256,expires>clock_timestamp() FROM diamond_w6.objects WHERE app=$1 AND id=$2 AND binding=$3 FOR UPDATE", &[&db.app,&id,&self.binding])?;
        let row = rows.first().ok_or(Error::Denied)?;
        let object = Object {
            parent: row.try_get(0).map_err(|_| Error::Denied)?,
            owner: row.try_get(1).map_err(|_| Error::Denied)?,
            name: row.try_get(2).map_err(|_| Error::Denied)?,
            media: row.try_get(3).map_err(|_| Error::Denied)?,
            length: row.try_get(4).map_err(|_| Error::Denied)?,
            state: row.try_get(5).map_err(|_| Error::Denied)?,
            sha256: row.try_get(7).map_err(|_| Error::Denied)?,
            live: row.try_get(8).map_err(|_| Error::Denied)?,
        };
        self.authorize(db, &principal, &object.parent)?;
        if (object.state != "ready" && principal.id() != Some(object.owner.as_str()))
            || (upload
                && (!object.live
                    || principal.id() != Some(object.owner.as_str())
                    || !identifier(self.upload)
                    || !super::super::identity::constant_equal(
                        &artifact::hash(self.upload.as_bytes()),
                        &row.try_get::<_, String>(6).map_err(|_| Error::Denied)?,
                    )))
        {
            return Err(Error::Denied);
        }
        Ok(object)
    }
    pub fn claim(&self, id: &str, length: i64) -> Result<i64> {
        let mut db = self.database()?;
        let object = self.row(&mut db, id, true)?;
        if object.length != length {
            return Err(input("upload length mismatch"));
        }
        if object.state != "reserved" {
            return Err(Error::Conflict);
        }
        db.connection.query(
            "UPDATE diamond_w6.objects SET state='receiving' WHERE app=$1 AND id=$2",
            &[&db.app, &id],
        )?;
        db.finish_request(Ok(()))?;
        Ok(object.length)
    }
    pub fn staged(&self, id: &str, sha256: &str) -> Result<()> {
        let mut db = self.database()?;
        let object = self.row(&mut db, id, true)?;
        if object.state != "receiving" {
            return Err(Error::Conflict);
        }
        db.connection.query(
            "UPDATE diamond_w6.objects SET state='staged',sha256=$3 WHERE app=$1 AND id=$2",
            &[&db.app, &id, &sha256],
        )?;
        db.finish_request(Ok(()))
    }
    pub fn finish(&self, id: &str) -> Result<Json> {
        let mut db = self.database()?;
        let object = self.row(&mut db, id, true)?;
        if object.state == "ready" {
            db.finish_request(Ok(()))?;
            return Ok(json!({"id":id,"state":"ready"}));
        }
        if object.state != "staged" {
            return Err(Error::Conflict);
        }
        let store = self.server.objects.as_ref().ok_or(Error::Denied)?;
        let source = store.path(id, true)?;
        let target = store.path(id, false)?;
        let chosen = if target.exists() { &target } else { &source };
        verify(chosen, &object, &db)?;
        if chosen == &source {
            fs::rename(&source, &target)?;
            File::open(&store.directory)?.sync_all()?;
        }
        // A commit failure may leave a renamed, still-unreadable object. Explicit
        // retry verifies that object; expiry reconciliation removes it otherwise.
        db.connection.query(
            "UPDATE diamond_w6.objects SET state='ready' WHERE app=$1 AND id=$2",
            &[&db.app, &id],
        )?;
        db.finish_request(Ok(()))?;
        Ok(json!({"id":id,"state":"ready"}))
    }
    pub fn status(&self, id: &str) -> Result<Json> {
        let mut db = self.database()?;
        let o = self.row(&mut db, id, false)?;
        if o.state != "ready" && !o.live {
            return Err(Error::Denied);
        }
        db.finish_request(Ok(()))?;
        Ok(
            json!({"id":id,"owner":o.owner,"filename":o.name,"media_type":o.media,"length":o.length.to_string(),"state":o.state,"sha256":o.sha256}),
        )
    }
    pub fn download(&self, id: &str) -> Result<(File, u64)> {
        let mut db = self.database()?;
        let o = self.row(&mut db, id, false)?;
        if o.state != "ready" {
            return Err(Error::Denied);
        }
        let file = verify(
            &self
                .server
                .objects
                .as_ref()
                .ok_or(Error::Denied)?
                .path(id, false)?,
            &o,
            &db,
        )?;
        db.finish_request(Ok(()))?;
        Ok((file, u64::try_from(o.length).map_err(|_| Error::Denied)?))
    }
    pub fn delete(&self, id: &str) -> Result<Json> {
        let mut db = self.database()?;
        let object = self.row(&mut db, id, false)?;
        if !["ready", "deleting"].contains(&object.state.as_str()) {
            return Err(Error::Conflict);
        }
        db.connection.query(
            "UPDATE diamond_w6.objects SET state='deleting' WHERE app=$1 AND id=$2",
            &[&db.app, &id],
        )?;
        db.finish_request(Ok(()))?;
        self.server
            .objects
            .as_ref()
            .ok_or(Error::Denied)?
            .remove(id)?;
        let mut db = self.database()?;
        db.connection.query("UPDATE diamond_w6.objects SET state='deleted',token_hash='' WHERE app=$1 AND id=$2 AND state='deleting'", &[&db.app,&id])?;
        db.finish_request(Ok(()))?;
        Ok(json!({"id":id,"state":"deleted"}))
    }
}
struct Object {
    parent: String,
    owner: String,
    name: String,
    media: String,
    length: i64,
    state: String,
    sha256: String,
    live: bool,
}
fn verify(path: &Path, object: &Object, db: &PostgresStore) -> Result<File> {
    let mut file = options().read(true).open(path)?;
    let metadata = file.metadata()?;
    if !metadata.is_file()
        || metadata.len() != u64::try_from(object.length).map_err(|_| Error::Denied)?
    {
        return Err(Error::Denied);
    }
    let mut sha = Sha256::new();
    let mut buffer = vec![0u8; 65536];
    loop {
        db.check_deadline()?;
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        sha.update(&buffer[..n]);
    }
    if super::hex(&sha.finalize()) != object.sha256 {
        return Err(Error::Denied);
    }
    file.seek(SeekFrom::Start(0))?;
    Ok(file)
}
