//! Checked physical schema and explicit current-version metadata.
use super::{
    Storage, invalid,
    types::{check, literal, quote, sql_type},
};
use crate::{
    error::{Error, Result},
    hir::CheckedProgram,
    host::HostRuntime,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub(crate) const META:&str="CREATE SCHEMA IF NOT EXISTS diamond_w4;
CREATE TABLE IF NOT EXISTS diamond_w4.apps(app TEXT PRIMARY KEY REFERENCES diamond_w2.apps(app), version INTEGER NOT NULL CHECK(version>0), shape TEXT NOT NULL, spec TEXT NOT NULL CHECK(octet_length(spec)<=1048576), catalog TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diamond_w4.backfills(app TEXT NOT NULL REFERENCES diamond_w4.apps(app), name TEXT NOT NULL, plan TEXT NOT NULL, last_id TEXT NOT NULL DEFAULT '', done BOOLEAN NOT NULL DEFAULT false, PRIMARY KEY(app,name));";
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct FieldSpec {
    pub ty: String,
    pub sql_type: String,
    pub nullable: bool,
    pub check: String,
    pub default: Option<String>,
    pub created_by: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Spec {
    pub storage: Storage,
    pub fields: BTreeMap<String, BTreeMap<String, FieldSpec>>,
}
pub(crate) fn table(app: &str, resource: &str) -> String {
    format!(
        "diamond_w4.{}",
        quote(&format!(
            "r_{}_{}",
            &app[..16],
            &crate::project::artifact::hash(resource.as_bytes())[..16]
        ))
    )
}
pub(crate) fn key(app: &str, resource: &str, kind: &str, fields: &[String]) -> String {
    let bytes = format!("{app}:{resource}:{kind}:{fields:?}");
    format!(
        "k_{}",
        &crate::project::artifact::hash(bytes.as_bytes())[..40]
    )
}
pub(crate) fn spec(p: &CheckedProgram) -> Result<Spec> {
    let storage = p.storage.clone().ok_or(Error::Denied)?;
    let mut fields = BTreeMap::new();
    for (r, n) in &p.resources {
        let mut fs = BTreeMap::new();
        for (f, v) in &p.records[n].fields {
            fs.insert(
                f.clone(),
                FieldSpec {
                    ty: v.ty.to_string(),
                    sql_type: sql_type(&v.ty).into(),
                    nullable: matches!(v.ty, crate::value::Type::Option(_)),
                    check: check(f, &v.ty, p)?,
                    default: v
                        .default
                        .as_ref()
                        .map(|v| literal(v, &p.records[n].fields[f].ty))
                        .transpose()?,
                    created_by: v.created_by,
                },
            );
        }
        fields.insert(r.clone(), fs);
    }
    Ok(Spec { storage, fields })
}
pub(crate) fn shape(spec: &Spec) -> Result<String> {
    let mut physical = spec.clone();
    physical.storage.version = 0;
    physical.storage.backfills.clear();
    physical.storage.lookups.clear();
    physical.storage.joins.clear();
    let s = serde_json::to_vec(&physical).map_err(|_| invalid("schema encoding"))?;
    Ok(crate::project::artifact::hash(&s))
}
pub(crate) fn column(name: &str, f: &FieldSpec) -> String {
    format!(
        "{} {}{}{}{} CHECK ({})",
        quote(name),
        f.sql_type,
        if f.sql_type == "text" {
            " COLLATE \"C\""
        } else {
            ""
        },
        if f.nullable { "" } else { " NOT NULL" },
        f.default
            .as_ref()
            .map_or(String::new(), |d| format!(" DEFAULT {d}")),
        f.check
    )
}
pub(crate) fn initialize(c: &mut HostRuntime, p: &CheckedProgram, app: &str) -> Result<()> {
    c.batch(META)?;
    c.query("INSERT INTO diamond_w2.apps(app,profile,shape) VALUES($1,2,'relational-v1') ON CONFLICT(app) DO NOTHING",&[&app])?;
    if !c
        .query("SELECT app FROM diamond_w4.apps WHERE app=$1", &[&app])?
        .is_empty()
    {
        return validate(c, p, app);
    }
    let spec = spec(p)?;
    let rows = c.query("SELECT profile FROM diamond_w2.apps WHERE app=$1", &[&app])?;
    if rows.first().and_then(|r| r.try_get::<_, i32>(0).ok()) != Some(2) {
        return Err(invalid("application already uses another storage profile"));
    }
    for (r, fields) in &spec.fields {
        create_table(c, app, r, fields)?;
    }
    install_keys(c, app, &spec)?;
    let fingerprint = catalog(c, app)?;
    c.query(
        "INSERT INTO diamond_w4.apps(app,version,shape,spec,catalog) VALUES($1,$2,$3,$4,$5)",
        &[
            &app,
            &i32::try_from(spec.storage.version).map_err(|_| Error::Denied)?,
            &shape(&spec)?,
            &serde_json::to_string(&spec).map_err(|_| invalid("schema encoding"))?,
            &fingerprint,
        ],
    )?;
    Ok(())
}
pub(crate) fn create_table(
    c: &mut HostRuntime,
    app: &str,
    r: &str,
    fs: &BTreeMap<String, FieldSpec>,
) -> Result<()> {
    c.batch(&format!(
        "CREATE TABLE {} ({}, PRIMARY KEY(\"id\"))",
        table(app, r),
        fs.iter()
            .map(|(n, f)| column(n, f))
            .collect::<Vec<_>>()
            .join(",")
    ))
}
pub(crate) fn install_keys(c: &mut HostRuntime, app: &str, spec: &Spec) -> Result<()> {
    for (r, config) in &spec.storage.resources {
        for (unique, keys) in [(true, &config.unique), (false, &config.indexes)] {
            for fields in keys {
                let mut ordered = fields.clone();
                if !unique && !ordered.iter().any(|n| n == "id") {
                    ordered.push("id".into());
                }
                let name = key(app, r, if unique { "u" } else { "i" }, fields);
                c.batch(&format!(
                    "CREATE {} INDEX IF NOT EXISTS {} ON {} ({})",
                    if unique { "UNIQUE" } else { "" },
                    quote(&name),
                    table(app, r),
                    ordered
                        .iter()
                        .map(|n| quote(n))
                        .collect::<Vec<_>>()
                        .join(",")
                ))?;
            }
        }
    }
    for (r, config) in &spec.storage.resources {
        for (name, reference) in &config.references {
            let fields = reference.fields.keys().cloned().collect::<Vec<_>>();
            let k = key(app, r, &format!("f_{name}"), &fields);
            let exists=c.query("SELECT 1 FROM pg_catalog.pg_constraint WHERE connamespace='diamond_w4'::regnamespace AND conname=$1",&[&k])?;
            if exists.is_empty() {
                c.batch(&format!("ALTER TABLE {} ADD CONSTRAINT {} FOREIGN KEY ({}) REFERENCES {} ({}) ON DELETE RESTRICT",table(app,r),quote(&k),fields.iter().map(|f|quote(f)).collect::<Vec<_>>().join(","),table(app,&reference.resource),reference.fields.values().map(|f|quote(f)).collect::<Vec<_>>().join(",")))?;
            }
        }
    }
    Ok(())
}
pub(crate) fn current(c: &mut HostRuntime, app: &str) -> Result<(u32, String, Spec)> {
    let rows = c.query(
        "SELECT version,shape,spec::text FROM diamond_w4.apps WHERE app=$1",
        &[&app],
    )?;
    let row = rows
        .first()
        .ok_or_else(|| invalid("missing relational schema; run project-init explicitly"))?;
    let version: u32 = row
        .try_get::<_, i32>(0)
        .ok()
        .and_then(|n| n.try_into().ok())
        .ok_or_else(|| invalid("invalid schema version"))?;
    let shape = row
        .try_get(1)
        .map_err(|_| invalid("invalid schema shape"))?;
    let json: String = row.try_get(2).map_err(|_| invalid("invalid schema spec"))?;
    if json.len() > crate::MAX_SOURCE_BYTES {
        return Err(Error::Limit("schema metadata"));
    }
    let spec: Spec = serde_json::from_str(&json).map_err(|_| invalid("invalid schema spec"))?;
    if self::shape(&spec)? != shape {
        return Err(invalid("schema metadata digest mismatch"));
    }
    Ok((version, shape, spec))
}
pub(crate) fn validate(c: &mut HostRuntime, p: &CheckedProgram, app: &str) -> Result<()> {
    let (version, hash, _) = current(c, app)?;
    let expected = spec(p)?;
    if version != expected.storage.version || hash != shape(&expected)? {
        return Err(invalid(
            "schema version/shape mismatch; migrate explicitly and restart all application nodes",
        ));
    }
    Ok(())
}

/// Fingerprint only this application's physical managed schema, not role grants.
pub(crate) fn catalog(c: &mut HostRuntime, app: &str) -> Result<String> {
    let prefix = format!("r_{}_", &app[..16]);
    let rows=c.query("SELECT pg_catalog.json_build_object('table',n.nspname||'.'||t.relname,'kind',t.relkind,'rls',t.relrowsecurity,'force_rls',t.relforcerowsecurity,'columns',(SELECT pg_catalog.json_agg(pg_catalog.json_build_array(a.attname,a.atttypid::regtype::text,a.attnotnull,a.attcollation::regcollation::text,a.attidentity,a.attgenerated,pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped),'constraints',(SELECT pg_catalog.json_agg(pg_catalog.json_build_array(k.conname,k.contype,k.convalidated,pg_catalog.pg_get_constraintdef(k.oid)) ORDER BY k.conname) FROM pg_catalog.pg_constraint k WHERE k.conrelid=t.oid),'indexes',(SELECT pg_catalog.json_agg(pg_catalog.json_build_array(pg_catalog.pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready) ORDER BY i.indexrelid::regclass::text) FROM pg_catalog.pg_index i WHERE i.indrelid=t.oid),'triggers',(SELECT pg_catalog.json_agg(pg_catalog.json_build_array(g.tgname,g.tgenabled,pg_catalog.pg_get_triggerdef(g.oid)) ORDER BY g.tgname) FROM pg_catalog.pg_trigger g WHERE g.tgrelid=t.oid AND NOT g.tgisinternal))::text FROM pg_catalog.pg_class t JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='diamond_w4' AND pg_catalog.left(t.relname,pg_catalog.length($1::text))=$1 ORDER BY t.relname",&[&prefix])?;
    let mut descriptions = Vec::new();
    for row in rows {
        descriptions.push(row.try_get::<_, String>(0).map_err(|_| Error::Postgres)?);
    }
    Ok(crate::project::artifact::hash(
        descriptions.join("\n").as_bytes(),
    ))
}
pub(crate) fn audit_catalog(c: &mut HostRuntime, app: &str) -> Result<()> {
    let expected = c
        .query("SELECT catalog FROM diamond_w4.apps WHERE app=$1", &[&app])?
        .first()
        .ok_or(Error::Denied)?
        .try_get::<_, String>(0)
        .map_err(|_| Error::Postgres)?;
    if catalog(c, app)? != expected {
        return Err(invalid(
            "physical schema changed outside the migration coordinator",
        ));
    }
    Ok(())
}
