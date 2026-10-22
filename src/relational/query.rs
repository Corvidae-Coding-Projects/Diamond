//! Finite, statically checked relational query shapes and bounded SQL execution.
use super::{
    invalid,
    schema::table,
    types::{Parameters, decoded, quote},
};
use crate::{
    error::{Error, Result},
    hir::CheckedProgram,
    policy::Action,
    syntax::{Expr, ExprKind},
    value::{Type, Value, ValueBudget, ValueKind},
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write,
};
pub(crate) struct Shape {
    pub arguments: Vec<Type>,
    pub reads: BTreeSet<String>,
    pub result: Type,
}
pub(crate) fn filter(name: &str) -> (&str, &str) {
    for (suffix, op) in [
        ("__gte", ">="),
        ("__lte", "<="),
        ("__gt", ">"),
        ("__lt", "<"),
    ] {
        if let Some(field) = name.strip_suffix(suffix) {
            return (field, op);
        }
    }
    (name, "IS NOT DISTINCT FROM")
}
fn ordered(t: &Type) -> bool {
    matches!(
        t,
        Type::Int
            | Type::Decimal
            | Type::Timestamp
            | Type::Duration
            | Type::Text
            | Type::BoundedText(..)
            | Type::Id(_)
    )
}
fn selector(args: &[(Option<String>, Expr)], name: &str, default: &str) -> Result<String> {
    let Some((_, expr)) = args.iter().find(|(n, _)| n.as_deref() == Some(name)) else {
        return Ok(default.into());
    };
    if let ExprKind::Text(s) = &expr.kind {
        Ok(s.clone())
    } else {
        Err(invalid("query selector must be a Text literal"))
    }
}
pub(crate) fn checked(
    p: &CheckedProgram,
    r: &str,
    method: &str,
    args: &[(Option<String>, Expr)],
) -> Result<Shape> {
    let storage = p.storage.as_ref().ok_or(Error::Denied)?;
    let name = &p.resources[r];
    let record = &p.records[name];
    let mut reads = BTreeSet::from([r.into()]);
    let mut output = name.clone();
    if method == "join" {
        let rel = selector(args, "relation", "")?;
        let j = storage
            .joins
            .get(&rel)
            .ok_or_else(|| invalid("join requires a declared relation literal"))?;
        if j.resource != r {
            return Err(invalid("join relation does not start at this resource"));
        }
        reads.insert(
            storage.resources[r].references[&j.reference]
                .resource
                .clone(),
        );
        output.clone_from(&j.output);
    }
    check_reads(p, &reads)?;
    let order = selector(args, "order", "id")?;
    if !record.fields.get(&order).is_some_and(|f| ordered(&f.ty)) {
        return Err(invalid(
            "ordering requires a nonoptional ordered scalar field",
        ));
    }
    let mut seen = BTreeSet::new();
    let mut arguments = Vec::new();
    let mut eq = BTreeSet::new();
    let mut ranges = BTreeSet::new();
    for (key, _) in args {
        let key = key
            .as_deref()
            .ok_or_else(|| invalid("relational queries require named arguments"))?;
        if !seen.insert(key) {
            return Err(invalid("duplicate query argument"));
        }
        let ty = match key {
            "limit" if ["select", "page", "join"].contains(&method) => Type::Int,
            "offset" if method == "select" => Type::Int,
            "order" if ["page", "join", "cursor"].contains(&method) => {
                selector(args, "order", "id")?;
                Type::Text
            }
            "descending" if ["page", "join", "cursor"].contains(&method) => Type::Bool,
            "after" if ["page", "join"].contains(&method) => Type::Text,
            "relation" if method == "join" => Type::Text,
            "row" if method == "cursor" => Type::Record(name.clone()),
            "field" if method == "sum" => {
                let f = selector(args, "field", "")?;
                if record.fields.get(&f).map(|v| &v.ty) != Some(&Type::Int) {
                    return Err(invalid("sum requires a declared Int field literal"));
                }
                Type::Text
            }
            _ if method != "cursor" => {
                let (field, op) = filter(key);
                let ty = &record
                    .fields
                    .get(field)
                    .ok_or_else(|| invalid("unknown query argument or field"))?
                    .ty;
                if op == "IS NOT DISTINCT FROM" {
                    eq.insert(field);
                } else {
                    if !ordered(ty) {
                        return Err(invalid("range requires a nonoptional ordered scalar"));
                    }
                    ranges.insert(field);
                }
                ty.clone()
            }
            _ => return Err(invalid("unknown cursor argument")),
        };
        arguments.push(ty);
    }
    if ["select", "page", "join"].contains(&method) && !seen.contains("limit")
        || method == "cursor" && !seen.contains("row")
        || method == "sum" && !seen.contains("field")
    {
        return Err(invalid(
            "query requires its explicit limit/row/field argument",
        ));
    }
    if method != "cursor" {
        check_indexes(&storage.resources[r], method, &order, &eq, &ranges)?;
    }
    Ok(Shape {
        arguments,
        reads,
        result: match method {
            "count" | "sum" => Type::Int,
            "cursor" => Type::Text,
            _ => Type::List(Box::new(Type::Record(output))),
        },
    })
}
pub(crate) fn projection(p: &CheckedProgram, r: &str, alias: &str) -> String {
    p.records[&p.resources[r]]
        .fields
        .keys()
        .map(|n| format!("{alias}.{}::text", quote(n)))
        .collect::<Vec<_>>()
        .join(",")
}
pub(crate) fn read(
    row: &tokio_postgres::Row,
    start: usize,
    p: &CheckedProgram,
    r: &str,
    budget: &mut ValueBudget,
) -> Result<Value> {
    let name = &p.resources[r];
    let mut fields = BTreeMap::new();
    for (index, (field, spec)) in p.records[name].fields.iter().enumerate() {
        fields.insert(
            field.clone(),
            decoded(
                row.try_get::<_, Option<String>>(start + index)
                    .map_err(|_| invalid("invalid stored row"))?,
                &spec.ty,
                p,
            )?,
        );
    }
    crate::data::validate_record(&p.records[name], &fields)?;
    let value = Value(ValueKind::Record(name.clone(), fields));
    budget.charge(&value)?;
    Ok(value)
}
#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    version: u32,
    resource: String,
    order: String,
    descending: bool,
    key: serde_json::Value,
    id: String,
}
fn cursor(p: &CheckedProgram, r: &str, named: &BTreeMap<&str, &Value>) -> Result<Value> {
    let order = text(named, "order", "id")?;
    let descending = boolean(named, "descending")?;
    let Some(Value(ValueKind::Record(_, fields))) = named.get("row").copied() else {
        return Err(Error::Denied);
    };
    let key = crate::project::wire::encode(fields.get(order).ok_or(Error::Denied)?)?;
    let id = fields["id"].display_text()?;
    let c = Cursor {
        version: 1,
        resource: r.into(),
        order: order.into(),
        descending,
        key,
        id,
    };
    let json = serde_json::to_string(&c).map_err(|_| invalid("cursor encoding"))?;
    let value = Value(ValueKind::Text(super::types::text_hex(&json)));
    let _ = p;
    Ok(value)
}
fn text<'a>(named: &BTreeMap<&str, &'a Value>, key: &str, default: &'a str) -> Result<&'a str> {
    match named.get(key).copied() {
        None => Ok(default),
        Some(Value(ValueKind::Text(s))) => Ok(s),
        _ => Err(invalid("invalid query Text argument")),
    }
}
fn boolean(named: &BTreeMap<&str, &Value>, key: &str) -> Result<bool> {
    match named.get(key).copied() {
        None => Ok(false),
        Some(Value(ValueKind::Bool(v))) => Ok(*v),
        _ => Err(invalid("invalid query Boolean argument")),
    }
}
pub(crate) fn execute(
    context: &mut super::runtime::Context<'_, '_>,
    r: &str,
    method: &str,
    args: &[(Option<String>, Expr)],
    values: &[Value],
    budget: &mut ValueBudget,
) -> Result<Value> {
    let super::runtime::Context {
        connection: c,
        program: p,
        app,
        scope,
    } = context;
    let named = args
        .iter()
        .zip(values)
        .map(|((n, _), v)| Ok((n.as_deref().ok_or(Error::Denied)?, v)))
        .collect::<Result<BTreeMap<_, _>>>()?;
    let shape = checked(p, r, method, args)?;
    for read in shape.reads {
        scope.require(&crate::authority::Effect::Read(read))?;
    }
    if method == "cursor" {
        return cursor(p, r, &named);
    }
    let mut params = Parameters::default();
    let mut conditions =
        vec![p.policies[r].postgres(Action::Read, scope.principal(), "a", &mut params, p, app)?];
    conditions.extend(filters(p, r, &named, &mut params)?);
    let order = text(&named, "order", "id")?;
    let descending = boolean(&named, "descending")?;
    let direction = if descending { "DESC" } else { "ASC" };
    append_cursor(
        p,
        r,
        &named,
        order,
        descending,
        &mut params,
        &mut conditions,
    )?;
    let Sources {
        from,
        fields,
        right,
    } = sources(
        p,
        app,
        r,
        if method == "join" {
            Some(text(&named, "relation", "")?)
        } else {
            None
        },
        scope.principal(),
        &mut params,
        &mut conditions,
    )?;
    let where_sql = conditions.join(" AND ");
    if method == "count" || method == "sum" {
        return aggregate(c, method, &named, &from, &where_sql, &params);
    }
    let Some(Value(ValueKind::Int(limit @ 1..=100))) = named.get("limit").copied() else {
        return Err(invalid("query limit must be 1..100"));
    };
    let offset = match named.get("offset").copied() {
        None => 0,
        Some(Value(ValueKind::Int(n @ 0..=1000))) => *n,
        _ => return Err(invalid("query offset must be 0..1000")),
    };
    let rows=c.relational_query(&format!("SELECT {fields} FROM {from} WHERE {where_sql} ORDER BY a.{} {direction},a.\"id\" {direction} LIMIT {limit} OFFSET {offset}",quote(order)),&params.bindings())?;
    let mut output = Vec::new();
    for row in rows {
        let left = read(&row, 0, p, r, budget)?;
        let value = if let Some((right, output)) = &right {
            let right = read(
                &row,
                p.records[&p.resources[r]].fields.len(),
                p,
                right,
                budget,
            )?;
            Value(ValueKind::Record(
                output.clone(),
                BTreeMap::from([("left".into(), left), ("right".into(), right)]),
            ))
        } else {
            left
        };
        output.push(value);
    }
    let value = Value(ValueKind::List(output));
    budget.charge(&value)?;
    Ok(value)
}

