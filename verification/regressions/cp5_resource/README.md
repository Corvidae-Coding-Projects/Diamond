The original failure is an ordinary checked program that copies an immutable
recursive record into both children of each new record. Under a child-only
512 MiB address-space ceiling, the unmodified v0.4.0 executable aborts after 18
record declarations with an allocation failure. Seventeen declarations finish.
The preserved [recursive_record.dia](recursive_record.dia) is the concrete
18-declaration replay. Its result is intentionally a scalar; discarding an
expensive intermediate value does not exempt construction from a resource limit.

Replay the changed interpreter with:

```sh
cargo build --locked
python3 verification/resource_enforcement.py
```

The driver requires every excessive value/copy-work case to return an ordinary
resource-limit failure; a signal, panic, timeout, successful partial result, or
caught `99` result fails verification. Ordinary values still produce their
specified results. Depth is measured through managed nodes, so 32 records each
containing an `Option` reach depth 64 and remain valid; the next record exceeds
that bound. Eight 512 KiB payloads alone fill 4 MiB before node overhead and must
fail; seven fit. The three work cases repeatedly copy modest values, staying below the
per-value size while exhausting cumulative work. Each first runs `diamond check`
and requires success, then runs the program and specifically requires
`resource limit exceeded: managed value work`. The iteration replay uses a named
100-element index list in nested loops; the branch replay uses 180 iterations.
Both remain below the compiler's expression-token limit. The parameter/scope
replay uses the same exact runtime-work oracle. The campaign performs 43 CLI
invocations, including these three explicit compile prechecks, 10 independent
SQLite checks, and 8 HTTP requests. The fixed seed only permutes 12 independently
specified successful tree sizes.

This stricter replay supersedes the earlier work-case evidence: a generic
resource-limit assertion could accept an expression-token rejection before
execution after compiler bounds were added. The revised cases establish both
successful checking and failure at the intended runtime budget.

The SQLite checks read actual separately seeded files: one stored row is too
large, a selected list is too large, relation materialization exceeds the work
budget, and generated create/update return rows exceed the size budget. The
mutation cases check the database independently and require no committed change.
A real HTTP server then performs write → resource failure inside an explicit
transaction three times. Each response must be 413, each independent read must
find zero rows, and the same server must still read and commit a normal write.
A new interpreter process must read that committed row afterward.

The weighted profile is 128 units per managed node plus owned string/tag/field
name bytes, a maximum depth of 64, 4 MiB per value, and 64 MiB of cumulative
value materialization/copy work per interpreter evaluation. Scope copies include
map/name overhead. Mutation rows and binding-sized copy work are charged before
the automatic commit. Managed limits are uncatchable `Error::Limit` failures.
Earlier independently committed implicit operations remain committed if later
evaluation fails; a full-request rollback is only promised inside an explicit
transaction.

This is a conservative managed-value/work profile. It is not exact allocator
accounting, a hard process/live-heap quota, or accounting for every native policy,
form-parser, SQLite, library, or host allocation. Returned public values are
opaque, and trusted native callers may still use their normal `Clone` API.

The focused build/Clippy/format checks and before/after execution receipt are in
[focused_receipt.json](focused_receipt.json). The parent checkpoint-five receipt
owns the required whole-project matrix and final combined-source fingerprints.
No public API, dependency, unsafe code, lint allowance, or MSRV change was made.
