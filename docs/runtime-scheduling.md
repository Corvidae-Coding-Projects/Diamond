# Runtime capacity, fairness, deadlines, and retries

Diamond supplies finite defaults without project configuration. Change them in
`diamond.toml`, then check and rebuild the project. Unknown fields, out-of-range
values, and inconsistent reservations fail before the server starts. Limits
apply per server process; account quotas and authorization remain database-backed.

## Defaults

```toml
[runtime]
request_limit = 16
queue_limit = 128
queue_timeout_ms = 1000
worker_threads = 2
blocking_threads = 4
maintenance_threads = 2
database_connections = 4
request_timeout_ms = 10000
stream_timeout_ms = 60000
cleanup_timeout_ms = 200
client_event_timeout_ms = 0

[runtime.fairness]
peer_limit = 0
principal_limit = 0
max_peers = 1024
max_principals = 1024

[runtime.retry]
max_retries = 2
window_ms = 2000
backoff_ms = 2
max_backoff_ms = 50
jitter_ms = 8
budget_capacity = 32
budget_refill_per_second = 8
client_retries = 2
client_max_backoff_ms = 4000
client_jitter_ms = 250
retry_after_seconds = 1
```

Zero selects an automatic value only for fields explicitly described that way
below. For example, `queue_limit = 0` disables waiting and
`budget_capacity = 0` disables transaction retries.

| `[runtime]` field | Default | Accepted values and behavior |
|---|---:|---|
| `request_limit` | 16 | 1–1024 admitted HTTP requests. Streaming responses retain their permits. |
| `queue_limit` | 128 | 0–4096 waiting requests across admission and identity queues. |
| `queue_timeout_ms` | 1000 | 1–5000 per queue wait, also capped by the original request deadline. |
| `worker_threads` | 2 | 1–16 async executor threads. |
| `blocking_threads` | 4 | 1–64 concurrent foreground/background blocking callbacks; no greater than `request_limit`. |
| `maintenance_threads` | 2 | 1–8 additional physical blocking threads available for heartbeat/maintenance work. |
| `database_connections` | 4 | 1–64 pooled connections, no greater than `blocking_threads`. |
| `request_timeout_ms` | 10000 | 10–120000; default interactive/control deadline. |
| `stream_timeout_ms` | 60000 | 10–600000; default transfer deadline. |
| `cleanup_timeout_ms` | 200 | 10–5000 for the PostgreSQL cancellation attempt after useful work expires. |
| `client_event_timeout_ms` | 0 | Automatic: largest HTTP workload deadline plus 5000. Explicit values must cover every HTTP workload deadline and be at most 610000. |

These settings cover managed connected apps, asset serving, and isolated Pages.
They do not change standalone CLI/job execution into HTTP requests. Native
protocol limits, such as eight simultaneous Git transports, 32 live sockets,
body sizes, and shorter individual database/network timeouts still apply.

## Workload isolation

Routes select their workload in the runtime. Client headers cannot select a
class or claim a reservation. These are the resolved defaults with the configuration
above:

| Workload | Routes/work | Request ceiling | Execution ceiling | Reserved requests | Reserved execution | Deadline |
|---|---|---:|---:|---:|---:|---:|
| `interactive` | Pages of the app, assets, API/RPC calls, live-socket handshake | 16 | 4 | 2 | 1 | 10000 ms |
| `control` | Sessions, login/logout, accounts, OAuth, health | 16 | 4 | 2 | 1 | 10000 ms |
| `transfer` | Files, Git/LFS, delivery/OCI, isolated Pages | 8 | 2 | 0 | 0 | 60000 ms |
| `background` | Each live-socket database poll | — | 1 | — | 0 | 3000 ms |

Class ceilings still share the global limits. Reserved slots belong to their
class; remaining slots are shared. For example, transfers can use at most two
blocking execution slots by default, leaving reserved capacity for browsing and
account operations. A reservation cannot make a failing database healthy or
preempt a callback already executing.

Customize a workload through `[runtime.workloads.interactive]`, `.control`,
`.transfer`, or `.background`:

```toml
[runtime.workloads.transfer]
request_limit = 6
execution_limit = 2
timeout_ms = 120000
reserved_requests = 0
reserved_execution = 0
```

`request_limit`, `execution_limit`, and `timeout_ms` use zero for automatic
values. Request/execution ceilings cannot exceed their global limits; a resolved
deadline must be 10–600000 ms. Omitting reservations chooses the defaults;
explicit zero removes that class's reservation. Each reservation must fit its
class ceiling, and the sum must leave at least one shared request/execution slot.
Background polls accept execution/deadline settings and execution reservations;
nonzero request limits/reservations are rejected because these polls do not
admit HTTP requests.

Automatic values follow global capacity: transfers get half the request and
blocking ceilings (minimum one); background gets one executor and at most a
3000 ms deadline. Interactive/control each reserve `max(1, request_limit / 8)`
requests when the global request limit is at least four, and one execution slot
when at least three blocking workers exist. Smaller configurations omit those
reservations.

Health requests have a separate admission bound so ordinary request saturation
does not prevent liveness checks. Readiness database work still consumes control
execution capacity and its deadline.

