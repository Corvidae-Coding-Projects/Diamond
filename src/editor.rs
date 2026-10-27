//! Bounded compiler-backed Language Server Protocol support.

use crate::error::{Error, Location, Result};
use crate::lexer::Kind;
use crate::syntax::{Declaration, ProjectModule, TypeName};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::io::{Read, Write};

const MAX_MESSAGE_BYTES: usize = crate::MAX_SOURCE_BYTES;

#[derive(Clone, Debug)]
struct Document {
    version: i64,
    source: String,
}

#[derive(Clone, Debug)]
struct Symbol {
    name: String,
    detail: String,
    location: Location,
    kind: u8,
    fields: Vec<(String, String)>,
}

#[derive(Debug, Default)]
struct Snapshot {
    symbols: Vec<Symbol>,
    diagnostics: Vec<Value>,
}

#[derive(Debug, Default)]
struct Server {
    documents: BTreeMap<String, Document>,
    shutdown: bool,
}

pub(crate) fn run() -> Result<()> {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut input = stdin.lock();
    let mut output = stdout.lock();
    let mut server = Server::default();

    while let Some(message) = read_message(&mut input)? {
        if !server.dispatch(&mut output, &message)? {
            break;
        }
    }
    Ok(())
}

impl Server {
    fn dispatch(&mut self, output: &mut impl Write, message: &Value) -> Result<bool> {
        let method = message.get("method").and_then(Value::as_str).unwrap_or("");
        let id = message.get("id").cloned();
        let null = Value::Null;
        let params = message.get("params").unwrap_or(&null);
        if self.shutdown {
            if method == "exit" {
                return Ok(false);
            }
            if id.is_some() {
                error_response(output, id, -32600, "server is shutting down")?;
            }
            return Ok(true);
        }
        match method {
            "initialize" => respond(output, id, &initialize_result())?,
            "initialized" | "$/cancelRequest" => {}
            "shutdown" => {
                self.shutdown = true;
                respond(output, id, &Value::Null)?;
            }
            "exit" => return Ok(false),
            "textDocument/didOpen" => self.did_open(output, params)?,
            "textDocument/didChange" => self.did_change(output, params)?,
            "textDocument/didClose" => self.did_close(output, params)?,
            "textDocument/hover" => respond(output, id, &hover(&self.documents, params))?,
            "textDocument/definition" => {
                respond(output, id, &definition(&self.documents, params))?;
            }
            "textDocument/references" => {
                respond(output, id, &reference_list(&self.documents, params))?;
            }
            "textDocument/completion" => {
                respond(output, id, &completion_list(&self.documents, params))?;
            }
            _ if id.is_some() => error_response(output, id, -32601, "method not supported")?,
            _ => {}
        }
        Ok(true)
    }

    fn did_open(&mut self, output: &mut impl Write, params: &Value) -> Result<()> {
        let text = &params["textDocument"];
        let uri = required_string(text, "uri")?.to_owned();
        let source = required_string(text, "text")?.to_owned();
        let version = text
            .get("version")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        bounded_source(&source)?;
        self.documents
            .insert(uri.clone(), Document { version, source });
        publish(output, &uri, self.documents.get(&uri).ok_or(Error::Denied)?)
    }

    fn did_change(&mut self, output: &mut impl Write, params: &Value) -> Result<()> {
        let text = &params["textDocument"];
        let uri = required_string(text, "uri")?.to_owned();
        let version = text
            .get("version")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        let source = params["contentChanges"]
            .as_array()
            .and_then(|changes| changes.last())
            .and_then(|change| change.get("text"))
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Input("LSP full-document change is required".into()))?
            .to_owned();
        bounded_source(&source)?;
        if self
            .documents
            .get(&uri)
            .is_none_or(|old| version >= old.version)
        {
            self.documents
                .insert(uri.clone(), Document { version, source });
            publish(output, &uri, self.documents.get(&uri).ok_or(Error::Denied)?)?;
        }
        Ok(())
    }

    fn did_close(&mut self, output: &mut impl Write, params: &Value) -> Result<()> {
        let uri = required_string(&params["textDocument"], "uri")?.to_owned();
        self.documents.remove(&uri);
        notification(
            output,
            "textDocument/publishDiagnostics",
            &json!({"uri": uri, "diagnostics": []}),
        )
    }
}

