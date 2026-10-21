# Checkpoint-three regressions

`request_shadow.dia` is a confirmed compiler/runtime mismatch discovered during
this pass. The initial implementation checked `request.authenticated` as an
ordinary record field inside `read`, but evaluation substituted the anonymous
runtime principal. The actual CLI exited successfully and printed `false`.
The required result is `true`: function parameters retain their checked meaning.
Context field/method dispatch now applies only when a local does not shadow it.

Replay from the repository root:

```sh
cargo build --locked
target/debug/diamond run verification/regressions/request_shadow.dia
python3 verification/checkpoint_three.py
```

The campaign also replays `returned_transaction_error.dia` and
`caught_transaction_failure.dia` with separate temporary SQLite files. Both must
fail and leave zero rows. They guard the two rollback gaps identified while
connecting recoverable results: returned Err values and caught operation errors
must not commit an earlier create. The campaign independently inspects SQLite;
it also checks a subsequent successful transaction commits once.
