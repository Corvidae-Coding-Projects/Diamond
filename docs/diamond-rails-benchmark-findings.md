# Diamond/Rails benchmark findings ledger

This ledger records repair candidates and interpretation limits discovered by
the real benchmark. A fast result is not evidence that a response was correct,
and an explicit capacity rejection is not successful throughput. The JSON
campaign checkpoint remains the primary quantitative evidence.

The subsequent deadline, workload/tenant fairness, and shared retry-budget
implementation and live qualification are recorded in
[SCHEDULING-RESULTS.md](../verification/performance-remediation/SCHEDULING-RESULTS.md).
All scheduling policies have checked project configuration documented in the
[runtime guide](runtime-scheduling.md). Earlier measurements below retain their
original binary and method boundaries.

Status vocabulary:

- **Open**: reproduced and worth repairing or making an explicit product decision.
- **Resolved in working tree**: a fix exists locally but is not necessarily committed.
- **Method limit**: not a product defect; it bounds what the results can establish.
- **Campaign complete**: the full requested matrix finished and the finding is
  based on its final checkpoint.

## DF-PERF-001: mixed reads and writes expose serialization aborts

- Status: **Resolved in working tree — bounded runtime conflict handling**
- Classification: successful safety behavior with material availability cost
- Observed interface: `POST /api/v1/update` returns HTTP 409
  `{"error":"aborted"}` under the mixed page/read/update workload.
- Reproduction evidence:
  - concurrency 8, 0.5 seconds: 4 conflicts in each of two independent repeats;
  - concurrency 32, 0.5 seconds: 3 then 2 conflicts;
  - concurrency 128, 0.5 seconds: 6 then 4 conflicts;
  - every independent database oracle still passed; no partial or corrupt write
    was observed.
- Likely interaction to investigate: Diamond's serializable request transactions
  combine range/page reads with concurrent writes. The evidence establishes the
  conflict surface, not the exact PostgreSQL dependency graph.
- Why it matters: applications must currently handle explicit aborts even when
  generated updates are distributed across different issue rows. Tail behavior
  and useful write throughput degrade as mixed concurrency rises.
- Repair directions to evaluate:
  1. minimize transaction read/write footprints without weakening policy checks;
  2. add bounded runtime retries only where replay is proven safe and idempotent;
  3. expose a generated, typed conflict-handling path so applications cannot
     forget the 409 case;
  4. retain explicit 409 for outcomes the runtime cannot safely replay.
- Acceptance evidence for a repair: replay the mixed campaign at concurrency 8,
  32, and 128; show fewer conflicts without duplicate creates, stale policy
  authorization, concealed unknown outcomes, or a weaker isolation invariant.
- Implemented Category A repair: ordinary relational RPC/API operations whose
  checked transitive effects contain only database reads/writes retry confirmed
  serialization/deadlock aborts at most twice. Each attempt reopens the store and
  rechecks current authorization. Actions, host effects, generic aborts, and
  unknown commit outcomes do not enter this retry path. Attempts use randomized
  delays and a two-second retry-start window. Exhaustion remains an explicit 409.
- Final real regression: a held row update forced a PostgreSQL serialization
  failure; the request retried successfully. In the companion race, revoking
  the credential before releasing the update made the fresh attempt return 401
  and left the protected row unchanged. Exact successful-create counts passed
  in all eight mixed cells. One conflict exhausted its retry budget at
  concurrency 128 in that bounded fast-storage replay; the repair handles
  contention without promising that contention can never abort a request.

Replay:

```bash
python3 benchmarks/rails-comparison/campaign.py \
  --dataset 100 --durations 0.5 --concurrency 8 32 128 \
  --repeats 2 --warmup 0.25 \
  --report benchmarks/rails-comparison/results/conflict-replay.json
```

## DF-PERF-002: overload shedding has no client backoff signal

- Status: **Resolved in working tree — bounded queue and replay contract**
- Classification: capacity behavior and retry-contract gap
- Observed interface: at concurrency 32 and 128, Diamond returns fast HTTP 503
  responses while preserving successful work and database correctness.
- Reproduction evidence from the 0.5-second high-concurrency qualification:
  - concurrency 32: 3,080 and 3,147 capacity rejections across two repeats;
  - concurrency 128: 2,917 and 3,004 capacity rejections across two repeats;
  - successful requests continued in every cell and all state oracles passed.
- Source observation: the generic 503 response maps to `{"error":"unavailable"}`
  and supplies no `Retry-After` header. Immediate clients can therefore generate
  a retry storm and inflate offered-throughput numbers with cheap rejections.
