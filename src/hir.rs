//! Sealed checked-program representation shared by runtime adapters.

use crate::authority::Effects;
use crate::error::Location;
use crate::policy::Policy;
use crate::syntax::{App, Statement};
use crate::value::{Type, Value};
use std::collections::BTreeMap;
use std::fmt::Write;

#[derive(Clone, Debug)]
pub(crate) struct Field {
    pub ty: Type,
    pub default: Option<Value>,
    pub created_by: bool,
}

#[derive(Clone, Debug)]
pub(crate) struct Record {
    pub fields: BTreeMap<String, Field>,
}

#[derive(Clone, Debug)]
pub(crate) struct Function {
    pub parameters: Vec<(String, Type)>,
    pub result: Type,
    pub body: Vec<Statement>,
    pub effects: Effects,
    pub opens_transaction: bool,
    pub location: Location,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct Enum {
    pub variants: BTreeMap<String, Vec<(String, Type)>>,
}

#[derive(Clone, Debug)]
pub(crate) enum Segment {
    Literal(String),
    Parameter(String, Type),
}

#[derive(Clone, Debug)]
pub(crate) struct Route {
    pub public: bool,
    pub method: String,
    pub path: String,
    pub name: Option<String>,
    pub segments: Vec<Segment>,
    pub body: Vec<Statement>,
    pub effects: Effects,
    pub location: Location,
}

/// An executable program that can only be constructed by successful checking.
///
/// ```
/// let program = diamond::compile("fn main() -> Text:\n  \"Diamond\"\n")?;
/// assert!(program.summary().contains("1 functions"));
/// # Ok::<(), diamond::Error>(())
/// ```
#[derive(Clone, Debug)]
pub struct CheckedProgram {
    pub(crate) host_operations: bool,
    pub(crate) durable: BTreeMap<String, (String, Type)>,
    pub(crate) storage: Option<crate::relational::Storage>,
    pub(crate) app: Option<App>,
    pub(crate) aliases: BTreeMap<String, Type>,
    pub(crate) records: BTreeMap<String, Record>,
    pub(crate) enums: BTreeMap<String, Enum>,
    pub(crate) resources: BTreeMap<String, String>,
    pub(crate) functions: BTreeMap<String, Function>,
    pub(crate) routes: Vec<Route>,
    pub(crate) policies: BTreeMap<String, Policy>,
    pub(crate) grants: Effects,
}

impl CheckedProgram {
    pub(crate) fn variant(&self, name: &str) -> Option<(&str, &[(String, Type)])> {
        self.enums.iter().find_map(|(enumeration, definition)| {
            definition
                .variants
                .get(name)
                .map(|fields| (enumeration.as_str(), fields.as_slice()))
        })
    }

    /// Return whether this program declares HTTP routes.
    #[must_use]
    pub fn has_routes(&self) -> bool {
        !self.routes.is_empty()
    }

    /// Describe the checked declarations and supported operation effects.
    #[must_use]
    pub fn summary(&self) -> String {
        let mut result = format!(
            "{} functions, {} records, {} resources, {} routes, {} policies\n",
            self.functions.len(),
            self.records.len(),
            self.resources.len(),
            self.routes.len(),
            self.policies.len()
        );
        if let Some(app) = &self.app {
            // Formatting into an owned String cannot fail.
            let _ = writeln!(result, "  app {}", app.name);
        }
        for route in &self.routes {
            let _ = writeln!(
                result,
                "  {} {} {} {:?}",
                if route.public { "public" } else { "private" },
                route.method,
                route.path,
                route.effects
            );
        }
        result
    }
}
