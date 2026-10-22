# W9 adversarial and distributed-failure qualification

Diamond **0.15.0** completes W9 within the bounded local profile described by the
[coverage record](w9/COVERAGE.md). The frozen final executable SHA-256 is
`4281f59a9eebda6db8dfa9169a88b6b568898474d194ce599ff1c7a75017a57d`; the
library SHA-256 is
`eba4885afbbc934d13373eafbc3cfb7dfe7393703338c83c0001444ca734b19b`.
The pre-W9 comparison was commit
`b60441f642746e7c54f3561104a221aeba2adb91`, executable SHA-256
`3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.

The campaign attacked the real HTTP, RPC, API, file, WebSocket, browser, worker,
PostgreSQL, backup/restore and shared-filesystem paths. It found five product
defects. Each was reproduced through a real interface, repaired, and replayed on
the exact final executable:

1. A running worker held its job row lock and blocked cancellation. Workers now
   validate without retaining that lock, and conditional fenced completion rolls
   back database work if cancellation or recovery wins.
2. The 10,000-job quota ran before stable-key resolution. An identical retry now
   returns its original job and a changed payload conflicts without consuming a
   new row.
3. A live server accepted uploads after its object root was replaced. Servers now
   bind the store marker at startup and revalidate the canonical private root and
   marker before reservations and physical path resolution.
4. Sixteen incomplete ordinary bodies starved all health probes. Two bounded
   health permits now keep liveness, readiness and metrics independently
   responsive.
5. Real browser tabs retained rendered private state after out-of-band session
   invalidation. Periodic event authorization now sends distinct authentication
   and access-loss closes, and the generated runtime clears component state for
   those outcomes while retaining state during transient node loss.

Baseline observations, exact repair replays, trust boundaries and corrected
verifier assumptions are retained in [the findings record](w9/FINDINGS.md) and
[oracle-correction record](w9/ORACLE_CORRECTIONS.md). The corrections are not
counted as product findings.

## Final hostile and failure evidence

- [Independent campaign](w9_independent/results.json): **8 campaigns, 39/39
  observations**, 100 helper HTTP requests, 12 hostile raw HTTP requests, 12 raw
  WebSocket handshakes, 5 upgraded hostile-frame schedules and 21 expected
  shutdown receipts. There were no unexpected exits, panics, forced cleanup
  kills, cleanup errors or retained roots.
- [Hostile transport campaign](w9/transport_results.json): **52/52 observations**
  across ambiguous framing and authority, methods/path encodings, cross-site
  inputs, cookies, CSRF, strict JSON, pipelining and post-case liveness.
- [Health starvation](w9/health_starvation_results.json), [live object-root
  replacement](w9/storage_root_replacement_results.json) and [artifact/credential
  boundaries](w9/artifact_boundary_results.json): **2/2, 2/2 and 3/3** direct
  observations respectively.
- [Tracker Chrome receipt](w9/live_workspace_browser_results.json): two real tabs
  rendered hostile SVG-shaped text literally, both cleared private state after an
  independent logout-all, re-login worked, and a deliberate node-A kill/restart
  retained valid state and delivered the next event.
- [Catalog Chrome receipt](w9/catalog_browser_results.json): anonymous draft
  denial, owner edit/publish, literal hostile content, a completed external job
  with one deduplicated receiver effect, private-state clearing on sign-out and
  anonymous public rendering.

## Sustained capacity and recovery

The [capacity receipt](w9/capacity_results.json) passed its fixed thresholds with
100 ordinary client threads and 100,004 records for **1,805.055 seconds**. It
recorded **90,238 HTTP responses at 49.992/s**, of which **84,562 (93.71%)** were
successful. Successful latency was 3.582 ms p50, 14.518 ms p95 and 16.835 ms p99,
well below the fixed 3-second p99 ceiling.

The nonsuccess responses were classified: 5,419 fixed API-rate-limit 429s; 127
cookie-session 401s at the documented 30-minute expiry; and 130 bounded 503s
around the deliberate node outage and admission pressure. The harness separately
recorded 59 refused and 3 reset connections while node B was intentionally absent.
Node B recovered in 3.234 seconds and worker two in 3.005 seconds. Both nodes ended
ready. All 15 accepted jobs reached `done` with 15 distinct receiver effects and no
incomplete work; all 15 typed events reached the original WebSocket before it
closed at 1,800.340 seconds. An 8 MiB deliberately slow upload published and
deleted in 12.825 seconds, and the 100,004-row access plan used its declared index.

Maximum RSS was 52,840/34,624 KiB for nodes A/B and 25,784/26,040 KiB for workers
one/two. All six process exits matched the schedule, including the two deliberate
SIGKILLs; no panic, forced cleanup or cleanup error occurred, and the temporary
root was removed.

## Regression and build gates

All preceding profiles were replayed against the same frozen 0.15.0 artifacts:

- [W7/W8](w9/w7_w8_regression/results.json): 77/77 observations, 666 helper HTTP
  requests, two nodes/two workers, jobs, leases, events, TLS/WSS, backup/restore,
  current-version replacement and 34 expected process receipts. TLS used the
  checksum-recorded official Caddy 2.11.7 binary.
- [W6](w9/w6_regression_results.json): 143/143 observations, 180 helper HTTP
  requests and 22 expected shutdowns across files, integrations, limits, DNS and
  the native consumer.
- [W5](w9/w5_regression_results.json): 294 observations and 1,956 HTTP requests
  across account lifecycle, cross-node revocation, credentials/scopes, API
  codecs/rates/faults and contract boundaries.
- [W3](w9/w3_regression_results.json): 185 semantic cases, 30 checker cases, 121
  wire cases, three regression replays, five native checks and 184 native corpus
  values.
- `cargo fmt --all --check`, strict all-target/all-feature Clippy, locked
  all-feature build/tests, warning-denied rustdoc and all 37 documentation examples
  passed. The crate continues to forbid authored unsafe code.
- The [sealed original-MVP replay](w9/mvp_regression_results.json) passed its full
  format/build/Clippy/test/rustdoc/embedding, W1-W6, diagnostics, startup/install
  and performance chain after all W9 source, documentation and verifier inputs
  were frozen. Its input-hash seal and executable/library hashes match this report.

Counts describe their individual receipts and overlap by design. They are not
summed into a synthetic score. Chrome DOM and visual state were paired with
independent PostgreSQL and receiver readback; backend suites do not substitute for
browser behavior.

## Boundary of the result

This is retained hostile evidence for W9, not proof of complete security or W10
release qualification. It uses synthetic identities/data, loopback networking,
owned local PostgreSQL clusters, controlled TLS/DNS/HTTP peers, local shared
directories and one Chrome environment. Public ingress, remote PostgreSQL TLS and
network partitions, distributed-filesystem replication, fsync/power-loss behavior,
malware scanning, SMTP, other browser engines, kernel compromise, a malicious
database/storage administrator and exhaustive protocol fuzzing remain outside the
evidence. The shared-store marker is an operator binding and can be copied by its
trusted administrator. External delivery remains at least once and requires
receiver deduplication.

W10 remains responsible for clean-install/release qualification, accessibility,
packaging, dependency review, final performance work and a current acceptance-
criterion record after any finishing changes.
