# LP7 — diagnostics, formatter, editor support, and guides

**Complete, 2026-10-27.** LP7 adds compiler-backed authoring tools without
changing Diamond authority or execution semantics. The predefined expectations
are in [SCENARIOS.md](SCENARIOS.md); the implemented contract is in
[the LP7 design](../../.design/lp7-tools.md) and the user guide is
[docs/tooling.md](../../docs/tooling.md).

## Candidate and results

The final debug binary SHA-256 is
`28a8f27b6db02a04301ffa458f03df38e6c37fad16aaa6b6929adcd448a51e30`.
The assembled LP8 source manifest independently records every `src/` digest.

| Boundary | Current observation |
| --- | --- |
| Formatter | `tools.json`: valid CRLF/uneven source with comments, escapes, Unicode, and checked HTML formatted, checked, and round-tripped; second format byte-identical; invalid source unchanged; `--check` wrote nothing. |
| CLI diagnostics | Single-file and project-module D110 failures reported the exact expression line/Unicode-scalar column, stable code, caret, expected/actual types, remedy, and no unrelated private literal. |
| LSP | A real framed JSON-RPC client initialized the server, opened/changed/closed versioned documents, and exercised hover, definition, references, field completion, cancellation notification, diagnostics, shutdown, and an oversized-message rejection. |
| Browser mapping | Public build contained reachable function/component/event markers and `ui.dia` mapping but no server canary. Actual Chrome displayed `Contract.fail`; local state survived and the next valid event recovered. The exact final candidate repeats this in all three engines in `../lp8/browser-engines.json`; `browser.json` retains the earlier focused Chrome receipt. |
| Examples | All 18 current project manifests/top-level examples selected by the driver passed the real `diamond check` entry point. |
| Performance | `../lp8/performance.json`: 40 fresh-process samples each; message-board check p95 3.487 ms, 4,000-declaration check 19.084 ms and 22,780 KiB peak RSS on this host. Cross-engine render/event timings are in `../lp8/browser-engines.json`. |

The first implementation run exposed three bounded findings: HTML whitespace
preservation, final-expression diagnostic location, and an internal handler label.
All were fixed at their shared enforcement point and replayed. The retained
before/fix record is [findings-original.json](findings-original.json).

## Source and recovery coverage

- `src/formatter.rs` parses before/after, compares serialized syntax with locations
  removed, preserves HTML body bytes, and is called only by the CLI/public
  `format_source` API. `src/main.rs` checks all inputs before individually atomic,
  permission-preserving replacement and rejects symlinks.
- `src/editor.rs` owns framed I/O bounds, current document versions, compiler
  diagnostics, parsed symbols, lexical references, completion, close clearing, and
  protocol shutdown. It performs no asynchronous analysis, so an obsolete
  background result cannot publish after a newer version.
- `src/checker.rs` owns the corrected final-expression type span. `src/main.rs`
  reloads the originating project module only for bounded diagnostic rendering.
- `src/project/lower.rs`, `browser.rs`, and `runtime.js` carry reachable component
  and event locations into public maps and safe error labels. The existing
  reachability/privacy filter remains the only source-content publisher.

## Replay

```sh
cargo build --all-features
python3 verification/lp7_tools.py
target/debug/diamond serve verification/lp1/browser/diamond.toml \
  --listen 127.0.0.1:39171
```

The focused earlier browser receipt is [browser.json](browser.json); the final
candidate browser receipt is [the LP8 cross-engine report](../lp8/browser-engines.json).
The automated tooling driver owns temporary files/builds, communicates with the
real LSP process, and opens no network listener.

## Explicit limits

- LSP navigation/indexing is current-document only; whole-project resolution
  remains `diamond check diamond.toml`. Rename, code actions, workspace symbols,
  and formatting-over-LSP are unsupported.
- Source maps identify handler/component entry locations, not statement-level
  stepping or every expression.
- The formatter preserves semantic HTML whitespace exactly, so it intentionally
  does not visually reindent HTML body lines.
- Exact timings are host snapshots, not service-level objectives. No compiler
  allocator isolation or exhaustive malformed-editor corpus is claimed.
