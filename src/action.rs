//! Managed action outcomes. An outcome is data and never a replay credential.

use crate::value::{Type, Value, ValueKind};
use std::collections::BTreeMap;

pub(crate) const ENUM: &str = "__diamond_action";
pub(crate) const EMPTY: [&str; 9] = [
    "ActionIdle",
    "ActionSubmitting",
    "ActionInvalid",
    "ActionDenied",
    "ActionAborted",
    "ActionUnknown",
    "ActionExpired",
    "ActionConflict",
    "ActionCorrupt",
];

pub(crate) fn constructor(name: &str) -> bool {
    EMPTY.contains(&name) || ["ActionSucceeded", "ActionRejected"].contains(&name)
}

pub(crate) fn cases(success: &Type, error: &Type) -> BTreeMap<String, Vec<Type>> {
    let mut cases: BTreeMap<_, _> = EMPTY.into_iter().map(|n| (n.into(), Vec::new())).collect();
    cases.insert("ActionSucceeded".into(), vec![success.clone()]);
    cases.insert("ActionRejected".into(), vec![error.clone()]);
    cases
}

pub(crate) fn signature(name: &str, arguments: &[Type]) -> Option<Type> {
    let (success, error) = match (name, arguments) {
        (name, []) if EMPTY.contains(&name) => (Type::Never, Type::Never),
        ("ActionSucceeded", [value]) => (value.clone(), Type::Never),
        ("ActionRejected", [error]) => (Type::Never, error.clone()),
        _ => return None,
    };
    Some(Type::Action(Box::new(success), Box::new(error)))
}

pub(crate) fn value(name: &str, fields: Vec<Value>) -> Value {
    Value(ValueKind::Variant(ENUM.into(), name.into(), fields))
}
