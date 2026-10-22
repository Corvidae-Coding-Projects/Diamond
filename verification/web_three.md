# W3 observable contract

This checkpoint implements the W3 row of `.design/general-web-apps.md`, while the
W4–W10 database/deployment limits remain unchanged. Preserve W1/W2 caller behavior.

- Shared records, nominal IDs, enums, Option and Result cross Rust evaluation,
  browser state, pure functions and explicitly declared RPC. Application enums
  may be the error parameter of Result. `?` preserves the declared error type.
  Host failures remain failures, never fabricated application errors.
- Decimal is signed Int64 millionths; decimal multiplication/division specify
  `half_even` or `half_away` rounding. Timestamp/Duration are signed Int64 Unix
  milliseconds/milliseconds. Construction/conversion is pure; browser time does
  not acquire server clock authority. Text operations count Unicode scalars.
- Components have typed properties, state, declaration-ordered derived values,
  mount/unmount handlers, conditionals, keyed lists and nested components. Keys
  preserve local state and DOM identity across insertion/reorder; duplicates
  reject without committing the proposed state. Event errors preserve state.
- Text/password/checkbox/textarea forms have labels and native validation;
  submit runs a declared Diamond handler once, exposes loading/error feedback,
  and retains focus and edits. No handwritten application JavaScript.
- Declared internal routes support direct navigation and back/forward. Async
  results belong to the initiating component generation; old replies and replies
  after unmount cannot overwrite current state. Cancellation never implies that
  a mutation rolled back, and mutations are never automatically replayed.
- Public deterministic initial component content is rendered by Rust into HTML.
  Hydration adopts existing DOM, attaches one set of handlers and rejects an
  incompatible contract/structure. No authenticated values are embedded at build.
- Styles and assets are validated project-relative inputs with hashed output
  references. Standard CSS is served under same-origin CSP. Browser source maps
  contain only reachable browser/shared sources; server canaries stay private.
- Verify through actual CLI/build/HTTP, PostgreSQL and Chrome controls. Compare a
  generated value corpus independently between Rust and JavaScript. Exercise
  invalid types, bounds, malformed wire data, hydration mismatch, failures and
  recovery, keyed identity, lifecycle, routing and delayed-response schedules.
  Retain concrete failures and final artifact fingerprints.