fn initialize_result() -> Value {
    json!({
        "capabilities": {
            "positionEncoding": "utf-32",
            "textDocumentSync": {"openClose": true, "change": 1},
            "hoverProvider": true,
            "definitionProvider": true,
            "referencesProvider": true,
            "completionProvider": {"triggerCharacters": ["."]}
        },
        "serverInfo": {"name": "diamond", "version": env!("CARGO_PKG_VERSION")}
    })
}

fn hover(documents: &BTreeMap<String, Document>, params: &Value) -> Value {
    document_request(documents, params, |uri, document, line, column| {
        let snapshot = analyze(&document.source);
        let (name, start, end) = identifier_span(&document.source, line, column)?;
        let symbol = snapshot.symbols.iter().find(|symbol| symbol.name == name)?;
        Some(json!({
            "contents": {"kind": "markdown", "value": format!("```diamond\n{}\n```", symbol.detail)},
            "range": character_range(line, start, end),
            "data": {"uri": uri, "version": document.version}
        }))
    })
    .unwrap_or(Value::Null)
}

fn definition(documents: &BTreeMap<String, Document>, params: &Value) -> Value {
    document_request(documents, params, |uri, document, line, column| {
        let name = identifier_at(&document.source, line, column)?;
        let snapshot = analyze(&document.source);
        let symbol = snapshot.symbols.iter().find(|symbol| symbol.name == name)?;
        Some(json!({"uri": uri, "range": location_range(symbol.location, &symbol.name)}))
    })
    .unwrap_or(Value::Null)
}

fn reference_list(documents: &BTreeMap<String, Document>, params: &Value) -> Value {
    document_request(documents, params, |uri, document, line, column| {
        let name = identifier_at(&document.source, line, column)?;
        Some(Value::Array(
            references(&document.source, &name)
                .into_iter()
                .map(|location| json!({"uri": uri, "range": location_range(location, &name)}))
                .collect(),
        ))
    })
    .unwrap_or_else(|| Value::Array(Vec::new()))
}

fn completion_list(documents: &BTreeMap<String, Document>, params: &Value) -> Value {
    document_request(documents, params, |_uri, document, line, column| {
        Some(completions(&document.source, line, column))
    })
    .unwrap_or_else(|| json!({"isIncomplete": false, "items": []}))
}

fn analyze(source: &str) -> Snapshot {
    match crate::parser::project_module(source) {
        Ok(module) => {
            let symbols = symbols(&module, source);
            let diagnostics = crate::checker::check(&module.program)
                .err()
                .map_or_else(Vec::new, |error| vec![diagnostic(&error)]);
            Snapshot {
                symbols,
                diagnostics,
            }
        }
        Err(error) => Snapshot {
            symbols: Vec::new(),
            diagnostics: vec![diagnostic(&error)],
        },
    }
}

fn symbols(module: &ProjectModule, source: &str) -> Vec<Symbol> {
    let mut result = Vec::new();
    for declaration in &module.program.declarations {
        match declaration {
            Declaration::Alias(name, ty, location) => result.push(symbol(
                name,
                format!("type {name} = {}", type_name(ty)),
                *location,
                22,
            )),
            Declaration::Record(record) => {
                let fields = record
                    .fields
                    .iter()
                    .map(|field| (field.name.clone(), type_name(&field.ty)))
                    .collect();
                let mut value = symbol(
                    &record.name,
                    format!("type {}", record.name),
                    record.location,
                    23,
                );
                value.fields = fields;
                result.push(value);
            }
            Declaration::Enum(enumeration) => result.push(symbol(
                &enumeration.name,
                format!("enum {}", enumeration.name),
                enumeration.location,
                13,
            )),
            Declaration::Resource(name, record, location) => result.push(symbol(
                name,
                format!("resource {name}: {record}"),
                *location,
                6,
            )),
            Declaration::Function(function) => {
                let parameters = function
                    .parameters
                    .iter()
                    .map(|(name, ty)| format!("{name}: {}", type_name(ty)))
                    .collect::<Vec<_>>()
                    .join(", ");
                result.push(symbol(
                    &function.name,
                    format!(
                        "fn {}({parameters}) -> {}",
                        function.name,
                        type_name(&function.result)
                    ),
                    function.location,
                    12,
                ));
            }
            Declaration::Route(route) => {
                if let Some(name) = &route.name {
                    result.push(symbol(
                        name,
                        format!("route {name}: {} {}", route.method, route.path),
                        route.location,
                        12,
                    ));
                }
            }
            Declaration::Policy(_) | Declaration::App(_) => {}
        }
    }
    for component in &module.components {
        let properties = component
            .properties
            .iter()
            .map(|(name, ty)| format!("{name}: {}", type_name(ty)))
            .collect::<Vec<_>>()
            .join(", ");
        result.push(symbol(
            &component.name,
            format!("component {}({properties})", component.name),
            component.location,
            7,
        ));
    }
    for route in &module.navigation {
        result.push(symbol(
            &route.name,
            format!("route {} = {:?}", route.name, route.path),
            route.location,
            12,
        ));
    }
    for (name, location) in &module.forms {
        result.push(symbol(name, format!("form {name}"), *location, 23));
    }
    for value in &mut result {
        value.location = definition_location(source, &value.name, value.location);
    }
    result
}

