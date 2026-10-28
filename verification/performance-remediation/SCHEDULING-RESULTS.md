# Configurable scheduling qualification

The implementation supplies workload reservations, peer/verified-account quotas,
one absolute request deadline, and a node-wide transaction retry budget. Every
policy has checked project configuration with finite defaults. The complete
operator reference is [runtime-scheduling.md](../../docs/runtime-scheduling.md).
Expectations are retained in [SCHEDULING-SCENARIOS.md](SCHEDULING-SCENARIOS.md).

## Current build

- Release binary SHA-256:
  `bdecf9ca6545c37272ebb763c13ba3f0ddec17cd772b765edd1f99068ccaa98a`.
- Source fingerprint:
  `c310f04c45010b1aab82664f54f2ef246ccc4e663fbe45984df2321ae5b8271a`.
  This hashes the sorted `sha256sum` manifest of all files under `src/`.
- Format, release/all-feature builds, Clippy with warnings denied, Rust 1.96
  compatibility, and rustdoc with warnings denied passed. There are 20 passing
  unit tests and 39 passing doctests. No unsafe code or public Rust signatures
  were added; Miri does not apply to this change.
- `scheduling-verification.json` records matching final receipt and driver hashes.
  The older README/`verification.json` describe the preceding admission repair.

## Real interfaces and independent state checks

| Replay | Result | Observed behavior |
|---|---|---|
| `scheduling.py` | 12 scenarios; repeated independently | Verified same-group accounts share their quota; another group progresses; tenant headers grant no authority. Actual row locks exercise executor wait, cancellation, no late mutation, shared retry depletion/refill, and disabled retries. Generated transport honors nondefault/disabled retries and a two-second server minimum. Fifteen invalid/overflow configurations are rejected. |
| `replay.py` | 20 scenarios | Repeated mixed work at concurrency 1/8/32/128, credential revocation between transaction attempts, bounded queue saturation/disconnect recovery, explicit replay proof, emitted-client cancellation/identity replacement, logging, keep-alive, and generic failures. Independent SQL readback checks persisted writes. |
| `lp5_actions.py` | 74 observations | Durable action/reconciliation behavior, including actual PostgreSQL COMMIT acknowledgment loss and cancellation races. |
| `health_starvation.py` | 2 observations | Deployment probes remain responsive while slow bodies occupy ordinary quotas and queue; occupancy returns to zero. |
| `websocket_malformed.py` | 3 observations | Native handshake/frame rejection and subsequent valid event delivery through the bounded background polling executor. |
| `files.py` | 65 observations | Cross-node upload/download, exact bytes, permissions, integrity failures, and stream recovery. |
| `git_delivery_redteam.py` | 157 checks | Git/delivery/OCI/LFS/Pages correctness and authority checks, two held authenticated downloads while same-account interactive RPC progresses, disconnection recovery, and 16 mixed streams totaling 256 MiB. |
| `assets.py` | 128 requests at concurrency 32 | All three documented TOML examples pass the checker. The built asset-only server returns identical successful HTML and shuts down cleanly. |
| Short benchmark matrix | 24 cells | Diamond and Rails with/without YJIT, one-second measurements, 0.25-second warmups, two repetitions, identical-state resets, concurrency 1/8/32/128, keep-alive and overload backoff. Independent state and cleanup checks pass. |

The native replays total 333 distinct observations, plus the asset requests and
benchmark cells. The scheduling replay's second run repeats its 12 cases rather
than adding new coverage. Passing receipts report no cleanup errors.

The short matrix completed 12,315 checked requests with zero capacity rejections,
transaction conflicts, unexpected responses, or transport errors. Expected
authorization denials remain part of its correctness workload.

The short benchmark is acceptance evidence; it does not establish a performance
ranking or replace the earlier 30/120/600-second campaign. It ran on a shared
host alongside qualification work. Its ignored local report is
`benchmarks/rails-comparison/results/scheduling-smoke.json`; the final receipt
retains its hash and classified response counts.

## Findings repaired during qualification

