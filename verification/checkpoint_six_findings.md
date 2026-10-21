# Checkpoint-six findings and fix decisions

The pre-edit executable was checkpoint five's 0.5.0 artifact with SHA-256
`b8ae4f208469c846114a92bd5b0229954bfe8f08f8fd3467d64d7928db1e1afc`.
The raw 24-case receipt is
[`regressions/cp6_diagnostics_before.json`](regressions/cp6_diagnostics_before.json).
It was captured after the acceptance contract and before production edits.

## Confirmed diagnostic and syntax failures

- Empty source, `1__2`, and `1_` all passed `diamond check`. The integer forms
  silently became `12` and `1`. They now fail at the offending underscore as
  `D004`; comment-only or empty programs fail as `D010`.
- Parser failures were exposed only through `anyhow`'s context chain. They had a
  code and internal point but no path-bearing span, excerpt, or caret. `diamond
  explain` did not exist. The CLI now renders bounded, control-sanitized source
  diagnostics and explains every category emitted by the compiler.
- A missing closing parenthesis at the physical end of line reported column 3,
  the line's first token, instead of column 9. Newline tokens now carry their
  actual one-based Unicode end column.
- A mismatched `</div>` began at column 12 but reported column 18 after the tag
  had been consumed. The template parser now retains the closing tag's start.

The same baseline already located an inline Unicode HTML interpolation at the
correct line and column. That behavior was retained. A subsequent all-error-path
source audit found older checker helpers that could manufacture `0:0` locations
for types, routes, static URLs, and ID literals. Those paths now receive their
declaration, statement, template-value, or expression location and are covered
by the final 71-case driver.

## Startup and packaging decisions

Database-backed routes previously bound and waited even when the host omitted
`--database`; denial happened only after a request. Startup now rejects the
missing host grant. Listener binding occurs before database or account adapters
are opened, so an occupied port fails before those requested stores can be
created or changed. This does not change runtime policy enforcement or grant
authority from source declarations.

The real `cargo install --locked --path . --root ROOT` path was exercised in an
empty disposable root. The installed release binary ran from outside the source
tree, checked every shipped Diamond example, initialized the expected schema,
served one bounded loopback request, and passed the refusal matrix. The first
attempt was stopped by the execution sandbox's denied crates.io DNS lookup; the
same driver passed with the required registry and loopback permission. This was
an environment restriction, not a product failure.

## Evidence boundary

The performance thresholds are prototype regression budgets on the recorded
Linux host. They are not cross-platform or production service-level claims. The
full checkpoint-five hostile-input, policy, SQLite, process-cancellation,
transport, browser, and clock evidence remains in force; checkpoint six replays
the automatable matrix but does not enlarge its security or durability claims.
