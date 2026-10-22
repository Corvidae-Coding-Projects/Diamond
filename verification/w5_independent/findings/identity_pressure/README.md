# Identity failure under bounded pressure

An initial eight-client run issued fourteen credential requests while a real
PostgreSQL trigger forced each admitted INSERT to abort with SQLSTATE40001.
Artifact `778ce2e23f2a4e8d4a55052efe2f2f303d9efb89fe292b98533e0bdcb136e34b`
returned eight confirmed409 responses and six safe503 responses. No credential
row committed, and the shared global and peer rate counters each recorded14.
Sequential minimization returned only409 and preserved the exact counters.

This is a retained pressure observation, not a claim that every over-capacity
request must reach a database abort. The declared runtime permits bounded503
admission/deadline failures. The replay fixes the oracle before execution:
only409/503, no credential writes, independently committed attempts for admitted
failed work, bounded elapsed time, successful fresh work after pressure, and
clean owned-process shutdown. A success, hidden write, rate loss, timeout,
crash or failure to recover fails the replay.

```sh
python3 verification/w5_independent/findings/identity_pressure/replay.py \
  --binary target/debug/diamond \
  --report verification/w5_independent/findings/identity_pressure/replay-results.json
```
