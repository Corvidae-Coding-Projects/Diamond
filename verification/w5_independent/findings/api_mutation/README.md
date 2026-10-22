# Scoped API mutation bindings

Expected: an owner with current red-workspace policy and a red `edit`/`delete`
credential can update or delete a red record. Cookie RPC and bearer API dispatch
must preserve the same data-policy behavior, with bearer scope attenuation.

Original artifact `fb29b8cea364c181bdfe102421a8e5759537a9069277ce17411e1fb6aa26b843`:
ordinary cookie RPC updated rank11 to49. The exact bearer API function returned
HTTP503 `unavailable` and left rank11. A permitted API deletion likewise503 and
left its row. Fresh clusters reproduced this under both schema-owner and
restricted runtime roles. PostgreSQL had no ERROR/FATAL/PANIC lines. Concrete
initial observations are retained in `initial.json`.

The implementation owner traced reuse of lookup-only scope bindings in mutation
SQL parameters and patched the binding ownership. The independent verifier did
not modify product code. Replaying against artifact
`778ce2e23f2a4e8d4a55052efe2f2f303d9efb89fe292b98533e0bdcb136e34b`
passed every owner/restricted RPC/API update and API deletion observation;
`patched.json` records that intermediate replay. The complete W5 runner repeats
this replay on its separately pinned final artifact.

Replay (real owned PostgreSQL and two actual Diamond nodes per database role):

```sh
python3 verification/w5_independent/findings/api_mutation/replay.py \
  --binary target/debug/diamond \
  --report verification/w5_independent/findings/api_mutation/replay-results.json
```

No credentials or plaintext delivery messages are retained in these receipts.
