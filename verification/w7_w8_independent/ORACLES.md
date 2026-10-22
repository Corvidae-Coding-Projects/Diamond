# Independent W7/W8 real-interface oracle

Written before implementation details or execution. The source of expectations is
`.design/general-web-apps.md`, REQ-14/15/17/18/19, AC-14/15/17/18/19, and the
jobs/events/shared-identity architecture. A successful RPC, worker log line, or
HTTP status is not sufficient: authoritative database state, received fixture
effects, WebSocket messages, restored records, and object bytes are read back.

| Case | Action | Independent expected observation |
| --- | --- | --- |
| J1 | Submit declared checked handler and serializable payload; try undeclared handler, version, authority/payload forgery | Legal work persists; malformed or ungranted work is rejected without side effects; execution derives fresh restricted authority |
| J2 | Commit and roll back application mutation plus outbox submission | Both rows appear together after commit; neither becomes deliverable after confirmed rollback |
| J3 | Two workers race; terminate a claimed worker before dispatch | One live lease owns a claim; recovery after lease expiry eventually executes; stale completion cannot overwrite a newer lease |
| J4 | Receiver records effect; terminate worker before acknowledgment; recover | At-least-once attempts may repeat; stable destination idempotency key produces one business effect at the real receiver |
| J5 | Fail destination, exhaust attempts, cancel queued work, revoke user | Bounded attempts become visible dead-letter; cancellation prevents later dispatch; current account/membership governs later protected effects |
| E1 | Valid users connect to different servers and publish checked typed events | Shared durable cursors; only authorized typed data reaches each client |
| E2 | Missing/hostile origin, absent/expired session, mismatched payload | Connection or operation rejected; no protected data emitted |
| E3 | Revoke session or membership while connection remains open | No new delivery authorization uses state older than five seconds; already-transmitted bytes are outside the claim |
| E4 | Disconnect/reconnect with current and unavailable cursor; leave slow client unread | Ordered replay or explicit resynchronization; bounded resource use and slow-client termination; ordinary requests remain usable |
| D1 | Validate unsafe/missing grants, origin, schema or contract before launch | Explicit nonzero rejection without deployment or external mutation; valid capabilities enumerate correctly |
| D2 | Run two servers/two workers; drain, replace and kill one instance | Health/readiness reflect state; draining refuses new work and cleans up owned resources; surviving node remains functional |
| D3 | Backup while stopped; restore into distinct owned cluster/storage | Manifest agrees on records/accounts/files/jobs; file lengths and independently computed hashes agree; restored workers remain paused until explicit recovery choice |
| D4 | Use original credentials/revocation/session policy and restored pending work | Documented recovery policy is observed, not inferred; recovery time and snapshot-to-source data-loss window are measured |
| A1 | Exercise tracker and catalog from fresh installations on two nodes | Ordinary application logic remains Diamond; protected workflows and controlled external calls persist across fresh clients and replacement |

Scope excludes infrastructure provisioning, universal exactly-once effects,
old/new deployed schema coexistence (explicitly excluded by the user), a general
capacity qualification, and claims of total security. Generated cases must vary
distinct identities/workspaces, malformed versus legal payloads, timing around
claim/effect/ack, cursor positions, and process lifecycle. Retained regressions
must record original failure plus final replay on exact executable hashes.

## Driver boundaries

Reuse only the W6/W5 owned PostgreSQL, CLI, cookie, and HTTP lifecycle helpers.
Those helpers forward real requests to the actual Diamond executable. The
external receiver is a controlled external system whose deduplication is an
explicit independent destination contract. The WebSocket client implements the
wire framing and upgrade only; it never implements Diamond policy, queues,
events, responses, health, or restore behavior. Commands and syntax are selected
only after the actual implementation interface is provided, then matched back
to the stable expectations above. Results have not been collected yet.
