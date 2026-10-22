# W7/W8 independent real-interface evidence

Final six-gate replay: **PASS**, 77 observations, 651 helper-recorded HTTP
requests, 34 expected server/worker terminations, 52.17 seconds. A supplementary
object-store readiness replay adds 2 passing observations, 8 requests and 2
expected terminations. WebSocket upgrade requests and Caddy's periodic upstream
health requests are additional actual traffic, not included in that HTTP count.

All final receipts use Diamond executable SHA-256:
`3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.
The TLS fixture uses the parent-verified official Caddy binary SHA-256
`678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9`.
The implementing agent reports the corresponding library fingerprint separately;
this campaign operates the executable rather than a native library consumer.

The retained pre-execution oracle is `ORACLES.md`. Corrections to checker
assumptions are explicit in `ORACLE_CORRECTIONS.md`. Final aggregate and
per-campaign receipts contain artifact fingerprints and observations. Passing
runs remove their private temporary roots and stop their own processes. The
36 recorded terminations include three deliberately injected SIGKILL exits;
there were no unexpected exits, cleanup failures, or observed panics. A final
process inventory found no remaining processes using these campaign roots.

## Entry-point and observable coverage

| Source / real caller | Exercise and independent observation | Receipt |
| --- | --- | --- |
| `src/parser.rs`, `src/checker/integrations.rs`, `src/project/jobs.rs`; `diamond check` | Declared generic job/event calls accepted; unknown binding, wrong wire payload, recursive grants, external transactional claim, missing parent-write grant, zero/excessive retry bounds rejected | `checker_results.json`: 10 |
| `src/eval.rs`, `src/project/jobs.rs`; actual generated-contract RPC and scoped JSON API | Submit/status/cancel, same-key dedup, commit and propagated abort; independently read actual application/outbox rows | `jobs_events_results.json`: 25 across core paths |
| `src/project/jobs/worker.rs`; two actual `project-work` processes | Both reach independently held receiver simultaneously; DB-only handler commits edit/event/done and failed handler rolls edit/event back; receiver acknowledgment withheld, workers killed, actual 30-second lease expires and new worker redelivers same key/body; receiver's explicit deduplication records one business effect | `jobs_events_results.json` |
| Worker principal/restriction reconstruction; cookie versus scoped API | Cookie work executes; red-only token cannot queue blue work; parent moved to blue after red-only submission remains denied at execution even though owner retains blue membership; revocation after enqueue denies external dispatch | `jobs_events_results.json` |
| Worker attempts and operator status | Three real failing receiver requests end in visible dead state; queued cancellation remains inert; cross-node status reads actual terminal outcome | `jobs_events_results.json` |
| `src/project/events.rs`, connected dispatch; native masked WebSocket clients on A/B | Typed durable cross-node delivery, missing/hostile origins and anonymous/unrelated users denied, reader cannot publish, missed-event replay, ahead cursor resync, live membership revocation | `jobs_events_results.json` |
| Event scope/lifecycle and separate socket quota | Invalid/overflow/noncanonical cursors rejected; 32 sockets admitted and excess 503 while HTTP stays usable; actual database-clock session expiry closes connection and denies reconnect; unavailable retained cursor resync; unsolicited inbound actions cannot publish | `events_limits_results.json`: 9 |
| Event transport backpressure | 320 actual RPC publications of over 15,800 bytes each to a tiny-buffer unread socket; send timeout disconnects it, socket permit returns, ordinary authenticated read succeeds | `events_limits_results.json` |
| `src/project/deployment.rs`; real deployment-check, startup, health and lifecycle commands | Complete authority accepted without launch; unsafe origins/missing grant/incompatible registry rejected; both nodes serve same hashed assets; active WebSocket drains on SIGINT while other node serves; killed worker is replaced and survivor processes actual work | `deployment_results.json`: 22 across deployment/recovery paths |
| `src/project/deployment/backup.rs`; actual pg_dump/GnuPG/pg_restore | Active backup refused; quiesced private encrypted snapshot; wrong key/ciphertext modification leave empty destination unchanged; separate empty PostgreSQL and object roots restore exact inventory; source edit made after snapshot is absent; nonempty destination refused | `deployment_results.json` |
| Restore identity and jobs recovery; real accounts/files/worker entry points | Old cookie's protected operation and old API token return 401, original password signs in anew, other node downloads exact bytes/hash, restored worker cannot claim paused jobs, active recovery refused, explicit resume processes pending receiver effect | `deployment_results.json` |
| Real Caddy TLS proxy and native certificate-verifying HTTPS/WSS clients | Generated CA trusted only by test clients; strict chain/hostname verification; login, 20 RPC calls observed 10 per upstream, SSR/assets, exact streamed upload/download, WSS typed event, forwarding-header stripping/direct rejection, worker submission, health-based surviving-node service | `tls_proxy_results.json`: 8 |
| Current-source replacement and worker handler versions | Browser-only edit retains schema/handler version and queued work completes; changed server body preserves schema but old queued job is not claimed by new worker; new work completes and old work requires explicit cancellation | `job_versions_results.json`: 3 |
| Final readiness native adjustment | Renaming only the owned shared object root makes both nodes unready while liveness stays 200; restoring the root restores readiness without restart | `readiness_objects_results.json`: 2 supplemental |

## Recovery measurements and manifest

The final source inventory contains four seeded issues, one verified account,
one ready synthetic attachment, completed jobs and one pending job. Database
rows, ownership/state, object lengths/hashes, and pending payload/state are
compared to the written pre-backup inventory in the receipt. The attachment is
131,111 bytes, independently hashed before upload and after restored download.
All pre-restore sessions, API credentials and challenges are absent after
restore. Password hashes remain usable for fresh authentication. Jobs remain
paused until explicit policy selection.

The final snapshot took **0.797 seconds**. Restore command plus independent
database/object inventory readback took **0.434 seconds**; snapshot completion
to restored inventory was **1.487 seconds**. Those are this tiny local fixture's
data-restoration measurements. Full service cutover RTO and capacity SLOs were
not measured. The RPO boundary is the quiesced snapshot: one intentionally later
source record edit is absent from restored state. Workers resume only through
the explicit at-least-once policy; receiver deduplication remains necessary.

## Replay

After building the current program and obtaining the documented verified Caddy
fixture binary, run:

```sh
python3 verification/w7_w8_independent/run.py \
  --binary target/debug/diamond \
  --caddy /path/to/verified/caddy
