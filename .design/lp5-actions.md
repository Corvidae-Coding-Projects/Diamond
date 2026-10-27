# LP5 reliable actions and recovery

Status: completed 27 October 2026. Scope is LP5/AC-6–AC-8 and the predeclared
real-interface schedules in verification/lp1/SCENARIOS.md. Prior work is preserved.

## Contract selected before implementation

```toml
[actions.rename]
function = "server.rename"
replay = "transactional"
resource = "Issues"
parent = "id"
label = "Save issue"
```

The parent parameter must be a checked nominal ID for the declared resource.
Native control code reauthorizes that parent and captures the action's protected
row accesses. Eligible actions use database CRUD and pure code, with bounded
captured rows. Set/aggregate queries and external effects reject for transactional
replay; `replay = "never"` retains the existing database-only single-attempt mode.
No distributed exactly-once guarantee or external-effect replay is introduced.

The runtime, rather than application code, issues invocation IDs. Each binds the
application/action contract, current principal, cookie/API profile and credential
restrictions, declared parent, and canonical validated input hash. Concurrent
prepare calls for the same unacknowledged intent reuse the same identity; changed
input conflicts. Duplicate execution serializes on the invocation row. A savepoint
allows business rejection to roll back its writes while recording that rejection;
the outer transaction atomically commits the outcome and successful mutation.

Receipts include automatically captured CRUD authorization facts and are sealed
with XChaCha20-Poly1305 using a runtime-generated per-application database key.
Both current rows and original access facts are reauthorized under current policy
and API restrictions before returning protected results, including scalar results.
Deleted rows require a committed deletion footprint and current policy on its
tombstone facts; a recreated/current row must also pass current read authorization.
Corruption, changed binding/version, and expiration are explicit terminal outcomes.
Missing/pruned identities are expired and can never become new executions.

Status reconciliation waits for the invocation lock. A still-prepared identity is
atomically closed as confirmed aborted, fencing any delayed execution. A lost
commit acknowledgement alone is Unknown; a fresh reconciliation establishes the
outcome from the same database. No automatic new identity or write retry follows
an uncertain result.

Recovery references live on the server. Browser reload discovers at most 16
unacknowledged references for the current account, then checks outcomes through
the same dispatcher. No form bodies, results, secrets, or invocation metadata need
browser storage. The runtime owns an accessible recovery notice with Check outcome
and Dismiss controls. Identity changes dispose private recovery state and requests.
Successful local handling acknowledges the reference automatically. API clients
use prepare/execute/status/ack through their existing explicit operation binding.

Bounds: 24-hour invocation lifetime, 16 unacknowledged and 128 total invocations per
user/application, 4,096 total/application, 64 captured CRUD accesses, and 256 KiB
plaintext receipt maximum. Preparation prunes expired records under a shared
capacity lock. Losing a receipt or reaching a bound never authorizes execution.

Qualification covers concurrent clients, canonical payload equivalence,
changed bindings, typed rejection/rollback, HTTP loss, PostgreSQL commit transport
loss, process interruption, fresh-session recovery, revocation/disablement/API
attenuation, corrupted/expired receipts and cleanup, real browser double-click and
reload, and directly affected LP2–LP4 behavior. Evidence distinguishes the actual
fault boundaries and records the final source/binary hashes and remaining limits.

The final 0.20.0 candidate passed the 74-observation main HTTP/PostgreSQL campaign,
23 compiler/artifact cases, a 17-case independently authored oracle, and five
actual Chrome recovery observations. Real PostgreSQL proxy cuts proved both
pre-commit rollback and committed-without-ack reconciliation. Confirmed findings
and their replays are recorded in `verification/lp5`; cross-browser and prolonged
resource qualification remain explicitly assigned to LP8 rather than inferred
from this bounded run.

One declared action function has exactly one `[actions.NAME]` binding. HTTP API
operations may alias that binding, but a second action name cannot introduce an
ambiguous parent/resource contract.
