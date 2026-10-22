# Reserved API metadata boundary

On candidate `778ce2e23f2a4e8d4a55052efe2f2f303d9efb89fe292b98533e0bdcb136e34b`,
checking an operation named `_contract` succeeded even though its route was
reserved for public metadata and could not dispatch as an ordinary operation.
GET metadata also returned200 when an explicit API-version header was2,0,
`invalid` or `01`. Omitted version and explicit1 correctly produced identical
current metadata. The actual initial receipt is `initial.json`; the original
reserved-operation source fixture is retained alongside this file.

The implementation owner rejected the reserved binding during checking and
validated an explicit metadata version header. The complete independent runner
replays the six observations on its final pinned artifact and preserves that
result under `reports.api_contract_boundary` in `results.json`.

```sh
python3 verification/w5_independent/findings/api_contract_boundary/replay.py \
  --binary target/debug/diamond \
  --report verification/w5_independent/findings/api_contract_boundary/replay-results.json
```
