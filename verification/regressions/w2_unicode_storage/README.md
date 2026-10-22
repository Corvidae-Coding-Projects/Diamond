# Valid Unicode text storage regression

An independent fresh reproduction sent `create_issue("w", "t", "\u0000")`.
JSON Text and Diamond's declared bounds accepted it, but PostgreSQL JSONB rejected
the payload. The endpoint returned HTTP 503; zero rows were stored, while a normal
control save succeeded. `before.json` preserves those actual observations.

W2 now stores JSON, binds `text::json`, reads raw `payload::text`, and decodes in
Rust. This preserves the complete Unicode string without PostgreSQL text-field
extraction. Expectations were not weakened to reject NUL. `after.json` records
HTTP 200, exact HTTP readback and independent raw SQL decoding to codepoint zero.

```sh
python3 verification/regressions/w2_unicode_storage/replay.py
```

The replay starts/stops a new real PostgreSQL cluster and two actual application
processes. It exits nonzero on the original mismatch. The generated and
independent campaigns also cover NUL versus literal backslash-u strings and other
Unicode/control text. No production database is involved.