- Repair directions to evaluate:
  1. add a short bounded admission wait before rejection;
  2. return an explicit overload code and safe `Retry-After` guidance;
  3. generate exponential-backoff behavior in Diamond clients;
  4. document when operators should add nodes rather than enlarge local queues.
- Acceptance evidence for a repair: a bounded overload campaign must preserve
  memory and latency limits, keep successful state correct, provide a machine-
  readable backoff signal, and avoid unbounded queues.
- Implemented Category A repair: checked `[runtime]` limits, bounded FIFO waiting,
  a queue deadline, `503 overloaded`, `Retry-After`, explicit pre-execution replay
  proof, connection closure for unread bodies, and generated capped exponential
  backoff with jitter. Overload logging is aggregated; health metrics expose
  queue use and ordinary rejection/retry counters. The same admission component
  protects apps, assets, and Pages; canceled Pages work retains its permit.
- Final real regression: zero overload rejections in all eight mixed cells at
  concurrency 1/8/32/128. With one admitted request, two queue slots, and a held
  table lock, three excess/expired requests received the explicit safe overload
  response; only the admitted create persisted. A disconnected queued client
  released capacity. The emitted browser helper waited before retrying, stopped
  at three attempts, honored cancellation and identity replacement, and did not
  retry a real database-unavailable 503. Aggregate logging and recovery passed.

## DF-PERF-003: fixed API admission ceilings blocked throughput measurement

- Status: **Resolved in working tree**
- Original observation: the first real replay reached Diamond's hard-coded
  600/300/120 global/peer/credential API counters and returned HTTP 429 before a
  throughput phase completed.
- Local repair: `[identity.api_requests_per_minute]` now permits an explicit,
  validated 10..10,000,000 range while retaining secure 600/300/120 defaults.
  Invalid bounds and `global < peer < credential` inversions fail checking.
- Verification: focused default/bounds/order tests, full Rust tests and doctests,
  Clippy with warnings denied, and the real quick/campaign qualification replays.
- Remaining action: review, commit, and keep the high benchmark ceiling confined
  to this fixture. It is not a recommended public deployment setting.

## BENCH-001: process metrics exclude PostgreSQL and the load generator

- Status: **Resolved in working tree — expanded measurement**
- Current evidence: CPU and RSS/PSS/USS cover only the active Diamond or Puma
  process. PostgreSQL and the Python client are intentionally outside the value.
- Consequence: the report answers application-process efficiency, not total
  service cost per request.
- Follow-up: add a companion system-cost report for the owned PostgreSQL process
  and client, while keeping the application-only series for diagnosis.
- Implemented: each sustained cell now reports separate CPU and RSS/PSS/USS
  series for the application process tree, owned PostgreSQL process tree, and
  Python client. This includes Puma workers in separately labeled worker
  profiles. Sampling adds overhead, and very short-lived processes between
  samples may be missed; these measurements do not equal whole-host energy cost.

## BENCH-002: one process is equal deployment count, not equal core policy

- Status: **Method limit**
- Current configuration: one Diamond process with its bounded native runtime;
  one Puma process with four threads and four database connections. Rails is
  exercised both with and without YJIT.
- Consequence: this is an honest one-process application comparison, but not a
  tuned maximum-throughput comparison for either stack. CRuby's process model
  and Diamond's request admission policy use the host differently.
- Follow-up: add separately labeled tuned-deployment profiles, including Rails
  worker counts/thread pools and multiple Diamond nodes. Do not replace the
  fixed one-process baseline.
- Implemented: `--rails-workers`, `--rails-threads`, and `--diamond-runtime`
  configure explicit, recorded alternative profiles without changing defaults.
  Runtime resource bounds remain checked by Diamond. This does not establish a
  maximum-throughput optimum or a multi-node ranking; those remain different
  experiments rather than product defects.

## BENCH-003: loopback uses a new HTTP connection for every request

- Status: **Original server-capability claim disproved; harness repaired**
- Correction: the managed Axum server already supports persistent connections.
  The earlier blanket claim that Diamond closed all connections confused the
  legacy single-file HTTP adapter with the project server. A nonexistent route
  in the old denied-read fixture also dropped an unread request body (BENCH-007).
- Consequence: the benchmark includes connection setup and does not establish
  performance behind a production reverse proxy with keep-alive or HTTP/2.
- Implemented: `--keep-alive` retains a connection per client worker. Each
  application must pass an actual same-socket qualification across successful
  and policy-denied requests. Overload explicitly closes unread-body connections
  so clients can safely reconnect after waiting. TLS/proxy/HTTP2 remain outside
  this loopback suite.

## BENCH-004: logical state equality does not make database caches cold