python3 verification/w7_w8_independent/readiness_objects.py \
  --binary target/debug/diamond
```

`run.py` freezes the executable before dispatch and permits two independent
campaigns concurrently. Each campaign creates its own PostgreSQL Unix-socket
cluster, private shared object root, generated accounts/data and controlled
external receiver as applicable. Test clients use real HTTP, masked WebSockets,
and HTTPS/WSS sockets. No fake Diamond responses, mock database, OS trust
installation, or infrastructure deployment is used.

## Boundaries

- Actual browser watch controls and the tracker/catalog UI journeys are the
  implementing agent's separate browser evidence, not established by these
  native clients. Likewise, scaffolding/clean installation and older compatibility
  replay are separate evidence.
- Shared storage here is one local private filesystem and PostgreSQL uses local
  Unix sockets. The actual TLS proxy also runs on loopback. No Internet-facing
  host, remote database, SMTP/object-cloud provider, or multi-host failure
  guarantee is established.
- The retention floor is advanced as owned database fixture state to exercise
  resynchronization; automatic trimming at 10,000 rows is not exercised.
- Queue recovery resumes work in the main recovery drill. The separate explicit
  `cancel` recovery policy is not exercised. No wall-clock 30-minute connection
  expiry, long capacity run, or live stale-holder completion race is established.
- HTTP and application writes remain separate checked handler profiles.
  Healthy concurrent database conflicts may cause repeated external attempts;
  at-least-once is not universal exactly once. Handler-body changes deliberately
  leave old-version pending work unclaimed for explicit reconciliation/cancel.
- Initial failures and checker corrections remain preserved; a passing final
  replay does not erase those observations. Broad adversarial/distributed
  qualification and final release gates remain W9/W10 work.