fn check_indexes(
    config: &super::config::Resource,
    method: &str,
    order: &str,
    eq: &BTreeSet<&str>,
    ranges: &BTreeSet<&str>,
) -> Result<()> {
    let indexes = config
        .indexes
        .iter()
        .chain(&config.unique)
        .cloned()
        .chain(std::iter::once(vec!["id".into()]));
    let supported = indexes.clone().any(|i| {
        i.first()
            .is_some_and(|f| eq.contains(f.as_str()) || ranges.contains(f.as_str()))
    }) || eq.is_empty() && ranges.is_empty();
    if !supported {
        return Err(invalid("query filters require a declared index prefix"));
    }
    if ["page", "join"].contains(&method)
        && !indexes.clone().any(|i| {
            i.iter()
                .take_while(|f| eq.contains(f.as_str()) && f.as_str() != order)
                .count()
                < i.len()
                && i.iter()
                    .find(|f| !eq.contains(f.as_str()) || f.as_str() == order)
                    .is_some_and(|f| f == order)
        })
    {
        return Err(invalid(
            "page ordering requires an index after its equality prefix",
        ));
    }
    if ranges.iter().any(|f| {
        !indexes.clone().any(|i| {
            i.iter()
                .find(|k| !eq.contains(k.as_str()))
                .is_some_and(|k| k == f)
        })
    }) {
        return Err(invalid(
            "range filters require a declared index after their equality prefix",
        ));
    }
    Ok(())
}

