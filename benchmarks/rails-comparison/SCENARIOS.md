# Benchmark scenarios and acceptance oracle

These expectations are written before running the benchmark. A timing result is
publishable only when every functional oracle passes. A fast incorrect response
is a benchmark failure, not a performance win.

## S1: isolated, equivalent setup

- Start one benchmark-owned PostgreSQL cluster on a Unix socket.
- Start one release-mode Diamond process and one production-mode Rails/Puma
  process. Puma uses one process and four threads; Diamond uses its normal
  bounded runtime.
- Give each application its own logical schema in the same database cluster.
- Seed the same number of issues, with the same titles, details, ranks, owner
  relationship, and one inaccessible workspace.
- Issue one bearer credential per application with only `page`, `read`,
  `create`, and `update` authority for the accessible workspace.
- Apply the same durable global, peer, and credential admission accounting in
  both applications, with an explicit high benchmark ceiling.

Expected: both health checks pass; both data stores have the requested initial
row count; credentials do not appear in the report.

## S2: authenticated reads

For each application and concurrency level:

- `page`: fetch the first 20 issues and the total count.
- `read`: fetch issue zero and verify its exact Unicode-bearing contents.

Expected: every response is HTTP 200, the page contains 20 well-shaped cards,
and the read body matches the seeded value exactly.

## S3: authorization denial

- Ask the accessible-workspace credential to read an issue owned through the
  inaccessible workspace.

Expected: every response is an authorization-safe denial (HTTP 403 or 404),
never issue content and never HTTP 200.

## S4: authenticated writes

- `update`: distribute generated, request-specific changes across seeded issues.
- `create`: repeatedly create generated issues in the accessible workspace.

Expected: every response is HTTP 200 and returns the value produced by that
request. After load stops, direct PostgreSQL readback observes the final update
and exactly the number of successful creates reported by the client.

## S5: measurements

For each measured phase, after an untimed warm-up, capture:

- request count, unexpected-response count, and throughput;
- p50, p95, p99, and maximum client-observed latency;
- process CPU seconds and CPU microseconds per completed request;
- sampled RSS, proportional-set size (PSS), and unique-set size (USS).

Expected: zero unexpected responses. Any phase with a functional error makes
the overall report fail and its timings non-comparable.

## S6: provenance and cleanup

Expected report provenance includes the Git revision and dirty state, operating
system and CPU, Ruby/Rails/Puma/PostgreSQL versions, binary and lockfile hashes,
dataset size, concurrency levels, duration, repeats, process model, and whether
Ruby YJIT was enabled. Only benchmark-owned application and database processes
are stopped.

## S7: sustained mixed-workload campaign

`campaign.py` uses a predeclared 35% read, 25% page, 20% update, 10% create,
and 10% denied-request mix. Each application/concurrency/duration/repetition
cell starts a fresh application process, qualifies every operation, warms that
exact runtime, then resets issues and admission counters to the same logical
baseline before measurement. The normalized database rows must have the same
SHA-256 across Diamond, Rails without YJIT, Rails with YJIT, and every repeat.

Expected: two repetitions at 30, 120, and 600 seconds for concurrency 1, 8, 32,
and 128. Report successful and completed throughput; successful-response and
all-response p50/p95/p99; process CPU utilization and CPU/request; and sampled
RSS, PSS, and USS distributions. HTTP 429/503 responses are capacity rejections,
not successful work or functional corruption. Diamond's explicit HTTP 409
serialization abort is reported separately as a transaction conflict and is
never retried by the harness. Every other invalid response or transport failure
is an unexpected error.

The denied operation must call the declared `/read` route with an existing
unauthorized issue and receive 403. Missing-route 404s fail qualification.
Optional persistent-connection profiles require same-socket qualification.
Backoff, worker/thread counts, runtime limits and CPU affinity are explicit
recorded profile choices, rather than silently changed defaults.

## Explicit limits

This first suite measures small JSON CRUD operations and application-level
authorization over loopback HTTP. It does not measure TLS, a reverse proxy,
browser rendering, WebSockets, durable jobs, cross-host database latency, or a
real production traffic distribution. It compares these two implementations of
this workload on this machine; it is not a universal language ranking. The
application series covers its process tree. Sustained campaigns also report
separate PostgreSQL process-tree and Python-client CPU/memory series. Sampling
overhead and short-lived processes between samples limit precision. Linux PSS
is proportional set size, while USS is private memory.
