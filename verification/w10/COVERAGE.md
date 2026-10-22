# W10 final release coverage ledger

This ledger names the production surfaces and retained evidence used for the
Diamond 0.16.0 decision. `coverage.py` inventories every Rust production module,
CLI command and declared workspace/catalog operation and fails if an entry is
unassigned. The final run also rejects missing/failed receipts or an executable or
library fingerprint mismatch.

## Acceptance criteria

| Gate | Current final-artifact evidence |
| --- | --- |
| AC-1 | Sealed checkpoint-six compatibility replay and W9 hostile campaign |
| AC-2 | W3 module/checker/artifact replay and installed scaffold/build receipt |
| AC-3 | W3 lifecycle/navigation/stale-response corpus and both real-Chrome journeys |
| AC-4 | W3 native/generated-JavaScript shared-value corpus |
| AC-5 | W3/W5 raw/generated clients and W9 artifact/transport campaigns |
| AC-6 | W5/W6/W7-W8 transport, TLS/streaming/cancellation and W9 hostile transport |
| AC-7 | W7-W8 relational gates, 100,000-record capacity run and W9 failure campaign |
| AC-8 | W5/W7-W8 policy paths and W9 authorization races |
| AC-9 | W7-W8 migration/backfill and W9 unknown-outcome/recovery checks |
| AC-10 | W5 account lifecycle, W9 cross-node races and Chrome session clearing |
| AC-11 | W3 frontend gates, W10 accessibility artifacts and both Chrome journeys |
| AC-12 | W6 file campaign, W9 storage faults and Chrome upload/download behavior |
| AC-13 | W6 controlled HTTP/DNS/TLS fixtures and W9 dependency failures |
| AC-14 | W7-W8 job gates, W9 kill/fence/dedup campaigns and sustained recovery |
| AC-15 | W7-W8 event gates, W9 malformed/stale/revocation campaigns and sustained socket |
| AC-16 | W6 trusted-adapter/native checks and checkpoint-six native API replay |
| AC-17 | W7-W8 deployment/restore, W9 crash/recovery and sustained node/worker recovery |
| AC-18 | Clean installed scaffold/build/run receipt, accessibility/diagnostics and Chrome |
| AC-19 | W7-W8 two-node/two-worker topology and both complete Chrome application journeys |
| AC-20 | This machine-checked ledger, every receipt above, current dependency review and capacity report |

## Production module groups

| Group | Paths | Primary evidence |
| --- | --- | --- |
| Language/checker/template | `lexer`, `parser`, `syntax`, `hir`, `checker/**`, `template` | checkpoint-six, W3, accessibility |
| Single-file runtime | root auth/data/eval/forms/http/policy/value/web modules | checkpoint-six compatibility/red-team |
| Public API and CLI | `lib.rs`, `main.rs` | installation, native consumers, deployment commands |
| Project compiler/frontend | project artifact/assets/browser/lower/render/resolve/RPC/wire modules | W3, artifact boundaries, Chrome |
| Connected transport/deployment | project connected/profiles/deployment/backup | W5, W7-W8, W9, capacity |
| Shared identity | `project/accounts/**`, identity | W5, W9 authorization races, Chrome |
| Relational storage | project storage and `relational/**` | W4 carried forward through W7-W9 and capacity |
| Private files | project files modules | W6, W9 storage loss/root replacement, Chrome |
| Jobs/integrations | project jobs/worker/integrations | W6, W7-W8, W9 fencing/dedup, capacity |
| Live events | project events | W7-W8, W9 malformed/stale sockets, Chrome, capacity |

The command inventory includes the 24 public command/help/version entry points.
The operation inventory is derived directly from both checked manifests and covers
every declared RPC, versioned JSON API, file binding, job and event. Session,
health, deployment and browser-runtime operations are covered by their adapter
groups and real-interface campaigns rather than counted as application declarations.

## Evidence boundary

Final receipts cover allowed and denied operations, malformed inputs, concurrency,
dependency loss, process termination, recovery, current locked dependencies,
accessibility inspection and sustained local capacity. They use synthetic identities
and data, one Chrome build, loopback/TLS fixtures, local Unix-socket PostgreSQL and
operator-owned shared directories. The untested boundaries in
`docs/release-qualification.md` remain outside this release result. Passing this
ledger is not proof of exhaustive security.
