# Secure language L8 qualification

Date: 2026-10-25

L8 passes for the bounded local profile. Every machine-readable receipt listed by
`secure_language_l8_results.json` names the same executable:

`91c6ad57530343be10bec2e8c01ff20fa36395e9a305c39cd93dcc34694f0e72`

This is evidence for the tested artifact and interfaces, not proof that Diamond or
an arbitrary deployment is defect-free.

## Retained coverage

| Boundary attacked | Real interface and result | Receipt |
| --- | --- | --- |
| Nested secret escape, forged `Html`, active Markdown, input/output expansion | 18 compiler/evaluator cases pass | `secure_language_l8_components/l1_static.json` |
| Forged, rendered, compared, persisted, wire-crossing, and cross-binding blobs | 10 compiler cases pass; final-binary HTTP file corpus is included below | `secure_language_l8_components/l2_static.json` |
| Forged, rendered, persisted, wire-crossing, and cross-channel streams | 13 compiler/generated-runtime cases pass | `secure_language_l8_components/l3_static.json` |
| Header/body injection, dedup collision, suppression, cancellation, current-recipient authority, replaced adapter root | 11 compiler cases and 9 PostgreSQL/worker/filesystem observations pass | `secure_language_l8_components/l4_static.json`, `l4_runtime.json` |
| Unbounded or effectful workflows, changed idempotency, concurrent parent starts, cancellation, disabled owners | 15 compiler cases and 6 PostgreSQL/worker observations pass | `secure_language_l8_components/l5_static.json`, `l5_runtime.json` |
| Missing/mismatched/duplicate secret declarations, source-visible leakage, missing runtime authority, rotation | 10 compiler cases and 4 HTTP/startup observations pass | `secure_language_l8_components/l6_static.json`, `l6_runtime.json` |
| Query helper/effect/cost/depth/row/byte evasion, malformed cursor/limit, owner isolation | 15 compiler cases and 5 authenticated RPC/PostgreSQL observations pass | `secure_language_l8_components/l7_static.json`, `l7_runtime.json` |
| API-vs-RPC plan bypass, expired workflow/mail leases, preexisting mail collision, poisoned workflow state, artifact tampering, log leakage | 8 assembled-runtime observations pass | `secure_language_l8_runtime_results.json` |
| Compression, malformed replies, slow upstreams, cancellation, redirects, DNS, path escape, file interruption, native authority, resource ceilings | 143 observations, 180 real HTTP requests, and 22 clean owned shutdowns pass | `secure_language_l8_w6_results.json` and `secure_language_l8_w6/` |
| Jobs/events, replacement, version fencing, encrypted isolated restore, post-restore authority revocation, backpressure, and checker rejection | 69 final-binary observations pass; this replay found and fixed non-Git restore denial | `secure_language_l8_w7w8/` |
| Hostile cross-node stream frame and cursor replay across a killed/restarted node | Chrome rendered hostile markup literally, created zero script nodes, preserved the parent, and replayed after a 0.282-second restart | `live-workspace_browser_results.json` |
| Public examples accidentally retaining manual secret handles | Finding fixed; all public examples now declare credentials and use automatic injection; the catalog/build/run/link corpus passes | `secure_language_l8_components/examples.json` |
| Git/OAuth compiler authority regression | 14/14 final-binary cases pass, including server-only Git authority and effect separation | `git_compiler_boundaries_results.json` |

The aggregate is 92 secure-language compiler cases, 32 new real runtime
observations, 143 transport/file observations, and 69 durability/recovery
observations. The Git-specific count is recorded separately because it overlaps
none of those totals.

## Quality gates

- `cargo fmt --check` passes.
- `cargo clippy --all-targets --all-features -- -D warnings` passes.
- `cargo test --all-targets --all-features` passes 9/9 tests.
- `TMPDIR=.diamond cargo test --doc -- --test-threads=1` passes 38/38 doctests.
- `verification/examples.py` checks eight standalone examples, executes five pure
  examples, performs two SQLite storage runs, builds and reloads both project
  examples, exercises three HTTP requests, covers 87 public callable forms, and
  validates 127 local documentation links.
- The authority and Git-browser projects both pass the final checker.
- `git diff --check` passes.

