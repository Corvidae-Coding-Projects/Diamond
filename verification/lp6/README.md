# LP6 cross-boundary hardening evidence

**Complete, 2026-10-27.** LP6 qualifies AC-2, AC-8, and AC-9 for the declared
bounds in [the LP6 design](../../.design/lp6-hardening.md) and the expectations
frozen in [SCENARIOS.md](SCENARIOS.md). This is a bounded security and runtime
qualification, not a claim that every possible program, allocator behavior, or
browser implementation is safe.

## Final candidate

- Source base: commit `4c0195faaa44fe42c1619b38d6532ae7dc33c101` plus the
  uncommitted LP1--LP6 candidate.
- Compiler/runtime source-tree hash: `8dbe707847a75bf2b222e7a6da82c48e2f89cee415ad7ea490c0636b0f86045a`
  (`sha256sum` of every sorted file under `src/`, then hash that manifest).
- Binary SHA-256: `278b10f6d64d966279e7ddaea9964d43904426c7478c20aafdd0f9228ce722e1`.
- Library SHA-256: `1acc7cf1901610d1a11f83d8b810e0a5170b3effac8c0dd3d49d08a77694e189`.
- Contract versions: language 0.20.0, artifact format 5, wire 7.
- Frozen deterministic seeds include `20261027`, `0xD1A20002`,
  `0xD1A30AC4`, and `0xD1A09317`.

The selected final reports contain 708 recorded observations. Counts overlap
where a later real-interface campaign deliberately replays an earlier compiler
or action invariant; they are evidence counts, not a unique-test metric.

## Exit-gate evidence

| Criterion | Final evidence | Bounded conclusion |
| --- | --- | --- |
| AC-2: confinement | `lp2-compiler.json` through `lp5-compiler.json` (136 cases), `secure-l1-final.json` through `secure-l7-final.json` (92 cases), `lp2-spine.json`, `lp3-inputs.json`, and `lp5-actions.json` | Direct and recursively wrapped restricted values, missing grants/effects, nominal swaps, malformed wire input, raw aliases, corrupt action state, and current policy checks rejected at compiler or authoritative consumers; valid siblings still worked. |
| AC-8: current authority | `authority-races-final.json`, `files-final.json`, `running-cancel-final.json`, `websocket-final.json`, `lp5-actions.json`, `browser-auth-final.json`, and the delayed-unmount journey backed by `browser_backend.json` | Cross-node logout/account disablement/token revocation, action receipt reads, queued/running jobs, blobs, sockets, stale callbacks, authenticated reload, and logout cleanup rechecked the current identity/epoch/policy instead of trusting an opaque handle. |
| AC-9: bounds and recovery | `limits.json`, `admission-final.json`, `websocket-final.json`, `files-final.json`, `prolonged-final.json`, `semantics-native.json`, `browser.json`, and the LP2--LP5 replays | Above-limit work failed explicitly; component/text/remote materialization was checked before or at the shared boundary; admission, sockets, uploads, processes, descriptors, tasks, and database sessions recovered; later valid work passed. |

The final prolonged campaign performed 1,000 real HTTP/authorized operations,
gracefully stopped and restarted one node, then completed valid reads on both
nodes. It sampled only Diamond web PIDs for RSS/file-descriptor/task envelopes;
PostgreSQL connections were sampled separately. Both surviving nodes settled at
40 descriptors and 7 tasks, and PostgreSQL settled at 12 connections.

## Confirmed findings and retained repair evidence

1. Native SSR allowed more than the browser's 1,024 live-component limit while
   remaining under the node cap. `limits_before.json` records the emitted
   1,251-component artifact. `render.rs` now rejects before artifact emission;
   `limits.json` retains the valid-small and rejected-over-cap cases.
2. Browser `join_text` and `text_replace` allocated the expanded string before
   enforcing the one-MiB text limit. `limits_before.json` records the missing
   preflight helpers. The generated runtime now projects UTF-8 output size before
   joining/replacing; native independent expansion cases and the final browser
   runtime replay pass.
