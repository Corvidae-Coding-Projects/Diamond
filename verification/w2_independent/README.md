# Independent W2 oracles

These drivers were authored/executed in a separate verifier context against the
W2 observable contract. They exercise the actual CLI, two-node HTTP and native
PostgreSQL; they do not recreate application storage or authorization.

Run the portable wrapper:

```sh
python3 verification/web_two_independent.py
```

It pins a copy of the requested executable, starts/stops its own fixture, runs
197 HTTP/SQL checks, 14 source/contract boundary cases and three cross-module
effect-budget cases, checks every exit/case, and redacts ephemeral CSRF values.
The independent seed is `0x22C0DE`. Child drivers require the wrapper's
`DIAMOND_W2_BINARY` environment and HTTP fixture state, so invoke the wrapper.

Retention changed absolute temporary paths into checkout-relative paths, added
failing exit codes to the original case reports, and renamed the HTTP driver to
avoid shadowing Python's `http` package. The expectations remain unchanged. The
portable versions were replayed against the final candidate. Main browser,
restart, concurrency and delivery/commit-fault observations are separate in
`../web_two_results.md`.
