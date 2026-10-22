# W7 and W8 qualification

Diamond **0.14.0** implements W7 durable jobs/live applications and W8 operable
current-version deployment within the bounded Linux profiles documented in
[jobs/live applications](../docs/jobs-and-live-apps.md) and
[deployment/recovery](../docs/deployment.md). W9 and W10 remain planned. No public
infrastructure was provisioned, no host trust store was modified, and no release
security/capacity certification follows from these observations.

Executed final executable SHA-256:
`3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.
Library SHA-256:
`472de60a13ffc4b72019f050671175cf6a1633a07adf907feebb6fa3ecb0e624`.
MSRV stays 1.96.0. This is a minor expansion from 0.12.0, with five additive public
project methods, embedded scaffolding, and explicit new runtime profiles.

## Independent real-interface observations

The [six-gate frozen replay](w7_w8_independent/results.json) passed **77** observations,
**651** helper-tracked HTTP requests, real raw WebSockets, PostgreSQL, object bytes,
external receiver state, CLI and worker processes. A separate actual missing-object
readiness/recovery check passed **2** observations and **8** additional requests.
Total: **79 observations, 659 tracked HTTP requests**, 33 graceful process exits
and 3 intentional SIGKILL crash injections. Caddy probes/raw socket handshakes are
not included in that request counter. Every child receipt identifies the same
final executable. The two independent context roles separately implemented the
runtime and the contract-derived verifier; agreement alone was not the oracle.

| Behavior/caller | Independent observed result | Receipt |
| --- | --- | --- |
| Diamond submit/RPC/transaction → durable outbox | Abort leaves neither record mutation nor job; commit preserves both; repeat key returns same job; cross-tenant/reader/wrong-shape submissions deny; cancel fences pending work | [Core](w7_w8_independent/jobs_events_results.json) |
| Fresh restricted checked handlers → real DB/external destination | Two concurrent workers; transactional write/event/done atomicity; kill after receiver effect, actual lease expiry and recovery; stable receiver dedup; three-attempt dead letter; membership/account/API-scope limits remain current | [Core](w7_w8_independent/jobs_events_results.json) |
| Two socket nodes → protected event disclosures | Exact-origin authenticated typed delivery; missing/hostile origins and unauthorized users deny; reconnect replays durable cursor; resync and live revocation | [Core](w7_w8_independent/jobs_events_results.json) |
| Socket quotas, expiry, slow consumer | 32 active sockets; 33rd denied while HTTP stays usable; actual DB-clock expiry; out-of-retention cursor; unsolicited inbound action denied; 320 real 15.8 KiB publications disconnect unread client and recover permits | [Limits](w7_w8_independent/events_limits_results.json) |
| Compiler → binding/handler authority | Static names and exact wire payloads; missing/recursive/excess grants and invalid retry/delivery declarations reject | [Checker](w7_w8_independent/checker_results.json) |
| TLS proxy → alternating app servers/workers | Official checksum-verified Caddy 2.11.7; strict trusted fixture CA/hostname; 20 RPC requests split 10/10; compiled assets; exact binary transfer; WSS; forwarding spoof rejection; worker effect; healthy survivor after stop | [TLS/WSS](w7_w8_independent/tls_proxy_results.json) |
| Operations → snapshot/isolated restoration | Invalid config/active maintenance/nonempty target rejected; private encrypted quiesced snapshot; wrong key/tampering leave empty DB; exact record/account/object/job inventory after separate restore; old sessions/API tokens deny protected use; fresh login works; paused jobs inert until explicit resume | [Recovery](w7_w8_independent/deployment_results.json) |
| Current-version replacement → typed jobs | UI source edit preserves queue compatibility; server implementation edit does not execute old-version queued work; new matching jobs run; incompatible declared durable schema rejects | [Versions](w7_w8_independent/job_versions_results.json) |
| Readiness → missing actual object root | Both nodes become unready with root absent while liveness remains 200; return root and readiness recovers without restart | [Readiness](w7_w8_independent/readiness_objects_results.json) |

Snapshot command duration was **0.797 seconds**. Restore command plus independent
DB/object inventory took **0.434 seconds**; snapshot completion to verified isolated
inventory was **1.487 seconds**. These are local data-restoration timings, excluding
full cutover/operator time. One intentional record edit after the snapshot was
absent in the restored database, establishing the stated snapshot data-loss boundary.
A successful native GnuPG/pg_restore command alone was never treated as restoration.

## Real reference application/browser and developer workflows

The actual generated Diamond tracker and catalog ran with two PostgreSQL-backed
application servers and two real workers each. Chrome exercised the generated
programs rather than fixture HTML or handwritten browser clients.

- [Tracker receipt](live-workspace_browser_results.json): sign-in, queue background
  edit, automatic WebSocket state update, durable done status, comments, literal
  hostile Unicode/HTML text, independent DB/RPC readback, and cleared live/job/comment
  fields after logout.
- [Catalog receipt](catalog_browser_results.json): anonymous draft denial and empty
  public listing; owner read/edit; publish with live activity and actual receiver
  effect; done status; logout clears private form/live/job state; anonymous public
  listing displays published content as literal text. Independent receiver records
  the exact stable job key and body hash.
- [Scaffold receipt](web_eight_scaffold_results.json): **15** real CLI observations
  covering both embedded templates, exact source-file comparison, checking,
  building/reloading, non-clobbering new-directory behavior, invalid template,
  public-write/host/unknown operation rejection, public contract auth flags and
  tampered build refusal.

A compiler-checked `public_rpc` allowlist exposes only explicitly declared operations
with read effects. The catalog also declares separate public versioned JSON APIs.
Ordinary private RPC and typed host operations remain authenticated/policy checked.
No handwritten JavaScript, SQL or Rust was needed to express the examples' business
logic. The generic verifier uses ordinary native DB setup for isolated fixture data.
Screenshots were visually inspected in Chrome; no saved screenshot artifact is claimed.

## Repairs and replay record

The independent [findings](w7_w8_independent/FINDINGS.md),
[coverage map](w7_w8_independent/COVERAGE.md) and
[oracle corrections](w7_w8_independent/ORACLE_CORRECTIONS.md) retain the original
parser, legal-parent-ID, worker retry parameter and optional-scope reconstruction
failures with their fixed real replays. The schema/handler-version coupling was
identified during implementation review and verified after separation; there is
no claim of a preserved old-binary reproduction for that review finding.

Browser exploration also reproduced stale private display/form data after logout
in both reference applications. Explicit identity-change clearing fixes are replayed
in the final receipts; original observations remain in
[tracker initial](live-workspace_browser_initial_results.json) and
[catalog initial](catalog_browser_initial_results.json). A UI success message alone
was supplemented by actual job rows, receiver calls and typed live output.

A healthy job may repeat delivery after a transaction conflict. The verifier's
initial exactly-one-attempt expectation was corrected to the declared at-least-once
contract, retaining the observed duplicate. A test CA and two restore assertions
were corrected as verifier defects, without weakening certificate validation or
public/runtime authorization.

## Replays, supporting checks and limits

Use the JSON receipts as the authoritative current status of the preceding-profile
replays; these are separate from W7/W8's direct evidence:

- [Original MVP replay](web_eight_compatibility_results.json):
  `python3 verification/checkpoint_six.py --full --output-dir verification/web_eight_compatibility --report verification/web_eight_compatibility_results.json`.
- [W3 replay](web_eight_w3_replay.json):
  `python3 verification/web_three_independent.py --report verification/web_eight_w3_replay.json`.
- [W6 replay](web_eight_w6_replay_results.json):
  `python3 verification/w6_independent/run.py --binary target/debug/diamond --library target/debug/libdiamond.rlib --dependencies target/debug/deps --output-dir verification/web_eight_w6_replay --report verification/web_eight_w6_replay_results.json`.
- Supporting build/API checks: locked build, strict all-target/all-feature Clippy,
  formatting, warning-denied rustdoc and **37** documentation examples. There are no
  generated implementation-mirroring unit tests. Authored unsafe remains forbidden;
  Miri is not needed for a newly authored unsafe path.

The initial original-MVP replay's individual commands passed but its input-hash
seal was invalidated by concurrent documentation/verifier edits; that receipt is
preserved as [initial qualification](web_eight_compatibility_initial_results.json).
It is not treated as a passing final sealed qualification. The final replay runs
after source, docs and verifier inputs are frozen.

Independent six-gate replay:

```sh
python3 verification/w7_w8_independent/run.py --binary target/debug/diamond --caddy /path/to/official/caddy
python3 verification/w7_w8_independent/readiness_objects.py --binary target/debug/diamond
python3 verification/web_eight_scaffold.py
python3 verification/web_seven_eight_browser.py --app live-workspace
python3 verification/web_seven_eight_browser.py --app catalog
```

See the coverage map for unexercised cases. This is not exhaustive security or
capacity evidence: full live stale-holder acknowledgment racing, automatic 10,000
row retention trimming, the explicit restore-cancel branch, a 30-minute real-time
socket lifetime, capacity/SLOs, SMTP, remote PostgreSQL/TLS, shared filesystem
replication and actual off-host backup retention are not established here.
Current-version replacement is verified; deployed old/new compatibility remains
outside the user's scope. Snapshot/restore trusts native operators and pauses jobs;
receiver deduplication remains an explicit external contract. W9 broadens failure
and adversarial coverage; W10 qualifies the final release scope.
