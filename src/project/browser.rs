//! Emit checked browser IR, runtime, and privacy-filtered source mappings.
use super::lower::BrowserProgram;
use crate::error::{Error, Result};
use std::fmt::Write;
pub(super) fn emit(
    program: &BrowserProgram,
    contract: &str,
    runtime: &super::admission::Config,
) -> Result<String> {
    let data = serde_json::to_string(program)
        .map_err(|_| Error::Input("browser IR serialization".into()))?;
    let quote = |s: &str| {
        serde_json::to_string(s).map_err(|_| Error::Input("browser string serialization".into()))
    };
    let mut output = format!(
        "const __contract={};\nconst __program=JSON.parse({});\nconst __functions=Object.create(null);\nconst __handlerLocations=Object.create(null);\nconst __handlerLabels=Object.create(null);\n",
        quote(contract)?,
        quote(&data)?
    );
    writeln!(output, "const __runtime={};", runtime.client())
        .map_err(|_| Error::Limit("browser formatting"))?;
    for (name, function) in &program.functions {
        writeln!(
            output,
            "__functions[{}]=(...args)=>__invoke({},args); // diamond-source:{}:{}",
            quote(name)?,
            quote(name)?,
            function.location.line,
            function.location.column
        )
        .map_err(|_| Error::Limit("browser formatting"))?;
    }
    for (component_name, component) in &program.components {
        writeln!(
            output,
            "__handlerLocations[{}]=[{},{}]; // diamond-source:{}:{}",
            quote(component_name)?,
            component.location.line,
            component.location.column,
            component.location.line,
            component.location.column
        )
        .map_err(|_| Error::Limit("browser formatting"))?;
        for (event_name, event) in &component.events {
            let handler = format!("{component_name}.{event_name}");
            let label = format!("{}.{}", public_component_name(component_name), event_name);
            writeln!(
                output,
                "__handlerLocations[{}]=[{},{}]; // diamond-source:{}:{}",
                quote(&handler)?,
                event.location.line,
                event.location.column,
                event.location.line,
                event.location.column
            )
            .map_err(|_| Error::Limit("browser formatting"))?;
            writeln!(
                output,
                "__handlerLabels[{}]={};",
                quote(&handler)?,
                quote(&label)?
            )
            .map_err(|_| Error::Limit("browser formatting"))?;
        }
    }
    output.push_str(include_str!("inputs.js"));
    output.push_str(include_str!("controls.js"));
    output.push_str(include_str!("recovery.js"));
    output.push_str(include_str!("interactions.js"));
    output.push_str(include_str!("runtime.js"));
    if output.len() > super::MAX_PROJECT_BYTES {
        return Err(Error::Limit("browser artifact"));
    }
    Ok(output)
}

fn public_component_name(name: &str) -> &str {
    let Some(rest) = name.strip_prefix("__diamond_component_") else {
        return name;
    };
    let Some((length, module_and_name)) = rest.split_once('_') else {
        return name;
    };
    let Ok(length) = length.parse::<usize>() else {
        return name;
    };
    module_and_name
        .get(length..)
        .and_then(|suffix| suffix.strip_prefix('_'))
        .unwrap_or(name)
}
fn vlq(value: i64) -> String {
    let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut n = value.unsigned_abs() * 2 + u64::from(value < 0);
    let mut out = String::new();
    loop {
        let mut digit = (n & 31) as usize;
        n >>= 5;
        if n != 0 {
            digit |= 32;
        }
        out.push(char::from(alphabet[digit]));
        if n == 0 {
            break;
        }
    }
    out
}
pub(super) fn source_map(project: &super::CheckedProject, js: &str) -> Result<String> {
    let names = super::resolve::reachable(&project.manifest.browser, &project.manifest.modules);
    let modules = names
        .iter()
        .map(|n| &project.modules[n])
        .collect::<Vec<_>>();
    let mut source = 0i64;
    let mut line = 0i64;
    let mut column = 0i64;
    let mut mappings = Vec::new();
    for generated in js.lines() {
        let mut mapping = String::new();
        if let Some((_, raw)) = generated.split_once("// diamond-source:")
            && let Some((l, c)) = raw.split_once(':')
        {
            let original = l
                .parse::<usize>()
                .map_err(|_| Error::Input("source map location".into()))?;
            if let Some((index, module)) = modules.iter().enumerate().find(|(_, m)| {
                original > m.line_base && original <= m.line_base + super::LINE_STRIDE
            }) {
                let next_source = i64::try_from(index).map_err(|_| Error::Limit("source map"))?;
                let next_line = i64::try_from(original - module.line_base - 1)
                    .map_err(|_| Error::Limit("source map"))?;
                let next_col = c
                    .parse::<i64>()
                    .map_err(|_| Error::Input("source map column".into()))?
                    .saturating_sub(1);
                mapping = format!(
                    "A{}{}{}",
                    vlq(next_source - source),
                    vlq(next_line - line),
                    vlq(next_col - column)
                );
                source = next_source;
                line = next_line;
                column = next_col;
            }
        }
        mappings.push(mapping);
    }
    serde_json::to_string(&serde_json::json!({"version":3,"sources":modules.iter().map(|m|&m.config.path).collect::<Vec<_>>(),"sourcesContent":modules.iter().map(|m|mapped_source(project,m)).collect::<Vec<_>>(),"names":[],"mappings":mappings.join(";")})).map_err(|_|Error::Input("source map serialization".into()))
}

fn mapped_source(project: &super::CheckedProject, module: &super::Module) -> String {
    use crate::syntax::Declaration;
    let mut declarations = module
        .parsed
        .program
        .declarations
        .iter()
        .filter_map(|d| match d {
            Declaration::Function(f) => Some((
                f.location.line,
                project.browser.functions.contains_key(&f.name),
            )),
            Declaration::Alias(n, _, l) => Some((l.line, project.browser.aliases.contains_key(n))),
            Declaration::Record(r) => Some((
                r.location.line,
                project.browser.records.contains_key(&r.name),
            )),
            Declaration::Enum(e) => {
                Some((e.location.line, project.browser.enums.contains_key(&e.name)))
            }
            _ => None,
        })
        .chain(module.parsed.components.iter().map(|c| {
            (
                c.location.line,
                project.browser.components.contains_key(&c.name),
            )
        }))
        .collect::<Vec<_>>();
    declarations.sort_unstable_by_key(|d| d.0);
    let mut output = String::new();
    for (index, line) in project.sources[&module.config.path]
        .split_inclusive('\n')
        .enumerate()
    {
        let global = module.line_base + index + 1;
        if declarations
            .iter()
            .rev()
            .find(|(start, _)| *start <= global)
            .is_some_and(|(_, used)| *used)
        {
            output.push_str(line);
        } else {
            for c in line.chars() {
                output.push(if c == '\n' { '\n' } else { ' ' });
            }
        }
    }
    output
}
