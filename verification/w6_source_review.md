# W6 source and boundary review

This records the changed paths and actual callers inspected during implementation.
It is not a claim of whole-repository audit or exhaustive security proof.

| Boundary | Source/callers inspected | Review focus |
| --- | --- | --- |
| Native authority | `integrations.rs`, public project APIs, `main.rs`, `host.rs`, `authority.rs` | explicit config/env boundary, redacted Debug/errors, no artifact secrets, bounded CA reads, synchronous runtime preconditions |
| Managed secrets | parser/type syntax, `checker/integrations.rs`, checker effect closure and named calls, `value.rs`, interpreter host dispatch, wire/storage type checks | nominal secret handles, static destination budget, no wire/storage/display/comparison/entry escape, transitive transaction/write rejection |
| Outbound HTTP | integration caller, URL validation, asynchronous DNS/address policy, pinned reqwest client, response decoder, RPC/API bindings | path segments, redirects, proxies, TLS, connected peer, bounds/deadlines, disabled retries/decompression, POST uncertainty and direct credential reflection |
| File authority | manifest file bindings, `relational/runtime.rs` attachment caller, file operations, current session/account principal, connected header/cookie/origin checks | current parent Read/Update policy; ticket hash/owner/app/binding/ID/expiry; no owner bypass; exact length and quotas |
| Shared files/recovery | files store/schema, operations/transport, PostgreSQL store setup/finish/drop, connected runtime lifetime and pool ownership | generated paths; no-follow regular files; marker identity; stage/rename/commit ordering; pending invisibility; corruption verification; bounded reconciliation and cancellation cleanup |
| Generated frontend | browser lowerer, asynchronous rewriter, runtime remote/event/identity/reconcile code, server rendering and asset contracts | native file inputs, static binding/input IDs, bounded upload protocol, stale generations, session epochs, file selection clearing, native download |
| Documentation/artifacts | build contract emit/load/recheck path, README, W6 guide/example, checkpoint plan and evidence | current schema only; explicit shared mount/durability trust; no production/SMTP/jobs claim; artifact descriptors and reproducible final fingerprints |

Actual-interface evidence is in the separate W6 receipts and historical-scenario
replays. Compiler rejection receipts are CLI runs against independently specified
expectations. Native doctests/fmt/Clippy/rustdoc are supporting build/API checks,
not substitutes for service/browser observations.

Trust limits include native operators and dependency/toolchain trust; a shared
filesystem administrator can disrupt/hang I/O; PostgreSQL plus store tampering can
rewrite both metadata and bytes. A local shared-directory test cannot establish
remote-mount durability, replication, infrastructure hardening, public capacity,
malware safety or exhaustive DNS behavior coverage. External endpoints are trusted
credential recipients; direct reflection protection is not arbitrary taint tracking.
Incoming duplicate requests may execute independently. Authorization is a request
snapshot and cannot recall already transmitted data.

A final real fault injection disproved the initial DNS deadline assumption: a
process-local shim delaying only the synthetic hostname
`w6-stall.invalid` held a one-second request for 8.006 seconds. The preserved
`w6_independent/dns_initial_failure.json` precedes the fix. The implementation now
uses owned asynchronous Hickory DNS rather than Tokio's blocking system resolver.
Final DNS receipts must replay both the removed getaddrinfo path and an actual
nonresponsive UDP DNS fixture; absence of the old call alone is insufficient.

The final public IPv6 correction was independently reproduced as two non-policy
503 results before a conservative range exclusion. Both reserved literal probes
return immediate 403 in the final build; no successful IPv6 contact is claimed.