fn definition_location(source: &str, name: &str, declaration: Location) -> Location {
    crate::lexer::lex(source)
        .ok()
        .and_then(|tokens| {
            tokens.into_iter().find_map(|token| match token.kind {
                Kind::Name(value)
                    if value == name && token.location.line() == declaration.line() =>
                {
                    Some(token.location)
                }
                _ => None,
            })
        })
        .unwrap_or(declaration)
}

fn symbol(name: &str, detail: String, location: Location, kind: u8) -> Symbol {
    Symbol {
        name: name.to_owned(),
        detail,
        location,
        kind,
        fields: Vec::new(),
    }
}

fn type_name(ty: &TypeName) -> String {
    match ty {
        TypeName::Named(name) => name.clone(),
        TypeName::Generic(name, arguments) => format!(
            "{}<{}>",
            name,
            arguments
                .iter()
                .map(type_name)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        TypeName::Id(tag) => format!("Id<{tag:?}>"),
        TypeName::Blob(binding) => format!("Blob<{binding:?}>"),
        TypeName::Secret(binding) => format!("Secret<{binding:?}>"),
        TypeName::Stream(channel) => format!("Stream<{channel:?}>"),
        TypeName::BoundedText(minimum, maximum) => {
            format!("Text<length: {minimum}..{maximum}>")
        }
    }
}

fn diagnostic(error: &Error) -> Value {
    let (code, location, message) = match error {
        Error::Diagnostic {
            code,
            location,
            message,
        } => (*code, *location, message.clone()),
        Error::Unsupported { location, feature } => {
            ("D900", *location, format!("unsupported feature: {feature}"))
        }
        Error::Source { source, .. } => return diagnostic(source),
        _ => ("D000", Location::default(), error.to_string()),
    };
    json!({
        "range": point_range(location),
        "severity": 1,
        "code": code,
        "source": "diamond",
        "message": message
    })
}

fn completions(source: &str, line: usize, column: usize) -> Value {
    let snapshot = analyze(source);
    let prefix = source
        .lines()
        .nth(line)
        .map(|value| value.chars().take(column).collect::<String>())
        .unwrap_or_default();
    let field_context = prefix.trim_end().ends_with('.');
    let mut items = if field_context {
        snapshot
            .symbols
            .iter()
            .flat_map(|symbol| &symbol.fields)
            .map(|(name, ty)| json!({"label": name, "kind": 5, "detail": ty}))
            .collect::<Vec<_>>()
    } else {
        snapshot
            .symbols
            .iter()
            .map(|symbol| {
                json!({"label": symbol.name, "kind": symbol.kind, "detail": symbol.detail})
            })
            .collect::<Vec<_>>()
    };
    if !field_context {
        items.extend(
            [
                "fn", "type", "enum", "resource", "match", "for", "let", "return", "true", "false",
                "Some", "None", "Ok", "Err",
            ]
            .into_iter()
            .map(|keyword| json!({"label": keyword, "kind": 14})),
        );
    }
    items.sort_by(|a, b| a["label"].as_str().cmp(&b["label"].as_str()));
    items.dedup_by(|a, b| a["label"] == b["label"]);
    json!({"isIncomplete": false, "items": items})
}

fn references(source: &str, name: &str) -> Vec<Location> {
    crate::lexer::lex(source).map_or_else(
        |_| Vec::new(),
        |tokens| {
            tokens
                .into_iter()
                .filter_map(|token| match token.kind {
                    Kind::Name(value) if value == name => Some(token.location),
                    _ => None,
                })
                .collect()
        },
    )
}

fn document_request<F>(
    documents: &BTreeMap<String, Document>,
    params: &Value,
    operation: F,
) -> Option<Value>
where
    F: FnOnce(&str, &Document, usize, usize) -> Option<Value>,
{
    let uri = params["textDocument"]["uri"].as_str()?;
    let document = documents.get(uri)?;
    let line = usize::try_from(params["position"]["line"].as_u64()?).ok()?;
    let column = usize::try_from(params["position"]["character"].as_u64()?).ok()?;
    operation(uri, document, line, column)
}

fn identifier_at(source: &str, line: usize, column: usize) -> Option<String> {
    identifier_span(source, line, column).map(|(name, _, _)| name)
}

fn identifier_span(source: &str, line: usize, column: usize) -> Option<(String, usize, usize)> {
    let characters = source.lines().nth(line)?.chars().collect::<Vec<_>>();
    let mut start = column.min(characters.len());
    let mut end = start;
    while start > 0 && identifier_continue(characters[start - 1]) {
        start -= 1;
    }
    while end < characters.len() && identifier_continue(characters[end]) {
        end += 1;
    }
    (start < end).then(|| (characters[start..end].iter().collect(), start, end))
}

fn identifier_continue(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '_'
}

fn character_range(line: usize, start: usize, end: usize) -> Value {
    json!({
        "start": {"line": line, "character": start},
        "end": {"line": line, "character": end}
    })
}

fn location_range(location: Location, name: &str) -> Value {
    let line = location.line().saturating_sub(1);
    let column = location.column().saturating_sub(1);
    json!({
        "start": {"line": line, "character": column},
        "end": {"line": line, "character": column + name.chars().count()}
    })
}

fn point_range(location: Location) -> Value {
    let line = location.line().saturating_sub(1);
    let column = location.column().saturating_sub(1);
    json!({
        "start": {"line": line, "character": column},
        "end": {"line": line, "character": column + 1}
    })
}

fn publish(output: &mut impl Write, uri: &str, document: &Document) -> Result<()> {
    let snapshot = analyze(&document.source);
    notification(
        output,
        "textDocument/publishDiagnostics",
        &json!({"uri": uri, "version": document.version, "diagnostics": snapshot.diagnostics}),
    )
}

fn bounded_source(source: &str) -> Result<()> {
    if source.len() > crate::MAX_SOURCE_BYTES {
        Err(Error::Limit("editor document bytes"))
    } else {
        Ok(())
    }
}

fn required_string<'a>(value: &'a Value, field: &str) -> Result<&'a str> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| Error::Input(format!("LSP field {field} is required")))
}

