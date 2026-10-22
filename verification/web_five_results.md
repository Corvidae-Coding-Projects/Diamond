# W5 — shared accounts and explicit external APIs

**Complete within the documented development profile, 22 October 2026.**
Diamond 0.11.0, Rust/Cargo1.96.0, Linux, local Unix-socket PostgreSQL and loopback
HTTP. W6–W10 remain. There is one current schema; no deployed-version upgrade
coexistence was added. Public TLS/proxy deployment and production readiness are
not established.

## Implemented and inspected paths

| Boundary | Implementation / callers | Observed evidence |
| --- | --- | --- |
| Explicit enrollment/profile | `project/mod.rs`, `accounts/{mod,schema,enrollment}.rs`, CLI/native initialization | Open/invite/closed; canonical pending nonadmin registration; trusted admin provisioning; wrong recipient/purpose/app/expiry; one-use race; resend/recovery. |
| Shared identity/revocation | `accounts/sessions.rs`, `project/identity.rs`, connected cookie handlers | Verified/disabled/epoch checks; password change/reset; single/all logout; disable/reenable; fresh request denial on both nodes; DB expiry/restart/outage. |
| Independent limits | `accounts/rates.rs`, actual peer capture and account/API dispatch | Every declared global/peer/subject threshold across nodes and actual source addresses; counters survive failed credentials, transaction abort and restart. |
| Development delivery | `accounts/delivery.rs`, explicit serve grant, postcommit dispatch | Real0700 directory/0600 files; digest-only DB challenges; delivery failure after commit; unknown COMMIT ack distinct from confirmed abort. |
| Attenuated credentials | `accounts/credentials.rs`, account route table, API grant | Operation/scope/expiry/epoch/owner checks; raw secret only at issuance; metadata-only list; owner revoke; live resource policy intersection. |
| External contract | `project/api.rs`, `connected/profiles.rs`, `artifact.rs`, strict wire codecs | Explicit exports; public read-only; reserved route and version rejection; strict raw JSON; cookie/bearer separation; canary absence; wire-safe contract definitions. |
| Relational enforcement | `authority.rs`, relational restriction/query/runtime, interpreter invocation | Every query/join base plus get; create/update/delete pre/post/return-read; rollback after hidden return; fresh mutation parameter bindings. |
| Browser controls/recovery | checked session lowering, generated runtime, `examples/accounts-workspace` | Actual Chrome login and CRUD, user-assisted credential/password controls, private cache clearing, competing pending events, truncated identity delivery and fresh recovery. |
| Public native/CLI interface | `main.rs`, `CheckedProject::{add_postgres_administrator,serve_accounts}` | External Rust consumer initializes/provisions/rejects duplicates and serves actual HTTP/API; runtime role has DML without schema ownership. |

This ledger covers the W5 implementation and affected callers. It is not a claim
of a new whole-codebase security audit.

## Final qualification

- Executable SHA256: `a9a73f51e193e90cf62b12d0fd8343f40177301541ababa93131a482fb349d74`
- Library SHA256: `f80a41e7c633896324e82b26b8d97a30d802be2d9819ef0c449eadd0e19a66ed`
- [Independent coverage](w5_independent/COVERAGE.md): **294 observations, 1,956
  actual HTTP requests, 25 clean node shutdowns** in one complete frozen-artifact
  run. [Machine receipt](w5_independent/results.json) records every component,
  source/fixture hash, concrete generated inputs, SQL observations and cleanup.
- Seed `0xD1A05005`:24 credential fixtures across4 users,3 scope IDs,7 sampled
  scope sets and all7 nonempty read/count/join operation sets;48 independent
  get/count comparisons. Six valid and23 invalid targeted wire classes.
- [Actual browser record](web_five_browser_results.json): final artifact sign-in,
  real workspace/project/issue creation, held logout with competing click/input,
  actual truncated200 body, private-state clearing, fresh sign-in/read recovery
  and sign-out. User-assisted password/credential controls ran on the recorded
  intermediate artifact; their persisted outcome was independently read back
  and reauthenticated after restart. Final backend lifecycle checks exercise the
  same account operations on the frozen artifact. Browser records label these
  artifacts separately; not every account control was operated in Chrome.
