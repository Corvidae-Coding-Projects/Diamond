# Diamond formatter and editor tooling

Diamond 0.20.0 ships source tools that call the same lexer, parser, and checker
as `diamond check`. They do not maintain a second permissive grammar.

## Formatter

Format one or more valid source files in place:

```sh
diamond fmt app.dia shared.dia
```

Check formatting without writing, suitable for a local pre-commit check:

```sh
diamond fmt --check app.dia shared.dia
```

The formatter normalizes line endings to LF and source block indentation to two
spaces. It preserves comment text, string literal contents, and the whitespace
inside checked HTML templates because HTML whitespace is program data. It parses
before and after formatting and compares location-independent syntax. If either
parse fails or meaning would change, formatting fails and no input file is
written. Multi-file invocations finish checking every input before replacing any
file. Replacements are atomic, retain permissions, and reject symlink inputs.

Formatting is idempotent: applying it twice to valid source returns identical
bytes. It is not a syntax-repair command and does not format `diamond.toml`.

## Diagnostics

`diamond check` reports the originating file/module path, stable diagnostic code,
one-based line and Unicode-scalar column, a bounded source excerpt and caret, and
a category-specific remedy. For example:

```text
error[D110]: expected Int, found Text
 --> app.dia:2:3
  |
2 |   "wrong"
  |   ^
 help: Use an expression supported by the declared type and current scope.
```

Run `diamond explain D110` for the category explanation. Diagnostics intentionally
show only the offending source line; they do not print unrelated source values or
runtime credentials. Public HTTP/runtime failures remain separately sanitized.

Browser builds map reachable shared/browser functions, components, and event
handlers. A failed event names its public `Component.event`; private server and
unreachable declaration source is excluded from public maps.

## Language server

Start the Language Server Protocol endpoint over standard input/output:

```sh
diamond lsp
```

Configure an editor's generic LSP client with command `diamond`, argument `lsp`,
language ID `diamond`, and file extension `.dia`. The server advertises UTF-32
positions because Diamond locations count Unicode scalar values. It supports:

- full-document open/change/close synchronization with versioned diagnostics;
- hover details for parsed top-level types, functions, components, routes, and
  forms;
- same-document definition and lexical-reference locations;
- parsed declarations/keywords and checked record-field completion;
- `shutdown`, `exit`, and cancellation notifications.

Each JSON-RPC message and open document is limited to 1,048,576 UTF-8 bytes; an
LSP header is limited to 8,192 bytes. Oversized, truncated, or malformed transport
input terminates the server explicitly. Analysis is synchronous and versioned, so
there is no background result that can publish after a newer document version;
closing a document publishes an empty diagnostic set.

The current server is deliberately local-document tooling. It does not provide a
workspace index, rename, code actions, formatting-over-LSP, or import-aware
cross-file navigation. Use `diamond check path/to/diamond.toml` for authoritative
whole-project resolution and module diagnostics.

## Qualification

The implemented contract and real protocol observations are recorded in the
[LP7 tool ledger](../verification/lp7/README.md). The final assembled replay and
browser/application evidence are in the [LP8 ledger](../verification/lp8/README.md).