- Status: **Method limit**
- Current safeguard: each measured cell warms its fresh application process,
  then resets to 1,000 normalized rows. Diamond and both Rails modes must produce
  the same logical-state SHA-256 before timing.
- Remaining difference: PostgreSQL buffer/cache history and each framework's
  physical schema/index implementation are not byte-identical or forcibly cold.
- Follow-up: retain the realistic warm-cache campaign, and add a separately
  labeled cold-cluster profile only if cold-start database behavior matters.

## BENCH-005: host scheduling is observed, not controlled

- Status: **Method limit**
- Current safeguard: only one application process is active during a cell;
  variant order rotates; host load average is recorded before and after; every
  configuration has two repetitions.
- Remaining risk: CPU frequency, thermals, scheduler placement, and unrelated
  host activity are not pinned by the harness.
- Follow-up: repeat decision-grade runs on a quiet dedicated host with recorded
  governor/turbo state and deliberate CPU affinity. Treat the present campaign
  as this machine's result, not a universal framework ranking.
- Implemented: optional inherited `--cpu-affinity`, plus recorded effective CPU
  affinity and per-CPU frequency-governor names. This does not reserve the host,
  fix thermals, or turn off unrelated work. The dedicated-host qualification is
  an environmental constraint and remains explicit.

## BENCH-006: the quick qualification is not a performance predictor

- Status: **Campaign complete / method warning**
- Final sustained evidence: Rails usually has higher successful mixed-workload
  throughput, including every concurrency in the two ten-minute repetitions.
  Diamond retains substantially lower PSS and much lower successful-response
  p95 latency at concurrency 128 because it rejects excess admission explicitly.
- Contrast: the earlier 0.75-second operation-isolated qualification made
  Diamond look uniformly faster. It did not include the sustained mixed
  page/read/write interaction, long-run admission behavior, or adequate YJIT
  warmup represented by this campaign.
- Rule: do not use the quick-run numbers for a framework-speed claim. Report
  successful rather than offered throughput, and present throughput, tail
  latency, rejection rate, CPU, and PSS together.

## BENCH-007: the old denied-read phase called a missing route

- Status: **Resolved in working tree — benchmark correctness repair**
- Reproduction: `HttpTarget.request('denied', ...)` sent
  `POST /api/v1/denied`, which neither fixture declared. The old oracle accepted
  its 404. This phase did not test authorization, and Diamond closed that
  connection after dropping the unread request body.
- Repair: send the existing unauthorized issue ID to the declared `/read`
  endpoint and require 403. HTTP status counts are now retained per operation.
  The corrected fixture fails if that read succeeds or falls through to a 404.
- Consequence: the old 72-cell run below is retained as historical evidence of
  admission pressure and application-process measurements. Its 10% denied-read
  component was invalid, so it is not a fully qualified policy-workload ranking.
  The corrected follow-up uses a new report rather than overwriting that run.

## Corrected remediation follow-up

- Status: **PASS — 24/24 cells**, final binary
  `e7d6462807b35e029d48d9522db1f60975a2b30363a0195c281238f62066cd15`.
- Configuration: 30 seconds, two repeats, two-second warmups, identical logical
  baselines, concurrency 1/8/32/128, all three variants, persistent connections
  and capacity backoff. The corrected denied phase required an actual 403.
- Report: `benchmarks/rails-comparison/results/remediation-30s.json` and `.md`.
- Across all variants: **zero capacity rejections, transaction conflicts,
  unexpected responses, and transport errors**. All independent state oracles
  passed. All owned processes stopped cleanly and cleanup reported no errors.
- Diamond's two-repeat averages:

  | Concurrency | Successful req/s | p95 ms | Mean PSS MiB | App CPU % |
  |---:|---:|---:|---:|---:|
  | 1 | 121.8 | 12.8 | 12.4 | 9.3 |
  | 8 | 159.0 | 64.2 | 13.9 | 14.1 |
  | 32 | 153.5 | 236.5 | 15.2 | 13.7 |
  | 128 | 157.1 | 907.5 | 18.3 | 14.0 |

- The accepted work now waits: the earlier apparent successful-tail advantage
  from immediate rejection disappears. This run verifies admission behavior;
  changes to connections, policy-denial coverage, pacing, measurement overhead
  and host activity preclude treating it as an isolated speedup experiment.
  Some other verification workloads ran during this campaign, so its throughput
  and latency are acceptance evidence rather than a dedicated-host ranking.
- An additional six-cell smoke profile passed with two Puma workers/two threads,
  two Diamond database/blocking workers, and inherited CPU affinity 0..3.