fn append_cursor(
    p: &CheckedProgram,
    r: &str,
    named: &BTreeMap<&str, &Value>,
    order: &str,
    descending: bool,
    params: &mut Parameters,
    conditions: &mut Vec<String>,
) -> Result<()> {
    let after = text(named, "after", "")?;
    if !after.is_empty() {
        if after.len() > 16_384 {
            return Err(invalid("cursor size"));
        }
        let json = super::types::unhex(after)?;
        let cursor: Cursor = serde_json::from_str(&json).map_err(|_| invalid("invalid cursor"))?;
        if cursor.version != 1
            || cursor.resource != r
            || cursor.order != order
            || cursor.descending != descending
            || !crate::auth::valid_id(&cursor.id)
        {
            return Err(invalid("cursor query mismatch"));
        }
        let ty = &p.records[&p.resources[r]].fields[order].ty;
        let key = crate::project::wire::decode(ty, &cursor.key, p)?;
        let key = params.add(&key, ty)?;
        let id = params.raw(&cursor.id);
        conditions.push(format!(
            "(a.{},a.\"id\") {} ({key},{id})",
            quote(order),
            if descending { "<" } else { ">" }
        ));
    }
    Ok(())
}

struct Sources {
    from: String,
    fields: String,
    right: Option<(String, String)>,
}
fn sources(
    p: &CheckedProgram,
    app: &str,
    r: &str,
    relation: Option<&str>,
    principal: &crate::authority::Principal,
    params: &mut Parameters,
    conditions: &mut Vec<String>,
) -> Result<Sources> {
    let mut from = format!("{} AS a", table(app, r));
    let mut fields = projection(p, r, "a");
    let mut right = None;
    if let Some(relation) = relation {
        let j = &p.storage.as_ref().ok_or(Error::Denied)?.joins[relation];
        let reference =
            &p.storage.as_ref().ok_or(Error::Denied)?.resources[r].references[&j.reference];
        write!(
            from,
            " JOIN {} AS b ON {}",
            table(app, &reference.resource),
            reference
                .fields
                .iter()
                .map(|(a, b)| format!("a.{} = b.{}", quote(a), quote(b)))
                .collect::<Vec<_>>()
                .join(" AND ")
        )
        .map_err(|_| invalid("query formatting"))?;
        conditions.push(p.policies[&reference.resource].postgres(
            Action::Read,
            principal,
            "b",
            params,
            p,
            app,
        )?);
        fields.push(',');
        fields.push_str(&projection(p, &reference.resource, "b"));
        right = Some((reference.resource.clone(), j.output.clone()));
    }
    Ok(Sources {
        from,
        fields,
        right,
    })
}

