# Independent W6 qualification

This separate verifier derived expected behavior from the approved W6 contract and
AC-12/13/16 before executing the implementation. It owns only this directory and
reuses historical W5 lifecycle/seed helpers by private import without modifying
those files. External fixtures implement actual HTTP/TLS/DNS peers, never Diamond
handlers, identities, policies or fabricated application answers.

## Reproduction and artifacts

```
python3 verification/w6_independent/run.py \
  --binary target/debug/diamond --library target/debug/libdiamond.rlib \
  --dependencies target/debug/deps
```

The runner pins both artifacts before execution. `results.json` records exact
executable/library hashes, each step, driver/dependency source hashes, HTTP request
counts and shutdown gates. Every campaign uses owned ephemeral PostgreSQL, real
Diamond processes and private temporary directories. Accounts, passwords, native
credentials, CA keys, destinations and files are synthetic and local. Receipts
redact upload credentials; PostgreSQL token-hash comparisons record only booleans.

Final frozen executable: `0aa105a7111872313e326cbc37e95ef01a4c47dcec54bcdd4b63abde15ae619c`.
Final library: `5cac9924ad3f4c053a083a889648a89be5e1ec4dca88a5976e2617410ab40266`.
The final replay passed 143 observations, 180 shared-requester HTTP requests plus 11 raw-socket requests, and 22 clean application exits in 66.27 seconds. Exact results are in `results.json`.

## Exercised behavior

| Campaign | Independent observations and interfaces |
| --- | --- |
| `files_results.json` | Actual zero-byte, binary and exact8MiB transfers across two nodes; committed owner/parent/length metadata precedes bytes; ticket hash-only storage and DB expiry; pending/staged denial; principal binding; concurrent finish and explicit retry; exact Reader downloads and safe headers; outsider denial; same-length corruption rejection; published deletion; four-pending quota; invalid length/oversize; interrupted real streaming with partial-file release and unrelated health; explicit expiry cleanup; traversal/control/oversized names; forged/missing ticket, CSRF and Origin; declared HTML remains attachment; Reader deletion denial; real deferred PostgreSQL abort after filesystem rename followed by explicit recovery; different shared-root init/serve denial; permission revocation after reservation. |
| `limits_results.json` | Actual begin requests against independently seeded512MiB declared metadata and10,000 rows; quota rejects without allocation. A batch of105 expired records with real partial/renamed files removes100, then automatic startup cleanup removes the remainder. Ready objects survive expired-ticket cleanup. A real stalled upload times out near30s, removes its partial file, releases descriptors and permits same-node work. A running server's minute tick removes expired metadata without CLI/restart. Metadata seeding is not a512MiB physical-storage capacity claim. |
| `outbound_results.json` | Two-node typed TLS GET and POST with owned CA/original hostname, actual native bearer delivery and exact wire bytes; authorization before outbound request; readonly snapshot allowance; absolute/network URLs, path segments, traversal/encoding/fragment denial; two GET redirects and third-hop denial; cross-origin/downgrade/off-prefix denial; malformed/wrong-type/oversized/compressed/stalled replies; raw and Unicode-escaped credential reflection denial; generic redacted errors; lost POST response observed exactly once with outcome_unknown; POST redirect not followed; trust/hostname/method/public-loopback denial; ambient proxy variables do not redirect traffic; native credential absent from public artifacts/logs; three repeated batches of three real downstream disconnects drain peers and stabilize file descriptors after bounded pool warmup, with fresh same-node work. |
| `checker_results.json` | Twenty generated CLI cases include ordinary positive programs, valid transaction syntax and readonly/transitive-call controls, then actual rejection of explicit transactions with host effects, transitive writes before/after HTTP, missing static budget, dynamic destination, wrong nominal Secret destination, comparison/text conversion, nested request/reply/RPC authority, database persistence, browser host access, public API host effects and invalid file binding/size. Diagnostics are retained so early parse failure cannot masquerade as authority coverage. |
| `native_results.json` | Separately compiled external Rust consumer uses RuntimeGrants::from_json, maintain_files and serve_with_runtime, checks private Debug representation, performs real HTTP/upload/download and cleanup, and rejects missing, duplicate or unknown runtime grants. |
| `dns_results.json` | Retained system-resolver regression replay: the former synthetic hostname no longer invokes blocking getaddrinfo; error returns promptly and fresh RPC succeeds. This is separate from actual UDP cancellation proof. |
| `reserved_ipv6_results.json` | The two source-review regressions use public-profile literal origins https://[3ffe::1] and https://[3fff::1]. Both now return exact403 denied within milliseconds. Original503/deadline results are retained separately; no packet capture or successful IPv6 connection is claimed. |
| `dns_async_results.json` | Real owned UDP A/AAAA answers reach an actual TLS service under a synthetic.test hostname. CNAME preserves the original TLS identity. Mixed loopback/nonloopback and private-only answers cause zero HTTP requests. Dropped DNS queries return within the configured1s operation deadline; fresh lookup recovers. Custom DNS is rejected for public network grants. Actual query counts are retained. |

The shared requester records ordinary HTTP requests. In addition, the runner sends
11 real raw-socket requests: one interrupted upload, one30s stalled upload and nine
upstream-confirmed downstream disconnects. These are recorded in their observations
rather than included in the shared requester's counter. External HTTP and DNS peer
counts are separate observations, not additions to the application HTTP count.

Normal application exits must be zero, without forced kill/panic, and fixture
threads must drain. Cleanup failures fail qualification. Successful ephemeral
clusters/directories are removed. The original failed resolver fixture was stopped
and retained locally with its failure receipt; it is not a running deployment.

## Findings and limits

Two actual product defects were reproduced: blocking getaddrinfo/runtime cleanup
extended a1s HTTP deadline to8.0063s. `dns_initial_failure.json` preserves the
original cc420 executable observation. The implementation owner replaced blocking
resolution with owned asynchronous DNS; the final UDP-drop and mixed-address
replays independently qualify the repair. The final source-review probes also
reproduced a reserved IPv6 policy-denial gap: two literals returned503/deadline
instead of policy403. The narrow public-address patch now returns exact403 for
both saved cases. `FINDINGS.md` distinguishes driver
corrections and the bounded FD warmup observation from product defects.

Chrome's authored upload workflow is owned and reported by the root agent; these
backend/CLI/native results do not substitute for it. These campaigns do not qualify
remote shared-mount durability, production proxy/TLS/OS-resolver environments,
DNS rebinding during an established connection, every IPv6/mixed-family permutation,
a blocked downstream download's30s ceiling, SIGKILL precisely between rename and
commit, storage fsync/power loss, lost PostgreSQL commit acknowledgment, every
cross-app/binding ticket combination, or production capacity. The controlled
rename/deferred-abort experiment proves its stated recovery path, not every crash
schedule. SMTP, jobs, arbitrary native loading/shell execution and optional Git
integration are outside this W6 implementation and are not claimed as verified.

Supporting deployed old/new application versions was explicitly excluded by the
user. No upgrade compatibility claim is inferred from these observations.