3. A direct remote root was type-validated but did not contribute to the event's
   cumulative 64-MiB managed-value budget. `materialization-before.json` records
   the exact pre-fix source boundary and its limitation. Root unwire now passes
   the fully materialized value through `__check`; `limits.json` retains the
   emitted-runtime regression marker. The before case is source-backed: LP6 did
   not deliberately force Chrome to allocate the full 64 MiB.

All three repairs live in shared compiler/runtime boundaries. No fixture-specific
exception, diagnostic suppression, or application-owned security check was used.

## Real browser observations

`browser.json` is a current-binary Chrome replay. Server/native and browser code
independently produced
`9007199254740993|3|-2|-0.000002|😀|·a·b·`; arithmetic and duplicate-key failures
preserved prior state, and a valid follow-up recovered. `browser-auth-final.json`
records a login, authenticated reload, and logout on the disposable two-node
fixture. Its mount handler intentionally reloads only `session.user`, so zero
cards after reload is fixture behavior and is not represented as a runtime data
reload guarantee.

The delayed-unmount journey used a real held action response: the database
committed exactly once, the removed component received no stale completion, the
replacement mounted from committed state, and logout removed private components
and recovery notices. `browser_backend.json` retains the database/action receipt;
that journey preceded the final one-line remote-materialization repair, while
the affected compiler/action/backend paths were replayed on the final binary.

## Qualification commands

The exact final candidate passed:

```text
cargo build --all-features
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-features
RUSTDOCFLAGS='-D warnings' cargo doc --all-features --no-deps
cargo check --all-features                 # rustc 1.96.0
python3 verification/lp6_limits.py --report verification/lp6/limits.json
python3 verification/w3_independent/semantics.py --binary target/debug/diamond --report verification/lp6/semantics-native.json
python3 verification/lp{2,3,4,5}_compiler.py ...
python3 verification/secure_language_l{1,2,3,4,5,6,7}.py ...
python3 verification/lp2_spine.py ...
python3 verification/lp3_inputs.py ...
python3 verification/lp4_components.py ...
python3 verification/lp5_actions.py ...
python3 verification/w6_independent/files.py ...
python3 verification/w9_independent/auth_policy_races.py ...
python3 verification/w9/health_starvation.py ...
python3 verification/w9_independent/running_cancel.py ...
python3 verification/w9_independent/websocket_malformed.py ...
python3 verification/lp6_prolonged.py ...
```

Rust unit tests: 10 passed. Rust doctests: 38 passed. `unsafe_code = "forbid"`,
so an unsafe/Miri audit is not applicable to this candidate.

## Calibration and remaining limits

- `capacity.json` is intentionally retained as an **invalid truncated
  calibration**, not a qualification result. A 25-second stop was applied to a
  30-minute W9 schedule whose synchronized restarts and bursts assume the full
  duration; it therefore reports `FAIL` despite healthy final readiness. LP6's
  conforming prolonged evidence is `prolonged-final.json`.
- The actual browser coverage is one Chrome environment. Firefox, Safari,
  assistive-technology combinations, and browser-engine heap isolation were not
  qualified.
- Exact 8,192-byte identity and 8-MiB rendered-page edge values were not each
  synthesized through a valid source program. The current compiler replay does
  exercise expanded render-node rejection and small valid follow-up; source and
  artifact size boundaries retain earlier coverage. This gap does not broaden
  the stated AC-9 conclusion beyond the exercised bounds.
- LP6 sampled process-level RSS, descriptors, tasks, sockets, and PostgreSQL
  sessions. It does not prove exact allocator reclamation, absence of all leaks,
  or behavior under an unbounded production duration.
- External OAuth providers, public Internet dependencies, deployment pipelines,
  and infrastructure are outside this language-runtime checkpoint.

No LP6 fixture process or isolated PostgreSQL cluster remains running. LP7 is the
next planned checkpoint.
