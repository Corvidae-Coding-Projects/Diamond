# W6 result — private files and typed integrations

**PASS within the documented development profile, October 22, 2026.** Diamond
0.12.0 implements shared private attachments, generated browser upload/download,
current parent-policy enforcement, typed named HTTP, opaque secrets, explicit
native grants, operation deadlines and bounded reconciliation. W7–W10 remain.
This is not a production deployment or exhaustive security claim.

## Final artifacts and reproduction

- Executable SHA-256: `0aa105a7111872313e326cbc37e95ef01a4c47dcec54bcdd4b63abde15ae619c`
- Library SHA-256: `5cac9924ad3f4c053a083a889648a89be5e1ec4dca88a5976e2617410ab40266`
- MSRV retained at Rust 1.96. See [final source fingerprints and gates](w6_final_state.json).
- [Behavioral contract](web_six.md), [guide](../docs/files-and-integrations.md),
  [source/caller coverage](w6_source_review.md), and [dependency declarations](w6_dependency_review.json).

```sh
cargo build --locked
python3 verification/web_six_checker.py
python3 verification/w6_independent/run.py --binary target/debug/diamond --library target/debug/libdiamond.rlib --dependencies target/debug/deps
python3 verification/web_three_independent.py --report /tmp/w6-w3-replay.json
python3 verification/web_five_independent.py --report /tmp/w6-w5-replay.json
```

The service verifier needs PostgreSQL tools, OpenSSL, a C compiler for the isolated
resolver fault shim, and permission to bind owned local sockets. It creates its own
clusters, synthetic users/credentials, shared directories and HTTP/TLS/DNS peers.
No host resolver/trust configuration is changed. Browser verification is a separate
actual Chrome workflow, not substituted by the backend/native runner.

## Observed behavior

| Evidence | Result and practical boundary |
| --- | --- |
| [Independent final replay](w6_independent/results.json) / [coverage](w6_independent/COVERAGE.md) | 143 passing observations; 180 ordinary plus 11 raw-socket app requests; 22 clean application exits; no cleanup errors. Actual PG, two Diamond nodes, bytes, HTTP/TLS/DNS and an external Rust consumer. |
| Private files | Empty, binary and exact 8 MiB transfer through A and verified download through B. Parent/owner/ticket checks, pending denial, concurrent finish, current permission revocation, safe MIME headers, corruption denial, streaming interruption/30-second expiry, quotas and bounded automatic cleanup. |
| Publication recovery | A real deferred PostgreSQL abort after physical rename leaves staged metadata and unreadable bytes. Explicit finish retry verifies and publishes one object. This is a controlled failure, not proof of every crash/ack-loss schedule. |
| Typed HTTP/secrets | Actual verified TLS calls; bounded typed JSON; path/origin/method/redirect/IP constraints; wrong trust/hostname, private addresses, compressed/malformed/oversized/slow replies, direct and JSON-escaped credential reflection rejected. Dropped POST response observed once with unknown outcome. |
| Async DNS | Owned UDP A/AAAA and CNAME reach the original-hostname TLS fixture. Mixed/disallowed answers cause zero HTTP calls. Dropped replies return in 1.0076 seconds for a one-second deadline, then fresh lookup recovers. No blocking system resolver is used in the request. |
| Cancellation resources | Three equal batches of three caller disconnects produce nine upstream calls, each peer drains, fresh same-node calls work, and FD counts stabilize 20→30→30→30 after bounded pool warmup. This is bounded evidence, not universal leak absence. |
| [Root compiler probes](w6_checker_results.json) | 19 actual CLI cases covering positive use and grant/type/secret/browser/file rejection. These overlap the independent compiler cases and are not additional distinct requirement coverage. |
| [W3 replay](w6_previous_w3_results.json) | Same final executable/library: 185 semantics cases, 30 checker cases, 121 wire cases, three regression reproductions, five native checks and 184 exact native corpus values pass. Historical receipts remain unchanged. |
| [W5 replay](w6_previous_w5_results.json) | Same final artifacts: 294 observations and 1,956 real account/API HTTP requests pass, including identity delivery and scoped mutation regressions. |
| Build/API checks | Final build, strict all-target/all-feature Clippy, formatting, warning-denied rustdoc, and 32 documentation examples pass. Supporting checks do not replace runtime evidence. |