- [Final browser DB readback](web_five_browser_database.json) and
  [shutdown](web_five_browser_lifecycle.json); the earlier user-assisted fixture's
  [DB observation](web_five_browser_initial_database.json) records account epoch2,
  no sessions and the issued credential's old epoch1.
- Existing W4 campaign on the final artifact:30 checker,98 scalar,175 query,
  18 migration/native and14 fault observations plus the saved regression;100,003
  stored rows. [Receipt](web_five_existing_relational.json).
- Existing W3 campaign on the final artifact:185 semantic,30 checker and121
  wire observations,3 saved regressions,5 native checks and184 exact native
  corpus values. This receipt does not stand in for fresh Chrome execution of
  the full shared-value corpus. [Receipt](web_five_existing_frontend.json).
- Build, format, strict Clippy (all targets/features),29 doctests and
  warning-denied rustdoc pass. There are zero unit tests. No unsafe code was
  introduced; Miri was not applicable.

## Discovered failures and replay

1. Valid scoped API updates/deletes returned503 while cookie RPC worked. Fresh
   owner and nonowner database roles reproduced it. Scope parameters from the
   initial authorized lookup were incorrectly reused for mutation SQL. Mutation
   bindings now start independently. [Original and final replay](w5_independent/findings/api_mutation/).
2. The reserved `_contract` name compiled as an operation although metadata owned
   its route; metadata ignored explicit invalid API versions. Both were observed
   before patching and now reject. [Replay](w5_independent/findings/api_contract_boundary/).
3. A competing browser action could cancel a pending logout handler after the
   server had committed, retaining old private DOM. The runtime now blocks
   competing events/inputs/routes while identity changes are pending, clears
   components on uncertain identity delivery or current401 identity loss, and
   preserves the epoch boundary for stale replies. Actual held/truncated reply
   replays pass. [Concrete manual replay](regressions/w5_identity_transition.md).

Eight-client identity pressure produced8 confirmed409 and6 safe503 responses in
4.028 seconds, zero credential writes,14 independently committed attempts, fresh
recovery and clean shutdown. Bounded admission/deadline unavailability is permitted;
this is not a production capacity SLO. The [pressure replay](w5_independent/findings/identity_pressure/)
retains the original observation and predefined no-write/rate/recovery oracle.

## Replay and limits

```sh
cargo build --locked
python3 verification/web_five_independent.py
python3 verification/web_five_browser_fixture.py --gated
# In another interactive terminal, use the browser regression instructions:
python3 verification/web_five_browser_gate.py
```

The gate forwards actual Diamond response bytes and never supplies application
handlers or expected answers. Stop both browser fixture terminals with `stop`.
All owned services from this qualification were stopped and disposable private
databases/mail directories removed; existing unrelated services were left alone.

Private mail files are a development adapter, not SMTP or a durable mail queue.
Generic acceptance does not establish constant-time recipient privacy or conceal
service faults. Ceilings were not exhaustively filled. Cross-component stale401
cleanup and history changes during pending identity were reviewed but not each
separately forced in Chrome. Backend snapshots have W4's serial-order revocation
boundary, not real-time cancellation.

The Diamond client has no application retry loop. In the recorded zero-byte
connection-loss case Chrome itself repeated the HTTP logout; the consumed session
then received401 and the private cache cleared. Qualification does not establish
exactly-once HTTP delivery. One-use challenges and request authorization still
apply to every received request; callers must inspect state before explicit
retry of an uncertain mutation.

No public TLS/proxy deployment, SMTP delivery, uploads/object storage, outbound
integrations, durable jobs, database-owner compromise, production load/timing SLO
or exhaustive security claim is included. See the [guide](../docs/accounts-and-apis.md)
and [final state](web_five_final_state.json) for configuration and fingerprints.
