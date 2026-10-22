# Independent W9 findings

The hostile baseline was the frozen `b60441f` executable with SHA-256
`3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.
The definitive final replay used the frozen 0.15.0 candidate with SHA-256
`4281f59a9eebda6db8dfa9169a88b6b568898474d194ce599ff1c7a75017a57d`.
The independent campaign confirmed two product defects. Both have direct
baseline reproductions and pass unchanged hostile replays on the final candidate.

## W9-I-01: Running cancellation was blocked by the active worker

**Baseline classification:** confirmed product defect.

`running_cancel.py` submitted a real external job, held the receiver response
after its deduplicated effect, observed the job as `running` at fence 1, and
cancelled from a second application node. Cancellation returned HTTP 503 after
2.0069 seconds, the row remained `running` at fence 1, and releasing the receiver
allowed the original attempt to commit `done`. The worker held the job row lock
for the handler transaction, so the documented running-state cancellation fence
could not commit within the request deadline.

**Final classification:** fixed and replayed.

The same schedule returned HTTP 200 in 8.2 ms, committed `cancelled` at fence 2,
and remained cancelled after the held worker resumed. The receiver recorded one
attempt and one deduplicated business effect. Evidence:
`running_cancel_baseline.json` and `running_cancel_final.json`.

## W9-I-02: Stable-key retries lost their meaning at the job row quota

**Baseline classification:** confirmed product defect.

`job_quota_dedup.py` created a real stable-key job, filled the application to
10,000 rows with unrelated terminal fixture rows, and retried through the real
RPC. Both the identical retry and a changed-payload retry returned HTTP 503.
The required outcomes were the original opaque job ID for the identical retry
and a conflict for the changed payload. The row count and original job stayed
unchanged, isolating the defect to quota classification before existing-key
resolution.

**Final classification:** fixed and replayed.

At 10,000 rows the identical retry now returns HTTP 200 with the original ID,
the changed payload returns HTTP 409 `aborted`, the row count stays 10,000, and
the node remains usable. Evidence: `job_quota_dedup_baseline.json` and
`job_quota_dedup_final.json`.

## Verifier defects kept separate

The first database-loss checker expected a 5xx readiness response. Diamond
returned 403 while liveness stayed 200, protected RPC returned 503, and the
existing socket closed. The contract requires non-ready rather than a particular
error class, so the oracle was corrected to require any non-200 response.

The first storage-loss invocation omitted the controlled `inventory` receiver
required by its declared runtime capability. Diamond correctly refused startup.
The harness was corrected before storage behavior was exercised. Neither case
is counted as a product finding; details are in `ORACLE_CORRECTIONS.md`.

The primary W9 campaign separately discovered and repaired live object-root
replacement, health-probe admission starvation, and retained private browser
state after out-of-band session invalidation. This independent campaign does
not claim discovery credit for those issues. Its final storage campaign does
independently exercise replacement, corruption, missing bytes, and live repair
against the final executable.
