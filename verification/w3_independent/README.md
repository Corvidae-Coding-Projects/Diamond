# Independent W3 verification

These drivers were designed from `verification/web_three.md` before final
qualification. They operate the actual Diamond CLI, public Rust library, real
HTTP servers and initialized PostgreSQL. The W2 helper supplies service setup
and raw transport only; it supplies no expected application answers.

From the repository root, after `cargo build --locked`:

```sh
python3 verification/web_three_independent.py
```

The wrapper pins the CLI and library bytes, compiles an external native caller,
records exact generated inputs and independent expected outputs, and returns
nonzero on any mismatch. It launches and stops only its own PostgreSQL and app
processes. PostgreSQL `initdb`, `pg_ctl` and `psql` and the Rust toolchain must be
available. Browser behavior is separately operated in real Chrome by the main
verification task; these raw drivers do not qualify UI interaction.

`ORACLES.md` states the requirements, reference calculations and covered
boundaries. `exploratory_summary.json` preserves the earlier exploration phase;
`../web_three_independent_results.json` is the final qualification receipt once
run after the implementation freezes. Successful CLI cases do not establish
cross-target browser agreement by themselves.

`browser_corpus/` is also a runnable pure Diamond project. Its **Run independent
corpus** button evaluates `corpus() -> List<Text>` and renders 184 unique labeled
values. `expected.json` comes from the independent Decimal/Unicode/list oracle;
`cases.json` preserves exact inputs and eight failure probes. Compare each list
item's `textContent` in real Chrome, preserving Unicode controls. Failure buttons
must preserve both prior output and corpus state; **Recover** must clear the
error and update output. `browser_corpus.py` regenerates the shared functions and
expected inputs, retaining a stable Diamond UI. The wrapper also compiles
`corpus_consumer.rs` against the public library and compares all 184 raw values;
project CLI text sanitization cannot serve as this exact Unicode oracle.

Concrete findings were captured before repair:

- `findings/result_error_bridge.dia`: an incompatible `Result<Int, DataError>`
  was accepted as `Result<Int, AppError>` and failed at runtime. The compile-time
  checker now must reject it. `findings/result_bridge_before.json` retains the
  original executable fingerprint and observed CLI output.
- `findings/type_privacy/diamond.toml`: browser source named a compiler-generated
  private shared record type and compiled. The original receipt is
  `findings/type_privacy_before.json`. The final checker campaign independently
  checks both direct generated-name rejection and public/private signature
  leakage, without relying on one rejection to establish the other.

Replay each minimal finding using `target/debug/diamond check PATH`; the expected
final result is a nonzero exit. The full checker campaign includes valid exported
shared types and contextual `Ok` controls to distinguish a correct fix from a
blanket rejection.


`event_composition/` is a separate connected Diamond project with an independent
runtime expectation table in `expected.json`. It exercises pure helpers around
RPC, nested remote arguments, lazy Bool/Match, sequential List/For, empty For,
loop binding scope, sequential assignments, a later failure preserving all prior
local state, and recovery. The checker driver compiles/builds valid nested forms
and rejects bad nested signatures and nested RPC in unmount cleanup. The HTTP
driver exercises the real server primitives and failure recovery. Those checks
are recorded separately from browser runtime execution; operate the visible
controls in real Chrome to qualify event composition itself.


`findings/text_replace_shrink.dia` builds 1,048,576 input bytes through legal pure
concatenation and replaces `aa` with `b`. The expected result is 524,288 scalars.
The old executable rejected this valid shrinking output because it estimated
input bytes plus replacement bytes without subtracting removed bytes;
`findings/text_replace_before.json` retains ASCII/Unicode before observations and
true expansion/deletion controls. The wrapper replays the exact minimal source.

`findings/data_error_wire_before.json` records that the documented fourth safe
code, `ApplicationFailure`, originally failed to decode as a Result argument.
The raw wire driver replays inspection and nested echo for all four codes.
Pure legacy `Err(Text)` retains the exact message in process on both targets.
Crossing the DataError wire deliberately redacts such Message errors to
`ApplicationFailure`; decoding it yields `Application failed.`. Explicit public
error details belong in an application enum.

`key_paths/` is the minimal real Diamond fixture for ambiguous raw Text keys in
component frame paths. Its expected action table requires four independent child
counters and retained values after reorder; compile/build is covered by the
checker, while actual counter independence must be qualified in Chrome.
