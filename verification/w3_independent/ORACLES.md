# Independent W3 observable oracles

Created before final qualification; the implementation is in progress. No pass is
claimed until the actual final executable is pinned and operated.

- CLI scalar corpus: fixed Int64 edges and a deterministic generated corpus.
  Python `decimal.Decimal` with precision 100 independently specifies millionth
  multiplication/division with ROUND_HALF_EVEN and ROUND_HALF_UP. Text outputs
  use Python Unicode scalar slicing, substring replacement and whitespace in
  the chosen explicit cases. Distinct ordered values exercise lists.
- Compile failures: incompatible application errors and DataError/application
  error bridges reject before execution; private/generated type names reject.
- Raw HTTP: real initialized PostgreSQL, real login/session, two real Diamond
  servers. Echo a record containing nominal IDs, bounded Unicode text, Int64
  lists, fixed Decimal, Timestamp, Duration, Option and application enum. Echo
  both branches of typed Result and preserve source enum tags. Deliberately
  mutate one shape/type/bound at a time; reject and then accept a good value.
  Unknown fields, missing fields, duplicate fields, wrong nominal tag, numeric
  coercion, noncanonical Int64, wrong enum payload count and list count reject.
  Lone/reordered UTF-16 surrogates reject; NUL and newline scalars in valid
  bounded text round-trip. Escaped duplicate field spellings reject.
- Actual HTTP artifact/SSR: exact route-specific initial escaped text; hashed
  assets byte matches; private server file/unknown route denials; source map
  only browser/shared source; private server canary absent from public assets.
  Distinct unused private shared helper/type/browser-component canaries must
  remain absent from both served JavaScript and browser source maps.
- Native consumer: call public library compile/evaluate/render_page, inspect
  returned text and structured Error class, reject an unknown route.

Browser lifecycle, DOM identity/focus, native validation, form loading,
back/forward, hydration mismatch and delayed reply schedules are operated by
root through real Chrome controls. Raw HTTP cannot substitute for those checks.

Concrete early findings are in `findings/result_bridge_before.json` (DataError
result bridge) and `findings/type_privacy_before.json` (generated private type
name), with minimal source fixtures.
The empty replacement fixture's native expected result is one `x`; browser
SSR/hydration is expected to agree. Shared default-record constructor fixture
expects defaults to exist in both targets. A valid nonempty bound textarea must
hydrate while preserving its initial native value.


Legacy DataError wire scope was clarified before the new error-wire probes:
`Denied`, `InvalidInput`, `Aborted`, and `ApplicationFailure` are the complete
accepted fixed codes. The first three map to their native safe human messages;
ApplicationFailure maps to `Application failed.`. Pure Err(Text), including
function return, argument and ? propagation, retains exact local text. The wire
redacts custom DataError messages; application enums carry declared public detail.
Valid shrinking/same-size replacements are judged by actual output byte count,
including exactly 1,048,576 bytes. A genuinely larger output must fail.
