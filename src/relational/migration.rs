//! Serialized additive schema changes and durable bounded backfill progress.
use super::{
    invalid,
    schema::{self, Spec},
    types::{Parameters, backfill_value, quote},
};
use crate::{
    error::{Error, Result},
    hir::CheckedProgram,
    host::HostRuntime,
    value::ValueBudget,
};
use serde_json::json;
fn coordinator(c: &mut HostRuntime, app: &str) -> Result<()> {
    c.batch("BEGIN")?;
    c.query(
        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))",
        &[&app],
    )?;
    Ok(())
}
fn finish(c: &mut HostRuntime, result: Result<String>) -> Result<String> {
    match result {
        Ok(value) => {
            c.batch("COMMIT")?;
            Ok(value)
        }
        Err(e) => {
            let _rollback = c.batch("ROLLBACK");
            Err(e)
        }
    }
}
pub(crate) fn status(c: &mut HostRuntime, p: &CheckedProgram, app: &str) -> Result<String> {
    let (version, shape, _) = schema::current(c, app)?;
    let rows = c.query(
        "SELECT name,last_id,done FROM diamond_w4.backfills WHERE app=$1 ORDER BY name",
        &[&app],
    )?;
    let mut fills = Vec::new();
    for r in rows {
        fills.push(json!({"name":r.try_get::<_,String>(0).map_err(|_|Error::Postgres)?,"last_id":r.try_get::<_,String>(1).map_err(|_|Error::Postgres)?,"done":r.try_get::<_,bool>(2).map_err(|_|Error::Postgres)?}));
    }
    Ok(json!({"profile":"relational-v1","version":version,"expected_version":p.storage.as_ref().ok_or(Error::Denied)?.version,"shape":shape,"backfills":fills}).to_string())
}
pub(crate) fn migrate(c: &mut HostRuntime, p: &CheckedProgram, app: &str) -> Result<String> {
    coordinator(c, app)?;
    let result = (|| {
        let (version, _, old) = schema::current(c, app)?;
        let new = schema::spec(p)?;
        if new.storage.version != version + 1 {
            return Err(invalid(
                "migration requires exactly the next schema version",
            ));
        }
        additive(&old, &new)?;
        for (r, fields) in &new.fields {
            if let Some(previous) = old.fields.get(r) {
                for (f, column) in fields {
                    if !previous.contains_key(f) {
                        c.batch(&format!(
                            "ALTER TABLE {} ADD COLUMN {}",
                            schema::table(app, r),
                            schema::column(f, column)
                        ))?;
                    }
                }
            } else {
                schema::create_table(c, app, r, fields)?;
            }
        }
        schema::install_keys(c, app, &new)?;
        let fingerprint = schema::catalog(c, app)?;
        c.query(
            "UPDATE diamond_w4.apps SET version=$2,shape=$3,spec=$4,catalog=$5 WHERE app=$1",
            &[
                &app,
                &i32::try_from(new.storage.version).map_err(|_| Error::Denied)?,
                &schema::shape(&new)?,
                &serde_json::to_string(&new).map_err(|_| invalid("schema encoding"))?,
                &fingerprint,
            ],
        )?;
        status(c, p, app)
    })();
    finish(c, result)
}
fn additive(old: &Spec, new: &Spec) -> Result<()> {
    for (r, fields) in &old.fields {
        let current=new.fields.get(r).ok_or_else(||invalid("resource removal requires an explicit data conversion outside this additive migrator"))?;
        for (f, spec) in fields {
            if current.get(f) != Some(spec) {
                return Err(invalid(
                    "field removal/type/default/constraint changes are unsupported; use an explicit data conversion",
                ));
            }
        }
        for (f, spec) in current {
            if !fields.contains_key(f)
                && (!spec.nullable && spec.default.is_none() || spec.created_by)
            {
                return Err(invalid(
                    "added fields require NULL or a checked constant default",
                ));
            }
        }
        let before = &old.storage.resources[r];
        let after = &new.storage.resources[r];
        if before.scope != after.scope
            || before.global_unique != after.global_unique
            || !before.indexes.iter().all(|k| after.indexes.contains(k))
            || !before.unique.iter().all(|k| after.unique.contains(k))
            || before.references.iter().any(|(k, v)| {
                after
                    .references
                    .get(k)
                    .is_none_or(|a| a.resource != v.resource || a.fields != v.fields)
            })
        {
            return Err(invalid(
                "removing/changing relationship or index contracts is unsupported",
            ));
        }
    }
    Ok(())
}
pub(crate) fn backfill(
    c: &mut HostRuntime,
    p: &CheckedProgram,
    app: &str,
    batch: usize,
) -> Result<String> {
    if !(1..=1000).contains(&batch) {
        return Err(invalid("backfill batch must be 1..1000"));
    }
    coordinator(c, app)?;
    let result = (|| {
        schema::validate(c, p, app)?;
        for (name, b) in &p.storage.as_ref().ok_or(Error::Denied)?.backfills {
            let plan = crate::project::artifact::hash(
                &serde_json::to_vec(b).map_err(|_| invalid("backfill encoding"))?,
            );
            c.query("INSERT INTO diamond_w4.backfills(app,name,plan) VALUES($1,$2,$3) ON CONFLICT(app,name) DO NOTHING",&[&app,&name,&plan])?;
            let rows=c.query("SELECT plan,last_id,done FROM diamond_w4.backfills WHERE app=$1 AND name=$2 FOR UPDATE",&[&app,&name])?;
            let row = &rows[0];
            if row.try_get::<_, String>(0).map_err(|_| Error::Postgres)? != plan {
                return Err(invalid("backfill plan changed; choose a new backfill name"));
            }
            if row.try_get::<_, bool>(2).map_err(|_| Error::Postgres)? {
                continue;
            }
            let last: String = row.try_get(1).map_err(|_| Error::Postgres)?;
            let ty = &p.records[&p.resources[&b.resource]].fields[&b.field].ty;
            let value = backfill_value(b, ty, p)?;
            let ids=c.query(&format!("SELECT \"id\" FROM {} WHERE \"id\">$1 ORDER BY \"id\" LIMIT {batch} FOR UPDATE",schema::table(app,&b.resource)),&[&last])?;
            let done = ids.len() < batch;
            let mut cursor = last;
            for row in &ids {
                let id: String = row.try_get(0).map_err(|_| Error::Postgres)?;
                let mut update = Parameters::default();
                let v = update.add(&value, ty)?;
                let key = update.raw(&id);
                c.relational_query(
                    &format!(
                        "UPDATE {} SET {}={v} WHERE \"id\"={key} AND {} IS NULL",
                        schema::table(app, &b.resource),
                        quote(&b.field),
                        quote(&b.field)
                    ),
                    &update.bindings(),
                )?;
                cursor = id;
            }
            c.query(
                "UPDATE diamond_w4.backfills SET last_id=$3,done=$4 WHERE app=$1 AND name=$2",
                &[&app, &name, &cursor, &done],
            )?;
            return Ok(
                json!({"backfill":name,"processed":ids.len(),"last_id":cursor,"done":done})
                    .to_string(),
            );
        }
        Ok(json!({"done":true,"processed":0}).to_string())
    })();
    finish(c, result)
}
pub(crate) fn audit(c: &mut HostRuntime, p: &CheckedProgram, app: &str) -> Result<String> {
    c.batch("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")?;
    let result = (|| {
        schema::validate(c, p, app)?;
        schema::audit_catalog(c, app)?;
        let mut counts = std::collections::BTreeMap::new();
        for r in p.resources.keys() {
            let mut last = String::new();
            let mut total = 0usize;
            loop {
                c.reset_deadline();
                let rows = c.query(
                    &format!(
                        "SELECT {} FROM {} AS a WHERE a.\"id\">$1 ORDER BY a.\"id\" LIMIT 100",
                        super::query::projection(p, r, "a"),
                        schema::table(app, r)
                    ),
                    &[&last],
                )?;
                let len = rows.len();
                let mut budget = ValueBudget::new();
                for row in rows {
                    let value = super::query::read(&row, 0, p, r, &mut budget)?;
                    let crate::value::ValueKind::Record(_, fields) = value.0 else {
                        return Err(Error::Denied);
                    };
                    last = fields["id"].display_text()?;
                    total = total
                        .checked_add(1)
                        .ok_or(Error::Limit("integrity row count"))?;
                }
                if len < 100 {
                    break;
                }
            }
            let count = c.query(
                &format!("SELECT count(*) FROM {}", schema::table(app, r)),
                &[],
            )?[0]
                .try_get::<_, i64>(0)
                .map_err(|_| Error::Postgres)?;
            if usize::try_from(count).ok() != Some(total) {
                return Err(invalid("integrity scan found invalid ordering keys"));
            }
            counts.insert(r, total);
        }
        Ok(json!({"profile":"relational-v1","rows":counts,"valid":true}).to_string())
    })();
    finish(c, result)
}
