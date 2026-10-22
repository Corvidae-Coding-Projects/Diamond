# W4 independent qualification coverage

This is an output ledger for the pinned run in
`verification/web_four_independent_results.json`, not a whole-codebase audit or
a claim of complete security. Browser work is root's separate actual Chrome
record. The user's scope correction excludes mixed schema versions, upgrade
compatibility, rollback windows, and application-version leases.

Executable SHA-256:
`15dfe6108d99a9defe3e5bd4304d0b9d720ed1203859e58e287d0b2ed4b06166`

Library SHA-256:
`f8e777be87138f2622c038c9799294de157c1ca7ad9fb57ec039e7a6958576e7`

Final receipt SHA-256:
`ca63cbb2db1216a3e8bd380e69944ae7315b3aed2335e540e48e4cf245864a56`

## Operated interfaces

| Driver | Observations | Actual caller path and independent checks |
| --- | ---: | --- |
| `checker.py` | 30 | Real `diamond check` over independently authored manifests and source; scalar shapes, resource/index/key bounds, explicit global uniqueness, lookup index/type/purpose, unsupported shapes and cycles |
| `scalars.py` | 98 | Real CLI startup/init/account provisioning; constrained-role two-node generated RPC create/get/update/delete/select; exact typed values; physical catalogs; injected corruption/audit/recovery; real graceful node shutdown |
| `queries.py` | 175 | Seed 100,003 real rows; four authenticated identities plus anonymous gate; seven scopes; independent Python counts/sums/pages/ranges/joins; actual generated SQL captured and explained; concurrent increments and committed revocation |
| `migrations.py` | 18 | External public Rust API consumer, current-schema CLI admin operations, serialized migration coordinators, process-killed backfill transaction, resumed checkpoints, owner/runtime separation, and ordinary RPC from migrated current nodes |
| `faults.py` | 14 | Two actual nodes with transparent framed Unix PostgreSQL proxy; held earlier snapshot, real HTTP disconnect, actual COMMIT acknowledgment loss, deferred PostgreSQL 40001,32 concurrent clients, database outage/restart and connection replacement |
| Retained join regression | 1 | Real check of source-level native-server join output; originally rejected and now accepted |

The query driver made 473 HTTP requests. Complete pagination traversed 143 nonempty
pages and retained all 14,286 ordered unique IDs in an authorized workspace,
page boundaries, and complete typed-value fingerprints. It compared the entire
ordered result against the prewritten model, not merely page length or success.
Other identity/scope pairs have independently checked counts/sums/first pages;
joins independently filter both base resources. The same fixture contains 6,667
None/None, 26,668 None/Some, 13,334 Some/None, 4,849 equal Some/Some, and 48,485
unequal Some/Some optional-field pairs.

## Contract matrix status

PASS means the stated bounded scenario was operated; it does not imply that
every possible value, schedule or policy program was explored.

