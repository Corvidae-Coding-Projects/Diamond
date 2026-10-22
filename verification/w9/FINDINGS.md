# W9 reproduced findings and repairs

The baseline executable was Git commit `b60441f642746e7c54f3561104a221aeba2adb91`
with SHA-256
`3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.
The stale-browser exploration first ran after the other four repairs on candidate
`06fe2a881ec4d60a564f87e2c380abe698c1fc4196d9618a0240d208a9e3c5f3`.
Every final replay uses Diamond 0.15.0 executable SHA-256
`4281f59a9eebda6db8dfa9169a88b6b568898474d194ce599ff1c7a75017a57d`.

## W9-P-01: running cancellation was blocked by the worker

The baseline worker held the job row lock throughout handler execution. A real
receiver held a running attempt while another node tried to cancel it. Cancellation
returned 503 after about two seconds, did not advance the fence, and the released
worker committed `done`.

The worker now validates its fence without retaining the row lock and conditionally
commits completion. Cancellation returned 200 in milliseconds, advanced the fence
to 2, and the stale attempt could not commit database changes or completion. External
delivery retains its documented at-least-once boundary. See
[`running_cancel_baseline.json`](../w9_independent/running_cancel_baseline.json) and
[`running_cancel_final.json`](../w9_independent/running_cancel_final.json).

## W9-P-02: stable-key retry failed at the job quota

At 10,000 retained rows, the baseline checked quota before resolving an existing
deduplication key. Both an identical retry and a changed-payload conflict returned
503. The implementation now resolves the key under the application advisory lock
before applying quota to a genuinely new job. The exact retry returns its original
ID, the changed payload returns 409, and the row count stays 10,000. See
[`job_quota_dedup_baseline.json`](../w9_independent/job_quota_dedup_baseline.json)
and [`job_quota_dedup_final.json`](../w9_independent/job_quota_dedup_final.json).

## W9-P-03: a live server accepted a replacement object root

Replacing the configured directory made baseline readiness fail, but the already
running server still reserved, uploaded, and finalized a new object in the empty
replacement path. The adapter now binds the marker identity at startup and
revalidates the canonical private directory and marker before every reservation and
physical path resolution. The final replay returns 503 before writing any
replacement bytes and recovers after the exact original root is restored. See
[`storage_root_replacement_baseline.json`](storage_root_replacement_baseline.json)
and [`storage_root_replacement_results.json`](storage_root_replacement_results.json).

The marker remains an operator binding rather than proof of shared-mount identity.
A trusted administrator can copy it to separate roots; remote filesystem,
replication, fsync and power-loss behavior remain outside this adapter's evidence.

## W9-P-04: ordinary slow bodies starved health probes

Sixteen incomplete ordinary request bodies occupied every request permit in the
baseline. Liveness, readiness and metrics all returned 503 until the three-second
body deadline released capacity. Health endpoints now have two dedicated bounded
permits after common request validation. With all ordinary permits held, final
liveness and metrics return 200 in about one millisecond and readiness returns 200
in about ten milliseconds. See
[`health_starvation_baseline.json`](health_starvation_baseline.json) and
[`health_starvation_results.json`](health_starvation_results.json).

## W9-P-05: stale tabs retained private state after session invalidation

Two real Chrome tabs displayed the same private issue and a literal hostile live
comment. An independent client ended all of Alice's sessions. The server stopped
both subscriptions, but the generated clients kept private issue/comment state and
reported that previous local state was preserved.

Periodic WebSocket authorization checks now close with distinct authentication or
access-loss codes. The generated runtime clears all component state on either code,
while ordinary node/network closes continue their bounded reconnect path. In the
final two-tab replay, session invalidation cleared workspace, project, issue, live
text and result fields in both tabs. Re-login succeeded. A separate node-A restart
retained valid state and delivered the next live event. See
[`browser_stale_session_baseline.json`](browser_stale_session_baseline.json) and
[`live_workspace_browser_results.json`](live_workspace_browser_results.json).

## Verifier corrections kept separate

The artifact-boundary fixture declares no public HTTP API. Its first oracle wrongly
required `/api/v1/_contract` to return 200; the correct response is 404.
The private server canary, source, contract and runtime credentials were absent from
served files and responses before and after that correction. The independent
campaign separately records its readiness status-class and missing receiver-binding
corrections in
[`ORACLE_CORRECTIONS.md`](../w9_independent/ORACLE_CORRECTIONS.md). None are counted
as product findings.
