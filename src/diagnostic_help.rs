//! User-facing explanations for the compiler's stable diagnostic categories.

#[derive(Clone, Copy, Debug)]
pub(crate) struct Explanation {
    pub code: &'static str,
    pub title: &'static str,
    pub remedy: &'static str,
}

const EXPLANATIONS: &[Explanation] = &[
    Explanation {
        code: "D310",
        title: "host operation signature",
        remedy: "Call a registered host operation with its exact argument types and declare its module/application capability budget.",
    },
    Explanation {
        code: "D320",
        title: "reserved compiler symbol",
        remedy: "Use source-level exported names through a declared module import; names beginning __diamond_ are reserved.",
    },
    Explanation {
        code: "D330",
        title: "browser component type or scope",
        remedy: "Use typed scalar state, a declared click event, and pure imported functions in the W1 component subset.",
    },
    Explanation {
        code: "D001",
        title: "tab character",
        remedy: "Replace the tab with spaces; Diamond source uses space indentation.",
    },
    Explanation {
        code: "D002",
        title: "inconsistent indentation",
        remedy: "Align this line with an enclosing block's indentation.",
    },
    Explanation {
        code: "D003",
        title: "empty HTML template",
        remedy: "Add an indented typed-HTML template below `html:`.",
    },
    Explanation {
        code: "D004",
        title: "invalid integer literal",
        remedy: "Use a signed 64-bit integer; underscores may appear singly between digits.",
    },
    Explanation {
        code: "D005",
        title: "invalid string literal",
        remedy: "Close the string and use only `\\n`, `\\r`, `\\t`, `\\\"`, or `\\\\` escapes.",
    },
    Explanation {
        code: "D010",
        title: "source syntax",
        remedy: "Correct the token or structure at the reported source span.",
    },
    Explanation {
        code: "D020",
        title: "typed HTML syntax",
        remedy: "Correct the reported allowlisted HTML structure, tag, or attribute.",
    },
    Explanation {
        code: "D101",
        title: "duplicate declaration",
        remedy: "Give each top-level declaration a unique name.",
    },
    Explanation {
        code: "D102",
        title: "invalid record field",
        remedy: "Use a unique field name and keep server-derived fields out of client forms.",
    },
    Explanation {
        code: "D103",
        title: "invalid type",
        remedy: "Use a declared type with the supported generic shape and bounds.",
    },
    Explanation {
        code: "D110",
        title: "invalid expression",
        remedy: "Use an expression supported by the declared type and current scope.",
    },
    Explanation {
        code: "D111",
        title: "invalid operator",
        remedy: "Use an operator defined for both operand types.",
    },
    Explanation {
        code: "D112",
        title: "unknown value",
        remedy: "Declare the value in this scope or pass it as a supported parameter.",
    },
    Explanation {
        code: "D113",
        title: "unknown field",
        remedy: "Use a field declared on the value's checked record type.",
    },
    Explanation {
        code: "D114",
        title: "invalid field access",
        remedy: "Access only fields supported by this value and authority context.",
    },
    Explanation {
        code: "D115",
        title: "invalid unary operation",
        remedy: "Apply `not` to Bool or unary `-` to Int.",
    },
    Explanation {
        code: "D116",
        title: "invalid function call",
        remedy: "Call a declared function with the required arguments.",
    },
    Explanation {
        code: "D117",
        title: "invalid call arguments",
        remedy: "Match the function's parameter count, names, and checked types.",
    },
    Explanation {
        code: "D118",
        title: "invalid record construction",
        remedy: "Supply each required record field once by name.",
    },
    Explanation {
        code: "D119",
        title: "invalid repository operation",
        remedy: "Use the resource's checked record, ID, and supported operation shape.",
    },
    Explanation {
        code: "D120",
        title: "invalid route use",
        remedy: "Use a declared route handle and supply its typed path parameters.",
    },
    Explanation {
        code: "D121",
        title: "invalid form",
        remedy: "Use a supported scalar form record and exclude protected fields.",
    },
    Explanation {
        code: "D122",
        title: "invalid return value",
        remedy: "Return a value matching the function or route's checked result type.",
    },
    Explanation {
        code: "D123",
        title: "invalid match",
        remedy: "Use supported patterns and cover every variant without duplicates.",
    },
    Explanation {
        code: "D124",
        title: "invalid propagation",
        remedy: "Use `?` only where the surrounding result can carry the same failure.",
    },
    Explanation {
        code: "D125",
        title: "invalid selection",
        remedy: "Use named scalar filters with limit 1–100 and offset 0–10,000.",
    },
    Explanation {
        code: "D200",
        title: "invalid policy",
        remedy: "Declare the resource and a supported action signature.",
    },
    Explanation {
        code: "D201",
        title: "unsafe policy expression",
        remedy: "Keep policy expressions within the restricted scalar, principal, and record-field subset.",
    },
    Explanation {
        code: "D204",
        title: "policy-controlled mutation",
        remedy: "Add the required policy action or remove the mutation; application grants cannot replace policy.",
    },
    Explanation {
        code: "D301",
        title: "missing effect",
        remedy: "Declare the exact application effect required by this call path.",
    },
    Explanation {
        code: "D302",
        title: "invalid transaction",
        remedy: "Use a database-enabled app and avoid nested or transitively nested transactions.",
    },
    Explanation {
        code: "D401",
        title: "route conflict",
        remedy: "Give each method and normalized route shape a single handler.",
    },
    Explanation {
        code: "D402",
        title: "invalid route path",
        remedy: "Use an absolute literal path with supported typed parameters.",
    },
    Explanation {
        code: "D403",
        title: "route parameter mismatch",
        remedy: "Make route parameters and handler-visible names agree.",
    },
    Explanation {
        code: "D405",
        title: "reserved route",
        remedy: "Move source routes outside the runtime-owned `/_diamond/` namespace.",
    },
    Explanation {
        code: "D900",
        title: "unsupported feature",
        remedy: "Use the documented MVP subset or remove the unsupported operation.",
    },
];

pub(crate) fn lookup(input: &str) -> Option<Explanation> {
    let canonical = input.to_ascii_uppercase();
    if canonical.len() != 4
        || !canonical.starts_with('D')
        || !canonical[1..].bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    EXPLANATIONS
        .iter()
        .copied()
        .find(|explanation| explanation.code == canonical)
}