| Oracle IDs | Status | Evidence and bounds |
| --- | --- | --- |
| S01 | PASS | Positive finite scalar/optional/unit-enum schema and distinct unsupported shape/config cases through real checker |
| S02 | PASS | Physical typed/nullability/collation columns, PK, scoped unique, secondary indexes and composite scoped FK inventoried; actual indexed query plan inspected |
| S03 | PASS | 23 distinct typed creates and fresh-node reads, including Int64 edges, precision beyond JS Number, Unicode/NUL, canonical-equivalent distinct text, 80-scalar emoji/NUL boundaries, Option and unit enum |
| S04 | PARTIAL | Scoped duplicate uniqueness and cross-scope FK rejection leave data unchanged; malformed type/tag/enum/owner inputs reject. Required-column and PK constraints were inventoried; direct SQL NULL/duplicate-PK fault injection was not part of this driver |
| S05 | PASS | Owner removes one check and injects invalid encoded text; actual get returns no record, explicit audit refuses corrupt row/catalog; exact data/check restoration passes read/audit |
| Q01/Q02 | PASS | Independently modeled authorization, count/sum, duplicate sort keys, forward complete pagination, reverse page, empty scopes, fresh clients and two nodes |
| Q03 | PASS | Identity/scope cursor reuse cannot grant authority; malformed/oversize data, forged resource/order/ID and direction mismatch reject; valid recovery succeeds |
| Q04 | PASS | Declared one-hop inner join applies independent project and issue policies, including hidden parents and absent optional references; left joins are outside this profile |
| Q05 | PASS | 100,003 real rows; real generated SELECT captured from PostgreSQL log and EXPLAIN ANALYZE; final Issues ordered Index Scan retrieves 17 rows with no Sort. No long-duration throughput/production-capacity claim |
| P01/P02 | PASS | Workspace-specific reader/editor/admin decisions, mixed identities, forged envelope rejection, five optional comparison classes and optional-owner native pre/post equality; anonymous calls are rejected before SQL by inherited connected RPC gate |
| P03 | PASS, bounded | Purpose-limited lookup operates without ordinary membership read grant/policy; invalid index/type/purpose and relationship cycle cases reject. No arbitrary recursive lookup execution exists or is claimed |
| P04/T03 | PASS | Actual earlier snapshot paused after authorized read; independent membership deletion commits; earlier work fits serial order or aborts; later requests on both nodes deny |
| T01 | PASS, bounded | Two independent clients race one read-modify-write; committed rank equals prior rank plus confirmed successes. This is a retained two-operation schedule, not exhaustive concurrency exploration |
| T02 | PASS | Cross-scope post-state denial leaves original row intact; a second write's failed return-read rolls back an earlier successful write in the same request |
| T04 | PASS, bounded | Actual PostgreSQL COMMIT CommandComplete is discarded; caller gets unknown while independent SQL sees exactly one commit; no automatic replay. HTTP disconnect and confirmed deferred 40001 are separate schedules. No power-loss/disk-durability claim |
| C01 | PASS, bounded | 32 callers, at most 8 observed runtime connections across two nodes, bounded 503 availability rejection, no idle transactions; failed/uncertain connection cleanup and actual DB outage/restart recover. All owned node processes/transport threads drain |
| M01 | PASS | Actual CLI startup refuses absent metadata and incompatible current version; digest tampering makes audit refuse, with exact restoration/recovery |
| M02/M03/M04/M06 | PASS | Explicit current schema migration, one winner among two coordinators,7-row bounded batches, killed in-flight batch behind real lock with unchanged committed progress, fresh-process resume, retained existing Some91, two restarted constrained-role nodes read final values |
| M05 | PASS, bounded | Unsupported field removal and changed same-name backfill plan refuse. The migrator exposes checked additive operations; arbitrary SQL is not implemented |
| N01 | PASS | External Rust consumer compiles and runs using public compile/init/status/audit/migrate/backfill interfaces and rejects invalid batch sizes |

## Distinct retained findings

- `findings/join_server_type_before.json` and source fixture: valid native-server
  join output was absent from the resolver symbol map. Final regression checks it.
- `findings/page_plan_before.json`: actual initial SELECT scanned 100,003 issue
  rows, returned 14,286 candidates and repeatedly scanned memberships before
  sorting to 17. Nonnullable equality and typed self-comparison folding produce
  the final ordered17-row Index Scan retained in the final receipt.
- `findings/shutdown_panic_before.txt` and
  `findings/shutdown_replay_before.json`: the prior build's data assertions
  passed while SIGINT caused exit 101 and a nested Tokio-runtime panic. The
  strengthened gate independently reproduces this failure; final shutdown
  receipts require code 0, no panic and no forced termination.

Earlier development receipts with harness/oracle mistakes or redundant passing
observations were removed after final qualification. Relevant corrections are
explained in `ORACLES.md`; expected authorized values and state transitions were
never adopted from implementation outputs.

Public TLS/proxy deployment, browser controls, W5–W10 features, long-duration
capacity, filesystem/power-loss durability, and universal concurrency/security
claims remain outside this independent campaign. Root's separate browser and
project qualification record must state its own operated paths and gaps.
