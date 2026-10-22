# Bound JSON payload regression

The first actual W2 create returned HTTP 400 `invalid_input` instead of saving a
normal bounded record; `before.json` preserves its executable hash and observed
failure. A PostgreSQL JSON-typed placeholder cannot be bound as Rust text without
an explicit text cast. Queries now bind text and then cast to the storage type.
Database adapter errors also remain opaque runtime failures rather than becoming
catchable user-input errors.

Replay the ordinary control and exact readback through the retained real
CLI/HTTP/PostgreSQL fixture:

```sh
python3 verification/regressions/w2_unicode_storage/replay.py
```

The full two-node create/read/update/delete journey in `../../web_two.py` also
detects this original mismatch.
