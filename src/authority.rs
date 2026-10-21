//! Explicit request authority. Effects describe requirements and never mint grants.

use crate::error::{Error, Result};
use std::collections::BTreeSet;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Effect {
    Read(String),
    Write(String),
    Host(String),
}

pub(crate) type Effects = BTreeSet<Effect>;

#[derive(Clone, Debug, Default)]
pub(crate) enum Principal {
    #[default]
    Anonymous,
    User {
        id: String,
        roles: BTreeSet<String>,
    },
}

impl Principal {
    pub fn id(&self) -> Option<&str> {
        match self {
            Self::Anonymous => None,
            Self::User { id, .. } => Some(id),
        }
    }
    pub fn has_role(&self, role: &str) -> bool {
        match self {
            Self::Anonymous => false,
            Self::User { roles, .. } => roles.contains(role),
        }
    }
}

/// A borrowed scope cannot outlive its principal or its host-provided grants.
/// It is not a Diamond value and has no serialization or cloning implementation.
#[derive(Debug)]
pub(crate) struct Scope<'a> {
    principal: &'a Principal,
    grants: &'a Effects,
}

impl<'a> Scope<'a> {
    pub fn new(principal: &'a Principal, grants: &'a Effects) -> Self {
        Self { principal, grants }
    }
    pub fn principal(&self) -> &Principal {
        self.principal
    }
    pub fn require(&self, effect: &Effect) -> Result<()> {
        if self.grants.contains(effect) {
            Ok(())
        } else {
            Err(Error::Denied)
        }
    }
}

pub(crate) fn random_hex() -> Result<String> {
    use std::fmt::Write;
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|_| Error::Random)?;
    let mut output = String::with_capacity(64);
    for byte in bytes {
        write!(output, "{byte:02x}").map_err(|_| Error::Random)?;
    }
    Ok(output)
}
