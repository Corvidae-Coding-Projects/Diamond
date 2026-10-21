# Checkpoint-six acceptance contract

This contract was fixed before the checkpoint-six failure campaign or production
edits. It covers the final polish and release-readiness pass for the interpreted
MVP. It does not expand the language, runtime authority, deployment, or security
claims recorded in `README.md` and `CHECKPOINTS.md`.

## Diagnostics and parser edges

- Every source diagnostic emitted by `diamond check` has a stable code, a
  one-based Unicode line and column, the source path, a bounded source excerpt,
  and a caret at the reported character. Control characters in an excerpt cannot
  manipulate terminal output.
- The native `Location` API exposes its line and column without exposing mutable
  compiler state. Display output remains `line:column`.
- `diamond explain CODE` accepts emitted diagnostic codes with or without a
  lowercase `d` prefix, prints the category and shortest useful remedy, and
  rejects unknown codes without treating arbitrary text as a source path.
- Decimal separators are accepted only as single underscores between digits.
  Trailing and repeated separators fail as `D004` at the offending underscore;
  a leading underscore remains the start of an identifier. Overflow remains
  `D004`.
- Valid CRLF input, Unicode before a diagnostic, comments and blank lines inside
  blocks, nested expressions, all shipped examples, and multiline typed HTML
  retain their supported behavior. Empty input, inconsistent indentation,
  malformed strings/templates/interpolations, trailing junk, unclosed groups,
  invalid numeric spellings, and unsupported tokens fail without panic, hang, or
  accidental execution.
- Template diagnostics and interpolated-expression diagnostics point to the
  actual template line and Unicode column in the original file.

## CLI, startup, and installation

- Help, version, command names, examples, and checkpoint wording agree at 0.6.0.
  Missing/unknown commands and options are explicit. `check` has no filesystem or
  runtime side effects beyond ordinary process output.
- `cargo install --locked --path . --root ROOT` succeeds in an empty disposable
  root. The installed binary reports 0.6.0, checks every shipped `.dia` example,
  runs `hello.dia`, and needs no source-tree-relative runtime assets.
- Pure `run` starts and exits with the expected result. `db-init` creates only the
  requested compatible schema. Route startup refuses non-loopback listeners,
  missing required database authority, conflicting auth/data paths, and occupied
  ports without accepting requests or creating unrelated paths.
- A loopback route program becomes ready, serves a request, honors its bounded
  request count, and exits cleanly. Startup failures remain bounded to five
  seconds.

## Documentation and organization

- The README provides a short install-to-first-run path, a command reference,
  links to a focused language guide and diagnostic reference, and a walkthrough
  of the complete message-board example. Every copied shell command is exercised
  against a disposable installation.
- Examples have an ordered index explaining the authority each one needs.
  Public docs distinguish the interpreted MVP from the architectural proposal
  and keep all production-readiness exclusions visible.
- Runtime/compiler modules remain internal unless an API is intentionally public;
  no lint suppression, authored unsafe Rust, generated unit-test suite, or new
  dependency is introduced for this pass.

## Measured performance and final gates

- On this qualification host, 40 fresh-process checks of `hello.dia`, 40 checks
  of `message_board.dia`, and 40 pure runs of `hello.dia` each have a p95 below
  one second. A generated legal source near the 4,096-declaration limit checks within
  five seconds. These are regression budgets for this prototype, not production
  service-level claims.
- Peak resident memory for checking `message_board.dia` and the generated large
  source is measured with the host's process accounting and remains below
  128 MiB per fresh process.
- Formatting, locked build, strict all-target/all-feature Clippy, tests, rustdoc,
  native embedding, checkpoint-one-through-five replay, the new edge/startup/
  install/performance campaigns, and an input/artifact fingerprint audit all pass
  on one final executable.
- Confirmed pre-repair failures are saved under `verification/regressions/cp6_*`
  with their original executable hash. Final machine-readable observations and
  artifact hashes are retained in `verification/checkpoint_six_results.json`.
