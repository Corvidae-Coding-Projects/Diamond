# LP7: diagnostics, formatter, editor support, and current guides

**Status:** Complete, 27 October 2026. Evidence: [LP7 ledger](../verification/lp7/README.md).

## Contract

LP7 adds tools around the existing checked language; it does not add an authority
escape hatch or change program meaning. Tooling must consume the real lexer,
parser, checker, project resolver, and browser lowering metadata.

- `diamond fmt [--check] FILE...` accepts only valid Diamond source, preserves
  comments and string/template meaning, normalizes indentation and line endings,
  is idempotent, and never changes an invalid file.
- `diamond lsp` speaks bounded LSP/JSON-RPC over stdio. Open/change diagnostics
  are compiler diagnostics for the current document version. Definition,
  references, hover, and completion use symbols parsed from that same version.
  No document or request may exceed the language source/message budgets.
- CLI diagnostics render the exact module path, one-based Unicode location,
  source excerpt, stable code, and a code-specific remedy without printing
  secret contents.
- Reachable component/event locations are emitted into the privacy-filtered
  browser map. A browser event failure names the reachable Diamond handler;
  unreachable server/private source remains absent from public artifacts.
- The canonical specification, profile matrix, guides, CLI help, and current
  examples describe the same 0.20.0 contract and tooling.

## Exit gate

AC-1 and AC-10 pass when the predefined LP7 scenarios pass on one candidate,
all current examples check, formatting is stable and meaning-preserving, the LSP
is exercised by a real protocol client, a real browser failure identifies its
handler, and the LP1--LP6 affected gates still pass.
