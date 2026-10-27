//! Checked reactive input schemas shared by public rendering and browser codecs.

mod routes;

use super::{Module, Target, input};
use crate::error::{Error, Result};
use crate::hir::CheckedProgram;
use crate::scalar_input::Scalar;
use crate::value::{Type, Value};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Serialize)]
pub(super) struct Field {
    pub ty: Type,
    pub codec: Scalar,
    pub default: Option<String>,
}

impl Field {
    fn checked(field: &crate::hir::Field, program: &CheckedProgram) -> Result<Self> {
        let codec = Scalar::checked(&field.ty, program)
            .filter(|_| !field.created_by)
            .ok_or_else(|| input("input fields require Text, Int, Bool, nominal Id, unit enum, or one-level Option; server-derived/restricted fields are forbidden"))?;
        let default = field
            .default
            .as_ref()
            .map(|value| {
                codec
                    .format(value)
                    .ok_or_else(|| input("invalid input field default"))
            })
            .transpose()?;
        Ok(Self {
            ty: field.ty.clone(),
            codec,
            default,
        })
    }

    fn absent(&self) -> Option<Value> {
        self.default
            .as_deref()
            .or_else(|| matches!(self.codec, Scalar::Optional { .. }).then_some(""))
            .and_then(|raw| self.codec.decode(raw).ok())
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub(super) struct Schemas {
    pub routes: BTreeMap<String, routes::Route>,
    pub forms: BTreeMap<String, BTreeMap<String, Field>>,
}

impl Schemas {
    pub(super) fn checked(
        modules: &BTreeMap<String, Module>,
        program: &CheckedProgram,
    ) -> Result<Self> {
        let mut schemas = Self::default();
        for module in modules.values() {
            if (!module.parsed.navigation.is_empty() || !module.parsed.forms.is_empty())
                && module.config.target != Target::Shared
            {
                return Err(input(
                    "reactive route/form schemas belong to shared modules",
                ));
            }
            for route in &module.parsed.navigation {
                let record = program
                    .records
                    .get(&route.name)
                    .ok_or_else(|| input("route schema must be imported by the browser"))?;
                let checked = routes::Route::checked(route, record, program)?;
                if schemas.routes.values().any(|old| old.overlaps(&checked)) {
                    return Err(Error::diagnostic(
                        "D331",
                        route.location,
                        "ambiguous named route patterns",
                    ));
                }
                schemas.routes.insert(route.name.clone(), checked);
            }
            for (name, location) in &module.parsed.forms {
                let record = program
                    .records
                    .get(name)
                    .ok_or_else(|| input("form schema must be imported by the browser"))?;
                if record.fields.len() > 32 || record.fields.keys().any(|name| !field_name(name)) {
                    return Err(Error::diagnostic(
                        "D332",
                        *location,
                        "invalid/reserved form fields (at most 32)",
                    ));
                }
                let fields = record
                    .fields
                    .iter()
                    .map(|(name, field)| Ok((name.clone(), Field::checked(field, program)?)))
                    .collect::<Result<_>>()?;
                schemas.forms.insert(name.clone(), fields);
            }
        }
        if schemas.routes.len() > 64 || schemas.forms.len() > 64 {
            return Err(Error::Limit("reactive schemas"));
        }
        Ok(schemas)
    }

    pub(super) fn match_path(&self, target: &str) -> Option<(String, Value)> {
        self.routes.iter().find_map(|(name, route)| {
            route
                .decode(name, target)
                .map(|value| (name.clone(), value))
        })
    }

    pub(super) fn url(&self, value: &Value) -> Result<String> {
        let crate::value::ValueKind::Record(name, fields) = &value.0 else {
            return Err(input("typed route value required"));
        };
        self.routes
            .get(name)
            .ok_or_else(|| input("unknown typed route"))?
            .url(fields)
            .ok_or_else(|| input("noncanonical typed route values or URL budget"))
    }

    pub(super) fn check_literals(&self, paths: &BTreeSet<String>) -> Result<()> {
        if paths.iter().any(|path| {
            self.routes
                .values()
                .any(|route| route.overlaps_literal(path))
        }) {
            return Err(input("literal and named browser routes overlap"));
        }
        Ok(())
    }
}

fn field_name(name: &str) -> bool {
    super::resolve::identifier(name)
        && !name.starts_with('_')
        && !["constructor", "prototype", "__proto__"].contains(&name)
}
