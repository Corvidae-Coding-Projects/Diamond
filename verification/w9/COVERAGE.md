# W9 final-candidate coverage

## Frozen artifacts

- Diamond 0.15.0 executable SHA-256:
  `4281f59a9eebda6db8dfa9169a88b6b568898474d194ce599ff1c7a75017a57d`
  (`/tmp/diamond-w9-final-4281f59a`).
- Library SHA-256:
  `eba4885afbbc934d13373eafbc3cfb7dfe7393703338c83c0001444ca734b19b`.
- Frozen pre-W9 baseline: commit
  `b60441f642746e7c54f3561104a221aeba2adb91`, executable SHA-256
  `3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.
- Rust toolchain: `rustc 1.96.0` and Cargo 1.96.0.
- TLS regression proxy: official Caddy 2.11.7 binary SHA-256
  `678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9`;
  downloaded archive SHA-512
  `a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7`.

All final behavioral receipts below identify the executable hash above. The W3,
W5 and W6 native checks used the library at `target/debug/libdiamond.rlib`; its
recorded hash matched the frozen library above while its target-directory location
made Cargo dependencies available to `rustc`.

## Real-interface matrix

| Surface | Final evidence | Covered behavior |
| --- | --- | --- |
| Hostile HTTP/RPC transport | [`transport_results.json`](transport_results.json), 52/52 observations | Methods and path forms; duplicate/conflicting Host, Origin, Cookie, CSRF and content-length; forwarding and transfer-encoding confusion; cross-site inputs; strict JSON, raw pipelining, post-case liveness and log canaries |
| Independent attack/failure campaign | [`../w9_independent/results.json`](../w9_independent/results.json), 8 campaigns and 39/39 observations | 100 helper HTTP requests, 12 hostile raw requests, 12 raw WebSocket handshakes, five hostile upgraded-frame schedules, 21 expected shutdown receipts, no unexpected exit, panic, forced cleanup, cleanup error or retained root |
| File-root replacement | [`storage_root_replacement_results.json`](storage_root_replacement_results.json), 2/2 | Two real nodes, replaced live root, readiness failure, no fallback reservation/bytes, exact-root restoration behavior |
| Health admission starvation | [`health_starvation_results.json`](health_starvation_results.json), 2/2 | All 16 ordinary permits held by incomplete bodies while bounded liveness, readiness and metrics stay responsive |
| Artifact and credential boundaries | [`artifact_boundary_results.json`](artifact_boundary_results.json), 3/3 | Planted server-only canary, guessed private paths, hidden RPC, absent API profile, browser assets, logs, password, CSRF and session values |
| W7/W8 deployment/jobs/events/TLS replay | [`w7_w8_regression/results.json`](w7_w8_regression/results.json), 77/77 observations and 666 helper HTTP requests | Two nodes/two workers, jobs, leases, live policy recheck, raw sockets, encrypted backup/restore, current-version replacement, limits, official-Caddy HTTPS/WSS, 34 expected process receipts |
| W6 files/integrations replay | [`w6_regression_results.json`](w6_regression_results.json), 143/143 observations and 180 helper HTTP requests | Shared private files, exact bytes, outbound HTTP/TLS/DNS, secrets, redirects, cancellation, limits, native consumer and 22 expected shutdowns |
| W5 accounts/API replay | [`w5_regression_results.json`](w5_regression_results.json), 294 observations and 1,956 real HTTP requests | Account lifecycle, two-node revocation, credentials/scopes, API codecs/rates/faults, mutation and contract-boundary regressions |
| W3 language/frontend replay | [`w3_regression_results.json`](w3_regression_results.json) | 185 semantic cases, 30 checker cases, 121 wire cases, three regression replays, five native checks and 184 native corpus values |
| Real Chrome tracker | [`live_workspace_browser_results.json`](live_workspace_browser_results.json) | Two tabs, literal hostile live text, out-of-band logout-all clearing, re-login, deliberate node-A SIGKILL/restart, state retention for transient loss and later live delivery |
| Real Chrome catalog | [`catalog_browser_results.json`](catalog_browser_results.json) | Anonymous draft denial, owner read/edit, literal hostile content, publish/live update, completed external job with one deduplicated receiver effect, sign-out clearing and anonymous public rendering |
| Sustained capacity/recovery | [`capacity_results.json`](capacity_results.json) | Fixed 100-client, 100,000-record and 30-minute thresholds; two nodes/two workers, mixed browser/API/assets/read traffic, long WebSocket, slow upload, worker/node faults, RSS/database/queue/latency/error sampling |

Counts describe their own receipts and overlap by design; they are not summed into
a synthetic coverage score. Browser observations come from Chrome DOM/visual state
plus independent PostgreSQL/receiver readback. Backend suites do not substitute for
those browser observations.

## Source and caller coverage

| Boundary | Production code read or changed | Actual callers/recovery paths exercised |
| --- | --- | --- |
| HTTP admission, body limits, health and shutdown | `src/project/connected.rs`, `src/project/connected/profiles.rs`, `src/project/deployment.rs` | Raw sockets, generated browser fetches, API clients, 16 held bodies, drain/restart, dependency loss, liveness/readiness/metrics |
| Sessions, permissions and credentials | `src/project/accounts/*`, `src/project/identity.rs`, policy/storage dispatch | Browser cookies/CSRF, bearer APIs, logout-all, disable/re-enable, membership removal, expiry/rate pressure, two-node stale callers |
| Durable jobs and external effects | `src/project/jobs.rs`, `src/project/jobs/worker.rs`, `src/project/integrations.rs` | RPC submit/status/cancel, two workers, receiver-held attempt, quota edge, SIGSTOP/SIGKILL, real lease expiry, restore policies, stable receiver deduplication |
| Durable live events | `src/project/events.rs`, `src/project/runtime.js` | Raw upgrades/frames, generated `<watch>`, retention/resync, backpressure, 30-minute socket, access/session close codes, transient reconnect and stale-tab clearing |
| Shared files | `src/project/files.rs`, `src/project/files/operations.rs`, `src/project/files/transport.rs` | Browser/native upload/download/delete, two nodes, corruption/missing bytes, interrupted publication, live root removal/replacement/restoration, readiness and cleanup |
| Build/browser artifacts | `src/project/artifact.rs`, `src/project/browser.rs`, `src/project/runtime.js` | Real builds, byte scans, SSR/hydration, literal hostile text, source maps/assets/contracts, private path guesses and browser navigation/reload |
| Deployment and recovery | `src/project/deployment.rs`, `src/project/deployment/backup.rs`, CLI wiring in `src/main.rs` | Deployment validation, Caddy HTTPS/WSS, node/worker replacement, encrypted backup, tamper/wrong-key rejection, isolated restore and explicit resume/cancel |

## Capacity measurements

The final capacity replay ran 100 ordinary client threads against 100,004 records
for 1,805.055 seconds. It recorded 90,238 HTTP responses at 49.992/s: 84,562 were
successful (93.71%), with successful latency of 3.582 ms p50, 14.518 ms p95 and
16.835 ms p99. The fixed gates were at least 1,800 seconds, at least 90% successful
responses and successful p99 below 3 seconds.

The receipt classifies every nonsuccess: 5,419 API rate-limit 429s, 127 cookie
session 401s at the documented 30-minute expiry, and 130 bounded 503s around the
planned node outage and admission pressure. Fifty-nine refused and three reset
connections occurred while node B was deliberately absent. Node B recovered in
3.234 seconds and worker two in 3.005 seconds; both nodes ended ready. The final
database retained exactly 100,004 records, 15 completed jobs, no incomplete jobs
and 15 typed events. The deduplicating receiver observed exactly 15 effects. The
original WebSocket received all 15 events and closed at 1,800.340 seconds. An 8 MiB
slow upload published and deleted in 12.825 seconds. The sampled boundary query
used its declared index.

Maximum resident sets were 52,840 KiB for node A, 34,624 KiB for node B, 25,784
KiB for worker one and 26,040 KiB for worker two. All six process receipts matched
their expected exits, including the two scheduled SIGKILLs. There were no panics,
forced cleanup kills or cleanup errors, and the isolated root was removed.

## Deliberate limits

This is bounded hostile evidence rather than proof of total security. It uses
synthetic identities/data, loopback networking, owned local PostgreSQL clusters,
local shared directories, controlled DNS/TLS/HTTP peers and one Chrome environment.
It does not qualify public ingress, remote PostgreSQL TLS/network partitions,
distributed-filesystem replication or fsync/power-loss semantics, malware scanning,
SMTP, arbitrary browser engines, kernel compromise, a malicious database/storage
administrator, or exhaustive protocol fuzzing. The shared-store marker is an
operator binding and can be copied by that trusted administrator. External job
delivery remains at least once and depends on receiver deduplication. W10 still owns
clean-install/release, accessibility, packaging, dependency and full acceptance-
criterion qualification.
