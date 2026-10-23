use super::invalid;
use crate::error::Result;
use crate::hir::CheckedProgram;
use crate::value::Type;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Storage {
    pub profile: String,
    pub version: u32,
    pub resources: BTreeMap<String, Resource>,
    #[serde(default)]
    pub lookups: BTreeMap<String, Lookup>,
    #[serde(default)]
    pub joins: BTreeMap<String, Join>,
    #[serde(default)]
    pub backfills: BTreeMap<String, Backfill>,
}
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Resource {
    pub scope: Option<String>,
    #[serde(default)]
    pub indexes: Vec<Vec<String>>,
    #[serde(default)]
    pub unique: Vec<Vec<String>>,
    #[serde(default)]
    pub global_unique: bool,
    #[serde(default)]
    pub references: BTreeMap<String, Reference>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Reference {
    pub resource: String,
    pub fields: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Lookup {
    pub resource: String,
    pub scope: String,
    pub principal: String,
    pub role: Option<String>,
    pub purpose: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Join {
    pub resource: String,
    pub reference: String,
    pub output: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Backfill {
    pub resource: String,
    pub field: String,
    pub value: toml::Value,
}

pub(crate) fn scalar(ty: &Type, p: &CheckedProgram) -> bool {
    match ty {
        Type::Int
        | Type::Decimal
        | Type::Timestamp
        | Type::Duration
        | Type::Bool
        | Type::Text
        | Type::BoundedText(..)
        | Type::Id(_) => true,
        Type::Enum(n) => p
            .enums
            .get(n)
            .is_some_and(|e| e.variants.values().all(Vec::is_empty)),
        Type::Option(t) => !matches!(t.as_ref(), Type::Option(_)) && scalar(t, p),
        _ => false,
    }
}
impl Storage {
    pub(crate) fn checked(&self, p: &CheckedProgram) -> Result<Self> {
        let this = self.clone();
        if this.profile != "relational-v1"
            || !(1..=1024).contains(&this.version)
            || this.resources.len() > 64
            || this.lookups.len() > 64
            || this.joins.len() > 64
            || this.backfills.len() > 64
        {
            return Err(invalid("invalid relational profile/version/budget"));
        }
        if this.resources.keys().collect::<BTreeSet<_>>() != p.resources.keys().collect() {
            return Err(invalid(
                "relational resources must exactly cover declared resources",
            ));
        }
        for name in this.resources.keys() {
            this.check_resource(name, p)?;
            this.check_references(name, p)?;
        }
        for name in this.resources.keys() {
            this.visit(name, &mut BTreeSet::new(), &mut BTreeSet::new())?;
        }
        for (name, l) in &this.lookups {
            this.check_lookup(name, l, p)?;
        }
        for (name, j) in &this.joins {
            this.check_join(name, j, p)?;
        }
        for b in this.backfills.values() {
            let record = p
                .resources
                .get(&b.resource)
                .and_then(|n| p.records.get(n))
                .ok_or_else(|| invalid("unknown backfill resource"))?;
            let ty = &record
                .fields
                .get(&b.field)
                .ok_or_else(|| invalid("unknown backfill field"))?
                .ty;
            if b.field == "id"
                || record.fields[&b.field].created_by
                || !matches!(ty, Type::Option(_))
            {
                return Err(invalid("backfills require an optional application field"));
            }
            super::types::backfill_value(b, ty, p)?;
        }
        Ok(this)
    }
    fn check_resource(&self, name: &str, p: &CheckedProgram) -> Result<()> {
        let r = &self.resources[name];
        let record = &p.records[&p.resources[name]];
        if record.fields.len() > 64
            || record.fields.keys().any(|n| {
                n.len() > 63
                    || [
                        "limit",
                        "offset",
                        "order",
                        "descending",
                        "after",
                        "relation",
                        "field",
                        "row",
                    ]
                    .contains(&n.as_str())
                    || ["__gt", "__gte", "__lt", "__lte"]
                        .iter()
                        .any(|s| n.ends_with(s))
            })
        {
            return Err(invalid("relational field count/name/query collision"));
        }
        if record.fields.values().any(|f| !scalar(&f.ty, p)) {
            return Err(invalid(
                "relational fields must be finite scalars, optional scalars or unit enums",
            ));
        }
        if let Some(scope) = &r.scope
            && !matches!(record.fields.get(scope).map(|f| &f.ty), Some(Type::Id(_)))
        {
            return Err(invalid("resource scope requires a nonoptional nominal Id"));
        }
        if r.indexes.len() + r.unique.len() + r.references.len() > 32 {
            return Err(invalid("relational index/constraint budget"));
        }
        for fields in r.indexes.iter().chain(&r.unique) {
            if fields.is_empty()
                || fields.len() > 4
                || fields.iter().collect::<BTreeSet<_>>().len() != fields.len()
                || fields.iter().any(|f| !record.fields.contains_key(f))
            {
                return Err(invalid("invalid relational index fields"));
            }
        }
        for fields in r.indexes.iter().chain(&r.unique) {
            let bytes = fields
                .iter()
                .map(|f| key_bytes(&record.fields[f].ty, p))
                .try_fold(0usize, |a, b| b.and_then(|b| a.checked_add(b)));
            if bytes.is_none_or(|n| n > 1800) {
                return Err(invalid(&format!(
                    "{name} index {fields:?} requires bounded values totaling at most 1800 encoded bytes"
                )));
            }
        }
        for fields in &r.unique {
            if !r.global_unique && r.scope.as_ref().is_none_or(|s| !fields.contains(s)) {
                return Err(invalid(
                    "unique key must include its scope or explicitly declare global availability disclosure",
                ));
            }
        }
        Ok(())
    }
    fn check_references(&self, name: &str, p: &CheckedProgram) -> Result<()> {
        let r = &self.resources[name];
        let record = &p.records[&p.resources[name]];
        for (reference_name, reference) in &r.references {
            let target = p
                .resources
                .get(&reference.resource)
                .ok_or_else(|| invalid("unknown reference resource"))?;
            if reference.fields.is_empty() || reference.fields.len() > 4 {
                return Err(invalid("reference arity must be 1..4"));
            }
            let mut targets = BTreeSet::new();
            for (left, right) in &reference.fields {
                let a = &record
                    .fields
                    .get(left)
                    .ok_or_else(|| invalid("unknown local reference field"))?
                    .ty;
                let b = &p.records[target]
                    .fields
                    .get(right)
                    .ok_or_else(|| invalid("unknown target reference field"))?
                    .ty;
                let a = if let Type::Option(t) = a {
                    t.as_ref()
                } else {
                    a
                };
                if a != b || !targets.insert(right.clone()) {
                    return Err(invalid("reference types/targets do not match"));
                }
            }
            if targets != BTreeSet::from(["id".into()])
                && !self.resources[&reference.resource]
                    .unique
                    .iter()
                    .any(|u| u.iter().cloned().collect::<BTreeSet<_>>() == targets)
            {
                return Err(invalid("reference must target a declared unique key"));
            }
            if let (Some(a), Some(b)) = (&r.scope, &self.resources[&reference.resource].scope)
                && reference.fields.get(a) != Some(b)
            {
                return Err(invalid(&format!(
                    "{name}.{reference_name} scoped reference must bind both resources' scopes"
                )));
            }
        }
        Ok(())
    }
    fn check_lookup(&self, name: &str, l: &Lookup, p: &CheckedProgram) -> Result<()> {
        if !crate::project::resolve::identifier(name)
            || l.purpose.is_empty()
            || l.purpose.len() > 256
        {
            return Err(invalid(
                "lookup requires a valid name and bounded explicit purpose",
            ));
        }
        let record = p
            .resources
            .get(&l.resource)
            .and_then(|n| p.records.get(n))
            .ok_or_else(|| invalid("unknown lookup relation"))?;
        if record.fields.get(&l.principal).map(|f| &f.ty) != Some(&Type::Id("user".into()))
            || !matches!(
                record.fields.get(&l.scope).map(|f| &f.ty),
                Some(Type::Id(_))
            )
        {
            return Err(invalid("lookup scope/principal fields invalid"));
        }
        if let Some(role) = &l.role {
            let t = &record
                .fields
                .get(role)
                .ok_or_else(|| invalid("unknown lookup role"))?
                .ty;
            if !matches!(t, Type::Enum(_) | Type::BoundedText(..)) || !scalar(t, p) {
                return Err(invalid("lookup roles need a unit enum or bounded Text"));
            }
        }
        let resource = &self.resources[&l.resource];
        if l.scope != "id"
            && !resource
                .indexes
                .iter()
                .chain(&resource.unique)
                .any(|x| x.starts_with(&[l.scope.clone(), l.principal.clone()]))
        {
            return Err(invalid(
                "membership lookup requires an index beginning with scope and principal",
            ));
        }
        Ok(())
    }
    fn check_join(&self, name: &str, j: &Join, p: &CheckedProgram) -> Result<()> {
        if !crate::project::resolve::identifier(name) {
            return Err(invalid("invalid join name"));
        }
        let r = self
            .resources
            .get(&j.resource)
            .ok_or_else(|| invalid("unknown join resource"))?;
        let reference = r
            .references
            .get(&j.reference)
            .ok_or_else(|| invalid("join requires a declared relationship"))?;
        let output = p
            .records
            .get(&j.output)
            .ok_or_else(|| invalid("unknown join output record"))?;
        if output.fields.len() != 2
            || output.fields.get("left").map(|f| &f.ty)
                != Some(&Type::Record(p.resources[&j.resource].clone()))
            || output.fields.get("right").map(|f| &f.ty)
                != Some(&Type::Record(p.resources[&reference.resource].clone()))
        {
            return Err(invalid(
                "join output must contain exactly typed left/right records",
            ));
        }
        Ok(())
    }
    fn visit(
        &self,
        n: &str,
        active: &mut BTreeSet<String>,
        done: &mut BTreeSet<String>,
    ) -> Result<()> {
        if done.contains(n) {
            return Ok(());
        }
        if !active.insert(n.into()) {
            return Err(invalid("cyclic relational references are unsupported"));
        }
        for r in self.resources[n].references.values() {
            self.visit(&r.resource, active, done)?;
        }
        active.remove(n);
        done.insert(n.into());
        Ok(())
    }
}

fn key_bytes(t: &Type, p: &CheckedProgram) -> Option<usize> {
    match t {
        Type::Option(t) => key_bytes(t, p),
        Type::Text => None,
        Type::BoundedText(_, max) => max.checked_mul(8),
        Type::Id(_) => Some(128),
        Type::Enum(n) => p.enums[n]
            .variants
            .keys()
            .map(|n| crate::project::resolve::public_name(n).len())
            .max(),
        _ => Some(8),
    }
}
