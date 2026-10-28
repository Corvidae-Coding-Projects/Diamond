# Runtime performance remediation acceptance

These requirements precede the final replay. All data and processes are disposable.

| Scenario | Required result |
|---|---|
| Mixed workload, concurrency 1, 8, 32, 128, two repeats | Valid successful values, no unexpected/transport failures, exact persisted create count. Default queue materially reduces the historical 97% rejection fraction. |
| Hold the issues table lock, requests=1 and queue=2 | One active request and at most two waiting; excess returns 503 `overloaded`, Retry-After, and explicit safe replay header before application effects. |
| Queue deadline and client cancellation | Timely bounded rejection; waiting and active counters recover; later valid request succeeds. |
| Release held database lock | Admitted mutations finish once, rejected mutations create no row; no leaked database leases. |
| Conflicting concurrent updates | Confirmed aborts may retry from a fresh identity/policy snapshot, at most twice; writes commit once; unknown outcomes and host-effect/action operations are never replayed by the ordinary retry helper. |
| Generated client under actual overload | Backoff respects server guidance; at most two retries; cancellation and identity replacement prevent another attempt; generic failures/unknown outcomes never retry. |
| Runtime configuration boundaries | Defaults accepted; zero/excess/inverted resource limits and unknown fields rejected by the real checker. |
| Persistent HTTP | Two sequential actual API requests on one connection succeed, with the same client socket, in both benchmark variants. |
| Evidence | Application, PostgreSQL, and client CPU/PSS are reported separately; admission policy, request pacing, connection reuse, thread/worker profile, affinity and host limits are explicit. |
