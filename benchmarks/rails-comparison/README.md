# Diamond and Rails application benchmark

This package answers a narrow, useful question: on the same machine and
PostgreSQL cluster, how do a small authenticated Diamond JSON application and
an equivalent Rails application behave under the same external workload?

It is a reproducible comparison rather than a one-line stopwatch. The runner
rejects invalid responses, independently reads persisted state, measures the
owned processes, and records enough provenance to explain or repeat a result.
Read [SCENARIOS.md](SCENARIOS.md) for the predeclared acceptance oracle and the
limits on what the numbers mean.

## Prerequisites

- Diamond's normal Rust toolchain
- PostgreSQL client and server tools (`initdb`, `pg_ctl`, and `psql`)
- Ruby 4.0.7 and Bundler 4.0.20 on the host
- Podman, used once to build the pinned Rails native gems without modifying the
  host Ruby installation
- Python 3.11 or newer (standard library only)

Rails is pinned to 8.1.4, Puma to 7.2.1, and the full dependency graph is in
`rails/Gemfile.lock`.

## Prepare

From the repository root:

```bash
./benchmarks/rails-comparison/prepare.sh
cargo build --release --locked
```

The generated bundle lives under an ignored `vendor/bundle` directory. No
password, bearer token, Rails secret, PostgreSQL data, or result file is added
to Git.

## Run a short qualification

```bash
python3 benchmarks/rails-comparison/run.py --quick
```

The quick profile uses 100 initial issues, concurrency 1 and 8, one repeat,
and a 0.75-second measured window per phase. It is intended to prove the
benchmark works, not to support a performance conclusion.

## Run the comparison

```bash
python3 benchmarks/rails-comparison/run.py
```

The default profile uses 1,000 initial issues, concurrency 1, 8, and 16, three
repeats, and a five-second measured window. Use `--help` to change one variable
at a time. For example, compare Rails with YJIT in a separate run:

```bash
python3 benchmarks/rails-comparison/run.py --rails-yjit \
  --report benchmarks/rails-comparison/results/yjit.json
```

The runner alternates which application goes first, warms each operation before
measurement, opens a new HTTP/1.1 connection per request for both applications,
and runs one application process per stack. Rails has four Puma threads and
four database connections. The result contains raw phase observations and a
small Markdown table; compare repeat distributions rather than quoting only the
best number.

Both fixtures perform a durable PostgreSQL-backed admission charge for global,
peer, and credential buckets before authentication. Their explicit benchmark
ceiling is 10,000,000 requests per minute so admission work remains measured but
the ceiling does not truncate a local throughput phase. Diamond applications
that omit `[identity.api_requests_per_minute]` retain the secure
600/300/120 defaults.

## Run the sustained follow-up

```bash
python3 benchmarks/rails-comparison/campaign.py \
  --durations 30 120 600 \
  --concurrency 1 8 32 128 \
  --repeats 2 \
  --warmup 10 \
  --report benchmarks/rails-comparison/results/sustained.json
```

This 72-cell matrix takes approximately five hours of measured time plus twelve
minutes of warmup. It runs Diamond, Rails without YJIT, and Rails with YJIT one
at a time so their CPU and Linux proportional-set-size measurements do not
contaminate one another. Each cell receives a fresh application process and an
identical post-warmup database reset. The JSON checkpoint is atomically updated
after every cell; after an interruption, repeat the command with `--resume`.

The sustained runner uses a bounded latency histogram instead of retaining one
Python object per request. It reports aggregate and per-operation p50/p95/p99,
successful and completed throughput, process CPU, and RSS/PSS/USS distributions.
Capacity rejections remain visible rather than being misreported as useful
throughput. Explicit 409 serialization aborts are likewise reported as
transaction conflicts and are not silently retried.

The current runtime can internally retry confirmed database conflicts for
eligible ordinary operations. These are bounded transaction attempts, not
transport replays of an unknown mutation. Capacity responses remain separate.

### Connection, pacing, and deployment profiles

Add `--keep-alive` to retain a connection per client worker and qualify actual
same-socket traffic on both targets. Add `--backoff` to honor capacity responses'
`Retry-After` before offering the next request. These options are recorded in the
report; the default fresh-connection, unpaced baseline remains available.

The denied operation reads an existing unauthorized issue through `/read` and
requires 403. The original `sustained.json` predates this correction: its denied
phase called a missing route and accepted 404. Preserve that historical evidence,
but use corrected runs for authorization-workload comparisons. See the
[findings ledger](../../docs/diamond-rails-benchmark-findings.md).

Every sustained cell now includes `measurement.service_cost.postgresql` and
`.client`, with separate CPU and memory series. The application series includes
its process tree, including any Puma workers. PSS is summed across owned process
trees; RSS double-counts shared pages and must not be interpreted as physical
host usage. Very short-lived processes between samples may be missed.

Use `--rails-workers 2 --rails-threads 2` for an explicitly labeled Puma profile.
`--diamond-runtime path.toml` reads the contents of a `[runtime]` table (without
the table header) into an isolated copy of the Diamond fixture. Diamond checks
the limits normally. Defaults stay one Puma process/four threads and one Diamond
process/four blocking workers/four database connections. Profiles are not
automatic claims of optimal tuning or equivalent maximum core use.

`--cpu-affinity 0 1 2 3` restricts the harness and its child services to CPUs
allowed by the current host. Effective affinity and frequency-governor names
are recorded. A quiet dedicated host and controlled thermals remain necessary
for stronger performance conclusions.

## Reading the result

Useful questions are:

- Does either application return incorrect or unauthorized data? If yes, stop;
  the timings are invalid.
- At the concurrency you actually need, what are p95/p99 latency and throughput?
- How much PSS/USS and CPU per request does each process require?
- Do the conclusions repeat on a quiet, fixed-frequency host?

Do not turn a loopback CRUD result into a claim about full Rails applications,
Diamond applications with different adapters, browser speed, or production
tail latency. Add representative endpoints and deployment layers before using
this harness for those questions.
