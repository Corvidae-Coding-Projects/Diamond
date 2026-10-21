//! JavaScript emission from the checked scalar component IR.

use super::lower::{Attribute, BrowserProgram, Expression, Instruction, Kind, Node, Scalar};
use crate::error::{Error, Result};
use std::collections::BTreeMap;
use std::fmt::Write;

const RUNTIME: &str = r"// Diamond scalar browser runtime, artifact format 1.
const __diamond_rt_root = document.getElementById('diamond-app');
const __diamond_rt_error = document.getElementById('diamond-error');
let __diamond_rt_fuel = 100000, __diamond_rt_depth = 0;
function __diamond_rt_step() { if (--__diamond_rt_fuel < 0) throw Error('evaluation budget exceeded'); }
function __diamond_rt_int(v) { if (v < -9223372036854775808n || v > 9223372036854775807n) throw Error('integer overflow'); return v; }
function __diamond_rt_text(v) { if (new TextEncoder().encode(v).length > 1048576) throw Error('text budget exceeded'); return v; }
function __diamond_rt_len(v) { return BigInt(Array.from(v).length); }
function __diamond_rt_compare(a,b) {
  if (typeof a !== 'string') return a < b ? -1 : a > b ? 1 : 0;
  const x=Array.from(a), y=Array.from(b);
  for (let i=0; i<Math.min(x.length,y.length); ++i) {
    const d=x[i].codePointAt(0)-y[i].codePointAt(0); if (d) return d < 0 ? -1 : 1;
  }
  return x.length < y.length ? -1 : x.length > y.length ? 1 : 0;
}
function __diamond_rt_element(tag,attributes,children) {
  const element=document.createElement(tag);
  for (const [name,value] of attributes) {
    if (name === 'on-click') element.addEventListener('click',()=>__diamond_rt_dispatch(value));
    else element.setAttribute(name,String(value));
  }
  element.append(...children); return element;
}
function __diamond_rt_fail() { __diamond_rt_error.hidden=false; __diamond_rt_error.textContent='Diamond component update failed; previous state was preserved.'; }
let __diamond_rt_state=Object.create(null);
const __diamond_rt_events=Object.create(null);
function __diamond_rt_dispatch(name) {
  __diamond_rt_fuel=100000;
  const __diamond_rt_next=Object.assign(Object.create(null),__diamond_rt_state);
  try {
    __diamond_rt_events[name](__diamond_rt_next);
    const __diamond_rt_previous=__diamond_rt_state;
    __diamond_rt_state=__diamond_rt_next;
    try { __diamond_rt_render(); } catch (error) { __diamond_rt_state=__diamond_rt_previous; throw error; }
    __diamond_rt_error.hidden=true;
  } catch (_) { __diamond_rt_fail(); }
}
";

pub(super) fn emit(program: &BrowserProgram) -> Result<String> {
    let mut output = RUNTIME.to_owned();
    for (name, function) in &program.functions {
        let parameters = function
            .parameters
            .iter()
            .map(|(name, _)| local(name))
            .collect::<Vec<_>>()
            .join(",");
        let mut bindings: BTreeMap<_, _> = function
            .parameters
            .iter()
            .map(|(name, _)| (name.clone(), local(name)))
            .collect();
        writeln!(output,"function {name}({parameters}) {{ __diamond_rt_step(); if (__diamond_rt_depth >= 64) throw Error('call depth exceeded'); ++__diamond_rt_depth; try {{").map_err(write_error)?;
        output.push_str("let __diamond_rt_result;\n");
        for instruction in &function.body {
            match instruction {
                Instruction::Let(name, value) => {
                    let value = expression(value, &bindings)?;
                    let variable = local(name);
                    writeln!(output, "const {variable}={value};").map_err(write_error)?;
                    bindings.insert(name.clone(), variable);
                }
                Instruction::Value(value) => writeln!(
                    output,
                    "__diamond_rt_result={};",
                    expression(value, &bindings)?
                )
                .map_err(write_error)?,
                Instruction::Return(value) => {
                    writeln!(output, "return {};", expression(value, &bindings)?)
                        .map_err(write_error)?;
                }
            }
        }
        output.push_str("return __diamond_rt_result;\n} finally { --__diamond_rt_depth; } }\n");
    }
    let state = bindings(program, "__diamond_rt_state")?;
    let next = bindings(program, "__diamond_rt_next")?;
    for (name, assignments) in &program.events {
        writeln!(
            output,
            "__diamond_rt_events[{}]=function(__diamond_rt_next) {{",
            quote(name)?
        )
        .map_err(write_error)?;
        for (target, value) in assignments {
            writeln!(
                output,
                "__diamond_rt_next[{}]={};",
                quote(target)?,
                expression(value, &next)?
            )
            .map_err(write_error)?;
        }
        output.push_str("};\n");
    }
    output.push_str("function __diamond_rt_render() { __diamond_rt_fuel=100000; const children=[");
    for node in &program.template {
        writeln!(output, "{},", node_js(node, &state)?).map_err(write_error)?;
    }
    output.push_str("]; __diamond_rt_root.replaceChildren(...children); }\ntry {\n");
    for (name, value) in &program.state {
        writeln!(
            output,
            "__diamond_rt_state[{}]={};",
            quote(name)?,
            expression(value, &BTreeMap::new())?
        )
        .map_err(write_error)?;
    }
    output.push_str("__diamond_rt_render(); __diamond_rt_root.dataset.ready='true';\n} catch (_) { __diamond_rt_fail(); }\n");
    if output.len() > super::MAX_PROJECT_BYTES {
        return Err(Error::Limit("browser artifact"));
    }
    Ok(output)
}