## Browser workflow

The user explicitly approved the named synthetic upload/download/delete workflow.
Using real Chrome controls, Alice signed in, selected
`w6-browser-synthetic.txt` through the native chooser, and invoked the Diamond
upload event. The UI returned ready metadata for the expected issue and owner.

The file was **131,112 bytes**, SHA-256
`096ff62a2485e794ca05b5f1509d245070e2e4035f6fd03f5d8df8316ee0f07f`.
An independent authenticated request to node B matched every byte. Chrome's native
download completed and its saved file independently matched that length/hash.
Approved deletion cleared the UI attachment ID, displayed “Deleted.”, denied node B
reads, and removed both physical object paths; reconciliation reclaimed metadata.
The inventory call displayed its exact Unicode fixture reply without a
visible error. After selecting the same approved file again without submitting,
logout cleared both its native selection and private attachment state. This complete
workflow was on `18878345`, before the isolated final public-IPv6 predicate correction;
its raw [complete browser receipt](w6_browser_initial_results.json) is preserved.
The frontend and file transport code did not change in that correction.

On the final `0aa105a7` executable, Chrome repeated chooser/upload/status, independent
B byte/hash readback, and approved UI deletion with B denial. A repeat native download
event timed out, and direct browser navigation returned `invalid_request`; no new
saved copy was observed. The cause was not established and this repeat is **unconfirmed**,
not a passing final-browser claim. Final native/backend download observations do pass.
Screenshots
were observed during CUA verification; no saved screenshot artifact is claimed.

See [final browser and independent byte observations](w6_browser_results.json) and
[owned fixture driver](web_six_browser.py). The disposable browser servers, external
fixture, PostgreSQL cluster and temporary shared directory were stopped/removed.
The explicitly downloaded synthetic copy remains at `/home/doll/Pictures/attachment.bin`.

## Findings repaired before completion

The initial implementation timed out its future but used Tokio's blocking system
DNS resolver. A process-local fault shim delaying only a synthetic hostname held a
one-second request for **8.0063 seconds** because runtime teardown joined that work.
The initial raw failure is preserved in
[dns_initial_failure.json](w6_independent/dns_initial_failure.json).

The repair captures native resolver/hosts configuration at startup and uses owned
asynchronous Hickory DNS for every hop. Runtime cancellation now releases its DNS
sockets without waiting on getaddrinfo. The old fault reproduction and a separate
real nonresponsive UDP fixture both pass on the final binary. See
[findings and fixture corrections](w6_independent/FINDINGS.md). Fixture assumptions
and bounded FD warmup were distinguished from actual product defects.

A final narrow public-address probe also found that `3ffe::1` and `3fff::1`
returned 503/deadline instead of immediate policy denial. This established a policy
gap, not successful network contact. The public IPv6 guard now conservatively
excludes those ranges; both final real-interface probes return 403 in milliseconds.
Original and repaired observations are preserved in the independent findings.

## Remaining trust and coverage limits

`shared-fs-v1` requires an operator-provided persistent mount common to all nodes.
The test used a real common directory; it does not qualify remote-mount durability,
replication, power-loss/fsync behavior or a malicious storage/database administrator.
A timeout cannot cancel an indefinitely blocked kernel filesystem operation.

The campaign does not establish every crash schedule, lost PostgreSQL commit
acknowledgment, blocked downstream download deadline, established-connection DNS
rebinding, every IPv6/mixed-family permutation, every cross-app/binding ticket
combination, production public ingress/TLS/capacity, malware safety or SMTP.
Already authorized/transmitted bytes cannot be recalled. Direct credential
reflection protection is not arbitrary taint tracking. Duplicate incoming requests
may execute independently; outbound POST is never automatically replayed.
No old/new deployment compatibility, durable jobs, arbitrary native loading/shell
execution, optional Git demonstration, or production deployment is claimed here.
