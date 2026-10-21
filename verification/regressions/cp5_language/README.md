# Checkpoint-five language exploration

`record_clone.dia` is the concrete legal source independently replayed before the
allocation repair. The frozen v0.4.0 executable accepted checking, then aborted in
two fresh child processes while constructing immutable recursive records. The
original executable fingerprint, limits and raw outcomes are in
`record_clone_before.json`. The expected result was either `7` or an ordinary
resource-limit error, with no abort, panic or hang.

The saved failure is opt-in so ordinary exploration does not accidentally repeat
an exhaustion experiment on an unrepaired binary. After building the repaired
program, replay it through the actual CLI with the same child-only limits:

```sh
python3 verification/language_exploration.py --replay-resource-regression
```

The ordinary generated run is:

```sh
python3 verification/language_exploration.py --seed 858629
```

Each run makes 124 real CLI invocations and compiles/runs a native public-API
consumer. Expectations are independent mathematical values, declared immutable
state, parsed HTML text/attributes and documented rejection limits. A second seed
changes signed operands, record payloads and list values. The driver records
input, driver, executable, native source and library fingerprints. Successful
temporary inputs are deleted; an unexpected result preserves the exact input and
last receipt under its printed temporary directory.

This is bounded behavioral evidence. It does not establish exhaustive grammar,
memory isolation, token collision resistance, wall-clock-change handling,
concurrent sessions, distributed expiry or production authentication security.
Session reissue is explicit in the native caller: revoke the old token and issue a
new one. The consumer does not claim that `SessionStore::issue` automatically
revokes earlier sessions for the same identity.
