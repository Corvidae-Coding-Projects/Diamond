# Runtime performance remediation

The deadline/fairness/shared-budget follow-up is documented in
[SCHEDULING-RESULTS.md](SCHEDULING-RESULTS.md), with the current configuration in
[runtime-scheduling.md](../../docs/runtime-scheduling.md). The results and binary
fingerprints below describe the earlier admission repair and are retained as
historical evidence.

The acceptance contract is in [SCENARIOS.md](SCENARIOS.md). The original
72-cell benchmark reproduced millions of immediate capacity rejections. The
findings ledger in `docs/diamond-rails-benchmark-findings.md` preserves that
evidence and its later-discovered missing-route error.

## Changes and boundaries

- Shared admission protects connected applications, assets, and Pages using
  checked deployment limits, a bounded FIFO queue and a queue deadline.
- Pre-execution overload responses carry explicit replay proof and backoff
  guidance. Generated transport retries are bounded, canceled with their event,
  and invalidated on identity replacement.
- Confirmed serialization/deadlock aborts may retry ordinary database-only
  operations. Host effects, actions, unknown outcomes, and generic aborts remain
  outside this path. Fresh attempts recheck all authority.
- Ordinary overload logs are aggregated; health metrics expose resource use and
  retry counters. Pages keeps permits with blocking work after disconnection.
- The sustained benchmark now offers persistent connections, backoff, worker and
  runtime profiles, CPU affinity, status counts, and separate app/PostgreSQL/client
  CPU and proportional-memory measurements.

The Rust repair and real-testing skills shaped these changes: the fixes live in
shared runtime boundaries, and the retained replay drives real HTTP, SQL locks,
credential revocation, and the helper extracted from the emitted browser asset.
No fake application server or mocked database supplies the acceptance results.

## Replays

From the repository root:

```sh
cargo build --release --locked
python3 verification/performance-remediation/replay.py
python3 verification/lp5_actions.py --binary target/release/diamond \
  --report verification/performance-remediation/actions-final.json
python3 verification/w9/health_starvation.py --binary target/release/diamond \
  --report verification/performance-remediation/health-final.json
python3 verification/git_delivery_redteam.py --binary target/release/diamond \
  --report verification/performance-remediation/delivery-final.json
```

The targeted replay uses 100 initial issues and two two-second repetitions at
concurrency 1, 8, 32 and 128. It then forces a row conflict, repeats that race
with credential revocation, forces admission saturation, cancels a queued
connection, checks generated-client retry/identity/abort behavior, and reads
persisted state independently. It also exercises invalid runtime configurations,
generic database failure, same-socket HTTP reuse, and recovery. Synthetic
credentials stay in the isolated fixture and are not included in receipts.

`client.cjs` extracts the actual emitted helper, sends requests to the real
Diamond server with Node's native fetch, and observes call counts and timing.
It does not establish DOM rendering or a Chrome UI journey.

The delivery fixture needed two updates to replay current code: its runtime
omitted the now-declared webhook credential, and its anonymous-page expectation
predated the sign-in UX. The synthetic credential and current shell expectation
are now explicit; an optional binary/report path avoids overwriting earlier
qualification artifacts. The full delivery replay passed 152 checks, including
16 simultaneous artifact/OCI/LFS/Pages streams totaling 256 MiB.

The asset-only `examples/components` server also served 128 actual HTTP requests
at concurrency 32 and shut down cleanly. The alternate-profile smoke campaign
passed all six cells using two Puma workers with two threads each, two Diamond
database/blocking workers, and inherited CPU affinity 0..3. Its results are in
`benchmarks/rails-comparison/results/profile-smoke.json` (ignored local evidence).

## Build evidence

The final application source passed:

```sh
cargo fmt --all --check
cargo build --release --locked
cargo build --all-features
cargo clippy --all-targets --all-features -- -D warnings
cargo +1.96.0 check --all-features --locked
cargo test --all-features
RUSTDOCFLAGS='-D warnings' cargo doc --all-features --no-deps
```

There were 15 passing unit checks and 39 passing doctests. No unsafe code or
public Rust signatures were added or changed; Miri was not applicable to this
patch. The MSRV remains Rust 1.96. The HTTP error/metadata contract and private
manifest configuration gain additive fields.

Final release binary SHA-256:
`e7d6462807b35e029d48d9522db1f60975a2b30363a0195c281238f62066cd15`.

Source fingerprint, computed by hashing the sorted `sha256sum` manifest of all
files under `src/`:
`41df746dc9f3a220bb0bf88588270abe6d5b0138330283f153c435fb3f3480e3`.

The corrected 30-second benchmark is an acceptance follow-up with two repeats,
two-second warmups and all four concurrency levels, using both Rails YJIT modes.
It does not replace the requested 120/600-second historical observations or
establish production tuning, cold-cache behavior, or dedicated-host performance.
Fresh-process resets, warm database caches and host scheduling limits remain
explicit. Bounded retries can still exhaust under contention; that returns 409
and preserves the transaction isolation contract.

Final result: 24/24 corrected benchmark cells passed, with zero capacity
rejections, transaction conflicts, unexpected responses, or transport errors.
The independent database oracles and process cleanup passed in every cell.
`verification.json` records receipt hashes and the matching final binary and
benchmark program hashes. The benchmark README/scenario documentation was
updated after measurement; that difference is recorded explicitly.