- A preflight identity lookup adds native database work. It now charges the
  matching durable rate bucket before lookup, with a request-local hashed
  receipt preventing duplicate charging in dispatch. Missing API routes skip
  this lookup: a real exclusive credential-table lock does not delay their 404.
- A canceled request could otherwise leave an executing PostgreSQL statement
  running. The owned worker sends CancelQuery, closes/joins its driver, and
  retains execution capacity through bounded cancellation cleanup. The live
  sleeping-trigger case confirms no inserted row and successful subsequent work.
- Async timeout alone can admit immediately-ready buffered output after expiry.
  The response wrapper checks the absolute time before polling output; a
  regression verifies the expired body cannot release its buffered success data.
- That stricter check exposed an error-reporting race: a native deadline failure
  could be wrapped in its already-expired success-stream budget. A real executor
  saturation replay received a disconnected socket instead of its final 503.
  `scheduling-before-timeout-report.json` preserves the failure. A native-only
  response marker now keeps the small final error report deliverable outside
  useful work, without replay authority or renewed application execution. The
  deterministic regression and both final live scheduling runs pass.

## Implementation coverage and limits

The shared boundary lives in `src/budget.rs`, `src/project/admission.rs`, and
`src/project/retry.rs`. The connected dispatcher verifies scheduling identity and
then rechecks authority during real execution. Native request-local accounting
is connected in `accounts/rates.rs`; credential lookup does not grant API scopes.
Pool acquisition, connection setup/statements, evaluation, outbound HTTP/OAuth,
Git/LFS/file/delivery streams, Pages, and assets propagate or cap their waits by
the original deadline. Browser generation emits only client timing/retry fields.
WebSocket database polls get bounded background budgets; heartbeat/maintenance
work retains physical blocking-thread headroom.

Live evidence specifically covers SQL cancellation, executor expiry, queue and
identity waits, body/stream/disconnection behavior, protocol replay boundaries,
and generated transport. Outbound adapter/OAuth deadline propagation was source
reviewed; this campaign does not claim live provider login, every network fault,
or exhaustive combinations of every configuration field.

The clock begins at managed handler entry, after HTTP headers. Cleanup can
outlive useful work within its separate cancellation bound. A deadline cannot
undo an external effect or establish whether an uncertain COMMIT succeeded.
Deadline/lost-response paths never acquire the explicit safe replay header.
Fairness uses concurrency ceilings and reserved/shared slots, not preemption;
tenant grouping is an operator map of verified accounts and grants no access.
All concurrency and retry budgets are per node. Other machines require their
own capacity planning; existing shared durable account rate limits still apply.

## Reproduce

From the repository root, with local PostgreSQL tools, Node, and the prepared
Rails benchmark dependencies available:

```sh
cargo build --release --locked
python3 verification/performance-remediation/scheduling.py
python3 verification/performance-remediation/scheduling.py --report verification/performance-remediation/scheduling-repeat.json
python3 verification/performance-remediation/replay.py --report verification/performance-remediation/runtime-scheduling.json
python3 verification/performance-remediation/assets.py
python3 verification/lp5_actions.py --binary target/release/diamond --report verification/performance-remediation/actions-scheduling.json
python3 verification/w9/health_starvation.py --binary target/release/diamond --report verification/performance-remediation/health-scheduling.json
python3 verification/w9_independent/websocket_malformed.py --binary target/release/diamond --report verification/performance-remediation/websocket-scheduling.json
python3 verification/w6_independent/files.py --binary target/release/diamond --report verification/performance-remediation/files-scheduling.json
python3 verification/git_delivery_redteam.py --binary target/release/diamond --report verification/performance-remediation/delivery-scheduling.json
python3 benchmarks/rails-comparison/campaign.py --durations 1 --warmup .25 --dataset 100 --repeats 2 --concurrency 1 8 32 128 --keep-alive --backoff --report benchmarks/rails-comparison/results/scheduling-smoke.json
```

Each fixture creates and tears down its isolated PostgreSQL/filesystem state.
The generated-helper replay uses actual Node fetch and the emitted JavaScript;
it does not claim a new Chrome/DOM journey.