fn check_reads(p: &CheckedProgram, reads: &BTreeSet<String>) -> Result<()> {
    for read in reads {
        if !p.policies.get(read).is_some_and(|q| q.has(Action::Read)) {
            return Err(invalid("query requires every base relation's read policy"));
        }
    }
    Ok(())
}

fn aggregate(
    c: &mut crate::host::HostRuntime,
    method: &str,
    named: &BTreeMap<&str, &Value>,
    from: &str,
    where_sql: &str,
    params: &Parameters,
) -> Result<Value> {
    let aggregate = if method == "count" {
        "count(*)".into()
    } else {
        format!("COALESCE(sum(a.{}),0)", quote(text(named, "field", "")?))
    };
    let rows = c.relational_query(
        &format!("SELECT ({aggregate})::text FROM {from} WHERE {where_sql}"),
        &params.bindings(),
    )?;
    let text: String = rows[0].try_get(0).map_err(|_| Error::Postgres)?;
    let count = text
        .parse::<i64>()
        .map_err(|_| invalid("aggregate exceeds Int range"))?;
    Ok(Value(ValueKind::Int(count)))
}

fn filters(
    p: &CheckedProgram,
    r: &str,
    named: &BTreeMap<&str, &Value>,
    params: &mut Parameters,
) -> Result<Vec<String>> {
    let mut conditions = Vec::new();
    for (key, value) in named {
        if [
            "limit",
            "offset",
            "order",
            "descending",
            "after",
            "relation",
            "field",
        ]
        .contains(key)
        {
            continue;
        }
        let (field, op) = filter(key);
        let ty = &p.records[&p.resources[r]].fields[field].ty;
        let condition = if op == "IS NOT DISTINCT FROM" {
            if matches!(value.0, ValueKind::None) {
                format!("a.{} IS NULL", quote(field))
            } else {
                format!("a.{} = {}", quote(field), params.add(value, ty)?)
            }
        } else {
            format!("a.{} {op} {}", quote(field), params.add(value, ty)?)
        };
        conditions.push(condition);
    }
    Ok(conditions)
}