fn bindings(program: &BrowserProgram, object: &str) -> Result<BTreeMap<String, String>> {
    program
        .state
        .iter()
        .map(|(name, _)| Ok((name.clone(), format!("{object}[{}]", quote(name)?))))
        .collect()
}

fn expression(value: &Expression, bindings: &BTreeMap<String, String>) -> Result<String> {
    let code = match &value.kind {
        Kind::Int(value) => format!("{value}n"),
        Kind::Text(value) => quote(value)?,
        Kind::Bool(value) => value.to_string(),
        Kind::Name(name) => bindings
            .get(name)
            .cloned()
            .ok_or_else(|| Error::Input("unresolved typed browser local".into()))?,
        Kind::Unary(operator, value) => {
            let value = expression(value, bindings)?;
            if operator == "not" {
                format!("(!({value}))")
            } else {
                format!("__diamond_rt_int(-({value}))")
            }
        }
        Kind::Binary(a, operator, b) => binary(a, operator, b, bindings)?,
        Kind::Call(name, args) => {
            let name = if name == "len" {
                "__diamond_rt_len"
            } else {
                name
            };
            let args = args
                .iter()
                .map(|value| expression(value, bindings))
                .collect::<Result<Vec<_>>>()?
                .join(",");
            format!("{name}({args})")
        }
    };
    Ok(format!("(__diamond_rt_step(),{code})"))
}

fn binary(
    a: &Expression,
    operator: &str,
    b: &Expression,
    bindings: &BTreeMap<String, String>,
) -> Result<String> {
    let left = expression(a, bindings)?;
    let right = expression(b, bindings)?;
    let sign = match operator {
        "add" => "+",
        "subtract" => "-",
        "multiply" => "*",
        "divide" => "/",
        "equal" => "===",
        "not_equal" => "!==",
        "and" => "&&",
        "or" => "||",
        "less" => "<",
        "less_equal" => "<=",
        "greater" => ">",
        "greater_equal" => ">=",
        _ => return Err(Error::Denied),
    };
    let simple = format!("(({left}){sign}({right}))");
    Ok(match operator {
        "add" if a.ty == Scalar::Text => format!("__diamond_rt_text({simple})"),
        "add" | "subtract" | "multiply" | "divide" => format!("__diamond_rt_int({simple})"),
        "less" | "less_equal" | "greater" | "greater_equal" => {
            format!("(__diamond_rt_compare({left},{right}){sign}0)")
        }
        _ => simple,
    })
}

fn node_js(node: &Node, bindings: &BTreeMap<String, String>) -> Result<String> {
    match node {
        Node::Text(text) => Ok(format!("document.createTextNode({})", quote(text)?)),
        Node::Value(value) => Ok(format!(
            "document.createTextNode(String({}))",
            expression(value, bindings)?
        )),
        Node::Element {
            tag,
            attributes,
            children,
        } => {
            let attributes = attributes
                .iter()
                .map(|(name, value)| {
                    let value = match value {
                        Attribute::Text(text) | Attribute::Event(text) => quote(text)?,
                        Attribute::Value(value) => expression(value, bindings)?,
                    };
                    Ok(format!("[{},{}]", quote(name)?, value))
                })
                .collect::<Result<Vec<_>>>()?
                .join(",");
            let children = children
                .iter()
                .map(|node| node_js(node, bindings))
                .collect::<Result<Vec<_>>>()?
                .join(",");
            Ok(format!(
                "__diamond_rt_element({},[{attributes}],[{children}])",
                quote(tag)?
            ))
        }
    }
}

fn local(name: &str) -> String {
    format!("__diamond_local_{name}")
}
fn quote(value: &str) -> Result<String> {
    serde_json::to_string(value)
        .map_err(|_| Error::Input("browser string serialization failed".into()))
}
fn write_error(_: std::fmt::Error) -> Error {
    Error::Input("browser artifact formatting failed".into())
}
