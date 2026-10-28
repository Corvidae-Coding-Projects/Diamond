# Scheduling and deadline acceptance

Expectations are fixed before the new implementation is exercised.

| Scenario | Required observation |
|---|---|
| No runtime overrides | Checked finite defaults; ordinary work succeeds, including the existing action/stream paths. |
| Queue and executor wait consume the request budget | Short deadlines expire within a bounded envelope. A mutation whose executor slot never became available does not execute later. |
| Expiry while SQL executes or commits | No blind replay or asserted rollback of an unknown outcome; independent readback establishes state and subsequent requests recover. |
| Pool acquisition, body input, retries and native adapters | They share the initial deadline instead of starting a new whole-request timeout at each stage. Cleanup cannot recycle a connection without confirmed rollback. |
| Workload isolation | Held transfers cannot occupy every request/execution slot. Interactive/control work can proceed when the remaining required resources are healthy. |
| Peer fairness | Requests from one peer/workload have a checked concurrent ceiling and bounded queue use. Keys come from the TCP peer. |
| Principal/group fairness | Only verified principals enter the principal/group quota. Same-group accounts share a ceiling; another group can proceed. Quotas never grant authorization. |
| Shared retry budget | Concurrent confirmed transaction aborts cannot exceed the node-wide token budget; refill is bounded; zero retries/budget disables retries. Unknown outcomes and external effects remain ineligible. |
| Configuration | All new values have documented defaults, checked ranges and cross-field validation. Bad/unknown fields fail before serving. |
| Generated transport | Configured retry/backoff/event limits are emitted without leaking tenant membership; cancellation and identity replacement still prevent replay. |
| Deadline error reporting | A native deadline error racing with expiry remains a readable 503 before headers; expired buffered success output cannot extend the stream. |
| Recovery | Canceled/expired requests release queued/execution/principal permits; completed and rejected writes match independent SQL state. |

Fairness is concurrency isolation plus FIFO waiting, not preemption of executing
work. Authenticated-account grouping is an operator QoS policy, not a replacement
for application resource authorization.