- The final targeted fast-storage replay passed 20 scenarios; one explicit 409
  remained after its bounded retry budget. The separate 152-check delivery/Pages,
  durable-action/unknown-commit, health-starvation, and asset-only HTTP replays
  passed. Details and retained receipts live in
  `verification/performance-remediation/`.
- The full 120/600-second matrix has not been rerun after remediation. Warm-cache
  behavior and non-dedicated-host constraints remain method limits, rather than
  unresolved language defects.

## Historical sustained campaign

- Status: **PASS — 72/72 measured cells passed**
- Configuration: 30, 120, and 600 seconds; concurrency 1, 8, 32, and 128;
  two repetitions; Diamond, Rails without YJIT, and Rails with YJIT; ten-second
  warmup followed by an identical-state reset for every measured cell.
- Checkpoint: `benchmarks/rails-comparison/results/sustained.json`
- Correctness and lifecycle result: zero failed cells, unexpected responses, or
  transport errors; every independent database oracle passed; every owned
  process exited cleanly. Seven Diamond serialization conflicts and 21,288,441
  Diamond capacity rejections are classified separately from successful work.
- Ten-minute, two-repeat averages:

  | Concurrency | Variant | Successful req/s | p50 ms | p95 ms | p99 ms | CPU % | Mean PSS MiB | Success rate |
  |---:|---|---:|---:|---:|---:|---:|---:|---:|
  | 1 | Diamond | 121.1 | 8.9 | 11.1 | 15.1 | 9.9 | 12.7 | 100.00% |
  | 1 | Rails, no YJIT | 154.9 | 6.4 | 10.9 | 12.1 | 17.4 | 113.4 | 100.00% |
  | 1 | Rails, YJIT | 146.5 | 6.6 | 11.1 | 13.2 | 17.0 | 122.1 | 100.00% |
  | 8 | Diamond | 179.3 | 48.1 | 61.7 | 79.2 | 15.3 | 14.5 | 100.00% |
  | 8 | Rails, no YJIT | 211.7 | 37.2 | 53.7 | 67.2 | 22.8 | 117.1 | 100.00% |
  | 8 | Rails, YJIT | 216.8 | 36.6 | 51.8 | 59.5 | 23.5 | 131.9 | 100.00% |
  | 32 | Diamond | 200.1 | 90.7 | 111.0 | 123.0 | 70.4 | 15.5 | 2.74% |
  | 32 | Rails, no YJIT | 216.6 | 150.0 | 180.5 | 196.0 | 23.4 | 121.7 | 100.00% |
  | 32 | Rails, YJIT | 215.8 | 150.0 | 178.5 | 194.0 | 23.5 | 136.0 | 100.00% |
  | 128 | Diamond | 199.3 | 101.0 | 122.5 | 149.5 | 69.2 | 18.2 | 2.75% |
  | 128 | Rails, no YJIT | 218.4 | 600.0 | 673.0 | 708.5 | 23.3 | 121.2 | 100.00% |
  | 128 | Rails, YJIT | 224.3 | 599.5 | 682.5 | 724.5 | 23.9 | 134.7 | 100.00% |

- Interpretation: Rails sustains roughly 8% to 28% more successful requests in
  these ten-minute cells. Diamond uses roughly one eighth to one tenth of the
  Rails application-process PSS. Diamond's much lower high-concurrency tail
  latency is an overload-policy result, not free capacity: only about 2.75% of
  completed responses at concurrency 32 and 128 are successful because the
  unpaced client immediately retries after bare 503 responses.
- Repeat behavior: the ten-minute Diamond throughput repetitions were
  120.0/122.3, 179.3/179.4, 189.6/210.5, and 197.5/201.2 req/s as concurrency
  rose. Rails without YJIT was 148.4/161.4, 203.8/219.7, 210.5/222.6, and
  208.1/228.6; Rails with YJIT was 147.1/145.8, 217.6/216.0, 212.1/219.4, and
  223.0/225.6. The larger no-YJIT spread reinforces the dedicated-host limit in
  BENCH-005; it does not reverse the broad throughput conclusion.
- YJIT result: YJIT is not a consistent win in this one-process profile. It is
  slightly faster at some concurrency levels, slightly slower at others, and
  uses about 9 to 15 MiB more mean PSS than Rails without YJIT.
- Memory review: across all cells Diamond mean PSS ranged from 12.6 to 20.3 MiB,
  Rails without YJIT from 112.2 to 122.1 MiB, and Rails with YJIT from 121.0 to
  138.3 MiB. Fresh processes are used per cell, so this matrix does not by itself
  establish or exclude a long-lived-process memory leak.