fn read_message(input: &mut impl Read) -> Result<Option<Value>> {
    let mut header = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        let count = input.read(&mut byte)?;
        if count == 0 {
            return if header.is_empty() {
                Ok(None)
            } else {
                Err(Error::Input("truncated LSP header".into()))
            };
        }
        header.push(byte[0]);
        if header.len() > 8192 {
            return Err(Error::Limit("LSP header bytes"));
        }
        if header.ends_with(b"\r\n\r\n") || header.ends_with(b"\n\n") {
            break;
        }
    }
    let header = std::str::from_utf8(&header)
        .map_err(|_| Error::Input("LSP header must be UTF-8".into()))?;
    let length = header
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("Content-Length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .ok_or_else(|| Error::Input("LSP Content-Length is required".into()))?;
    if length > MAX_MESSAGE_BYTES {
        return Err(Error::Limit("LSP message bytes"));
    }
    let mut body = vec![0u8; length];
    input.read_exact(&mut body)?;
    serde_json::from_slice(&body)
        .map(Some)
        .map_err(|_| Error::Input("invalid LSP JSON".into()))
}

fn respond(output: &mut impl Write, id: Option<Value>, result: &Value) -> Result<()> {
    let Some(id) = id else {
        return Ok(());
    };
    write_message(
        output,
        &json!({"jsonrpc": "2.0", "id": id, "result": result}),
    )
}

fn error_response(
    output: &mut impl Write,
    id: Option<Value>,
    code: i64,
    message: &str,
) -> Result<()> {
    let Some(id) = id else {
        return Ok(());
    };
    write_message(
        output,
        &json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}),
    )
}

fn notification(output: &mut impl Write, method: &str, params: &Value) -> Result<()> {
    write_message(
        output,
        &json!({"jsonrpc": "2.0", "method": method, "params": params}),
    )
}

fn write_message(output: &mut impl Write, value: &Value) -> Result<()> {
    let body = serde_json::to_vec(value).map_err(|_| Error::Input("LSP response JSON".into()))?;
    if body.len() > MAX_MESSAGE_BYTES {
        return Err(Error::Limit("LSP response bytes"));
    }
    write!(output, "Content-Length: {}\r\n\r\n", body.len())?;
    output.write_all(&body)?;
    output.flush()?;
    Ok(())
}