## Peer and verified-account fairness

`[runtime.fairness]` provides concurrent ceilings independently for each workload:

| Field | Default | Accepted values and behavior |
|---|---:|---|
| `peer_limit` | 0 | Automatic: half `request_limit`, minimum one. Explicit 1–`request_limit`. |
| `principal_limit` | 0 | Automatic: half `blocking_threads`, minimum one. Explicit 1–`request_limit`. |
| `max_peers` | 1024 | 1–65536 live peer/workload keys. |
| `max_principals` | 1024 | 1–65536 live identity/group/workload keys. |

The peer is the actual TCP peer. Forwarded headers do not create identities.
Behind a reverse proxy, set the peer ceiling for that proxy's aggregate traffic;
the verified-account ceiling continues to distinguish accounts. Anonymous work
uses a peer-based identity. Keys are removed after their last request/waiter
leaves, so the tables cannot grow with historical traffic.

An optional operator map groups verified accounts into one concurrency budget:

```toml
[runtime.fairness.tenants]
alice = "company_a"
bob = "company_a"
charlie = "company_b"
```

Both keys and group names must be valid Diamond identity identifiers, with at
most 10000 mappings. Unmapped accounts receive individual budgets. Diamond
verifies the credential before assigning this scheduling identity, releases
preliminary admission while waiting for the identity quota, then checks the
credential and resource authorization again during execution. This mapping
grants no permissions. Tenant headers cannot override it. The native preflight
and dispatcher charge the matching durable rate bucket once per HTTP attempt.
Downloads use a separate workload key, allowing the same account to browse
while downloading.

## One deadline through the request

The absolute deadline starts when the managed handler receives a request. Queue
waits, identity lookup, executor/pool acquisition, body input, evaluation,
transaction retries, outbound adapters, and streaming share the remaining time.
A new transaction or pool lease cannot restart the whole-request clock. This
deadline does not time TCP/TLS setup or HTTP header receipt before the handler.
Each live-socket database poll gets a fresh bounded background budget; it does
not impose a ten-second lifetime on the socket.

An expired callback waiting for execution never starts later. Active SQL receives
a cancellation attempt; its connection is closed and its driver joined before
the owned execution permit is released. Cancellation cleanup has its own finite
allowance because abandoning a connection immediately can leave work running.
Already executing native effects cannot all be forcibly undone.

Before response headers, deadline expiry returns 503 `deadline_exceeded`, without
`X-Diamond-Retry: safe`. The small final error report is delivered outside the
expired useful-work budget; it cannot restart application work. After headers,
expiry terminates the response stream, including output already buffered.
Neither establishes whether a mutation committed; reconcile state before
retrying. Generic errors and lost responses never authorize automatic replay.

## Shared retry budget and browser backoff

Only confirmed PostgreSQL serialization/deadlock aborts of ordinary operations
whose checked effects are entirely database reads/writes qualify for retry.
Every attempt starts a fresh transaction and rechecks authority. Actions, host
effects, generic failures, and unknown outcomes remain ineligible regardless of
configuration.

| `[runtime.retry]` field | Default | Accepted values and behavior |
|---|---:|---|
| `max_retries` | 2 | 0–5 extra transaction attempts. |
| `window_ms` | 2000 | 1–30000 retry-start window, capped by the request deadline. |
| `backoff_ms` | 2 | 1–1000 initial exponential delay. |
| `max_backoff_ms` | 50 | `backoff_ms`–5000 maximum transaction delay, including jitter. |
| `jitter_ms` | 8 | 0–1000; uniform integer jitter from zero up to, excluding, this value. Zero disables jitter. |
| `budget_capacity` | 32 | 0–4096 shared tokens; bucket starts full. One token per extra attempt. |
| `budget_refill_per_second` | 8 | 0–4096 tokens/second. Zero leaves a finite initial bucket. |
| `client_retries` | 2 | 0–5 extra browser requests after explicit pre-execution overload rejection. |
| `client_max_backoff_ms` | 4000 | 100–30000 exponential client-backoff ceiling; the server's `Retry-After` remains a minimum. |
| `client_jitter_ms` | 250 | 0–5000 additional browser jitter, exclusive upper bound. |
| `retry_after_seconds` | 1 | 1–30, emitted with explicit overload rejections. |

Cookie RPC and bearer API requests share one transaction token bucket on each
node. Exhausted retries remain explicit 409 conflicts. Browser retries are a
separate mechanism: only the runtime's 503 `overloaded` response with
`X-Diamond-Retry: safe` proves application execution did not begin. Such responses
close the connection if its body remains unread. The browser honors backoff,
event cancellation, identity replacement, the event deadline, and the existing
remote-call budget. Only the required client settings are emitted in JavaScript;
account/group mappings stay server-side.

`/_diamond/health/metrics` reports active/waiting/executing work, resolved class
limits and occupancy, overload/deadline counts, transaction retries, and budget
denials. Metrics expose no tenant map or credentials. Overload logging is
aggregated. The reproducible live qualification is in
[`verification/performance-remediation`](../verification/performance-remediation/).