## Exact generated artifact

The assembled L8 project recorded these output hashes:

| File | SHA-256 |
| --- | --- |
| `contract.json` | `a72bf59144de7b94a7c2472771f08efe191f626b0bf55a00c7ae0cc89341d4ef` |
| `server.json` | `488d3c6f4c0ba32479f0953d20b090a0e9985619818b64e7ce4405ad5bb96ff0` |
| `index.html` | `ee6357737b18444bf882c89927149ce44219a6df2e18ea99cd1164f37a5ce548` |
| browser JavaScript | `4a3736593df5e408ff8f40b2e009ae8e2492e556d83d31c6c5e5edd82fab0922` |
| browser CSS | `5e04328fd18532a49f8d03a757e515db51af816715d74dc2dd55d4b375b16fbe` |
| source map | `0f48209fe6b52a96156fc6969e4f0b6ce4cd98fdd32cde52b800a90c70a825cf` |

Changing `contract.json` by one byte was rejected before the artifact could load.

## Findings and repairs

1. Mail cancellation could report `cancelled` while a claimed worker was still
   emitting the message. Delivery now holds the outbox row lock through the
   external adapter write, serializing cancellation with the effect.
2. Query budgets initially applied only by RPC operation name. An HTTP API binding
   to the same resolver could have skipped the plan. API dispatch now finds the
   unique resolver plan by checked function identity and applies the same argument,
   database-query, and result budgets.
3. Older public examples still obtained explicit secret handles. They now use
   declarative secret bindings and adapter injection; the callable-catalog gate no
   longer requires developers to learn the compatibility-only handle API.
4. Isolated restore unconditionally called Git repair, which denied a valid
   application with no Git manifest and no Git runtime grant after importing its
   database and files. Git repair now returns success only for that exact no-Git
   pairing and still denies undeclared runtime Git authority. The complete 22-case
   restore drill passes after the fix.
5. Two retained W7/W8 oracles had drifted: health metrics omitted the later
   aggregate Git-transport counter, and the socket-backpressure burst collided with
   the independently qualified shared request limiter. The metric allowlist now
   includes only that aggregate counter; the disposable backpressure fixture clears
   only its owned rate rows between bounded batches. Product rate limits remain
   enabled and tested elsewhere.

## Trust boundary

Diamond still trusts the Rust compiler and selected crates, PostgreSQL transaction
and durability semantics, the operating system's process/filesystem primitives,
Git and OpenSSH where configured, browser CSP/DOM behavior, and deployment
administrators. Operators still own TLS termination, DNS, PostgreSQL availability,
root ownership and capacity, secret provisioning/rotation, off-host retention,
monitoring, and incident response. Runtime declarations narrow authority; they do
not make a malicious host administrator harmless.

## Explicitly untested or not implemented

- The mail adapter is the private directory adapter. Internet SMTP delivery and a
  provider-specific retry/idempotency contract do not exist yet.
- Google and GitHub OAuth claim verification has local unit/adversarial coverage,
  but this L8 run did not use live provider accounts or provider outages.
- The full six-campaign Git G6 suite was not rerun after L8. The final binary passed
  the Git/OAuth compiler boundary corpus and the Git application checks; the older
  774/774 local Git-service campaign remains separate, version-pinned evidence.
- Final-hash Chrome covered the stream/reconnect/XSS path. The file picker and
  browser download UX were not repeated; the final binary instead replayed the
  real HTTP file interruption/download corpus. Earlier browser receipts are not
  counted as final-hash evidence here.
- No public edge/CDN deployment, Internet DNS, multi-host network partition,
  PostgreSQL failover cluster, public SMTP service, or off-host backup provider was
  exercised.
- Limits are enforced at checked/runtime boundaries, but this is not a formal
  allocator proof, exhaustive state-space proof, or exhaustive fuzzing claim.

## Replay

Build first with `cargo build --all-targets --all-features`, run the L1-L7 static
and L4-L8 runtime scripts, run `w6_independent/run.py` with the final binary/library,
perform the owned Chrome replay, then run:

```sh
python3 verification/secure_language_l8.py
```

The collector fails if any retained receipt is not `PASS` or names a different
binary hash.
