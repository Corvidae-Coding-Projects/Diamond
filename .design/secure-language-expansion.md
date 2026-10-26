# Secure language expansion

**North star:** If a developer needs to remember something for an application
to be secure, the developer should not have to remember it.

**Status:** L1-L8 implemented and locally qualified on 25 October 2026. The
bounded evidence and explicit untested areas are recorded in
`verification/secure_language_l8_coverage.md`.

## Outcome

Add the remaining general-application primitives to Diamond without turning
security into framework conventions. The compiler and runtime own mechanical
safety. Source code supplies only business decisions that cannot be inferred,
such as who may read a document, which destination may receive mail, and which
query fields an API intentionally exposes.

## Non-negotiable rules

1. Unsafe states should be unrepresentable before they become lint rules.
2. Omission fails closed. Missing policy, quota, destination, or retry semantics
   is a compile or startup error, never a permissive default.
3. Authority is never an ordinary serializable value.
4. Managed data is bounded by type, manifest ceiling, and runtime aggregate
   budget. A lower deployment limit may narrow but never raise a release ceiling.
5. Long-lived and delayed work reacquires current authority at the protected
   operation. Capturing a request's authority is not allowed.
6. External effects have stable idempotency identities or are explicitly
   non-retryable. "Exactly once" is not promised across distributed boundaries.
7. Trusted markup has no string constructor. Secrets have no text, equality,
   logging, persistence, browser, or generic wire representation.
8. The same policy-bearing operation guards browser, API, job, stream, and native
   service entry points. A controller cannot opt out by forgetting middleware.

## Checkpoints

### L1 - security kernel and safe documents

- Derive client/wire/secret properties from full checked types in one fail-closed
  analysis shared by compiler and runtime sinks.
- Add bounded `markdown(Text) -> Html`; remove raw HTML and embedded images,
  constrain links, and keep `Html` non-serializable and non-browser-managed.
- Prove hostile input remains inert through the real compiler and evaluator.

Exit: nested secret-bearing records and enums cannot cross the server entry or
wire boundary; arbitrary text cannot construct `Html`; bounded Markdown renders
useful content without script, raw HTML, active schemes, or remote image loads.

### L2 - opaque blobs

- Add `Blob<"binding">` as a nominal opaque reference, never an in-memory byte
  string or bearer authorization token.
- The runtime rechecks principal, binding, object state, size, and policy on every
  consume operation. Handles cannot be displayed, compared, persisted outside
  declared fields, or moved across bindings.
- Upload, download, hash, metadata, and deletion use fixed buffers and aggregate
  permits. Incomplete objects remain private and executable serving is opt-in.

### L3 - bounded streams and streaming APIs

- Add opaque, nominal `Stream<"channel">` descriptors created only by
  `stream("channel", parent)`. They carry identity, not authority; every open and
  periodic read rechecks the current session and parent policy.
- Cancellation, deadlines, cleanup, backpressure, per-frame bounds, concurrency
  permits, reconnect limits, and slow-consumer eviction are runtime-owned.
- Do not expose network byte streams as managed language values. Bounded native
  adapters stream uploads, downloads, HTTP, Git, and delivery bodies. Source sees
  only an opaque `Blob<"binding">` or a checked decoded result, so it cannot buffer
  an unbounded body into `Text` or retain request authority.

### L4 - declarative mail

- Implemented manifest-bound plain-text templates, runtime-validated deployment
  addresses, automatic message construction, control-character rejection, and no
  raw SMTP/header surface.
- Submission uses a transactional outbox, content-bound owner deduplication key,
  bounded fenced retries, cancellation, per-recipient suppression, and current
  sender/recipient/resource reauthorization immediately before output.
- The first native adapter is an explicitly initialized private directory store.
  Its opaque identity is bound to app and binding, replacement fails closed, and
  immutable delivery-ID files make uncertain completion safely replayable. SMTP
  transport is deliberately outside this checkpoint rather than exposed unsafely.

### L5 - reusable durable workflows

- Implemented manifest-declared state machines with a wire-safe input type and
  enum state/output type, a fieldless initial variant, explicit terminal variants,
  database-clock timers, optional transactional compensation, bounded retries and
  transitions, owner idempotency, and runtime-owned parent concurrency.
- The compiler requires exhaustive typed handlers and explicit effects. Workflow
  handlers and compensation cannot invoke HTTP, mail, Git, delivery, operations,
  jobs, or another workflow, so no uncertain external outcome is automatically
  replayed. Transactional database/event effects commit with a fenced transition.
- Workers reacquire current account, API-scope, and parent write authority for every
  transition. Leases, fences, cancel requests, retry timing, and terminal state are
  runtime-owned and survive process failure.

### L6 - declarative secret consumption

- Implemented manifest declarations binding a closed secret kind to one HTTP or
  OAuth adapter and exact named audience. Duplicate consumers, mismatched
  capabilities/providers, unknown kinds, and audience drift fail compilation.
- Authenticated HTTP calls can omit the former secret argument. The compiler then
  requires a declaration and the native adapter injects the credential; source
  never obtains a handle. Runtime startup requires the corresponding deployment
  secret, and restart captures rotation as a new opaque generation.
- Deterministic artifacts contain a kind/audience inventory but never secret
  values or deployment environment names. Runtime values remain zeroizing,
  non-debuggable, non-wire-safe, non-browser, and redacted on failure. The older
  explicit opaque handle remains checked for existing low-level code, but is not
  needed or taught for new application code.

### L7 - checked graph queries

- Implemented query documents attached to same-named RPC operations. A resolver
  must be exactly one exported `page` or `join` repository expression with
  manifest-declared cursor/limit parameters and exact read-resource effects; it
  cannot call helpers, perform mutations, or introduce host effects.
- The compiler derives every scalar leaf path from the wire-safe result graph and
  requires an exact selection. It statically verifies depth, node, row, cost,
  database-query, fixed five-second, and response-byte budgets. Runtime rechecks
  cursor/limit before evaluation and row/node/depth/bytes after typed encoding.
- Repository policy is applied inside the one base page/join plan under the current
  principal and any API restriction. Cookie RPC and any API operation sharing the
  resolver receive the same plan checks. The deterministic contract publishes the
  field selection and budgets as bounded introspection.

### L8 - adversarial qualification

- Exercised compiler/CLI, Chrome/network, PostgreSQL, worker, filesystem, and
  native-service interfaces against one exact final binary.
- Attacked cross-binding handles, secret nesting, rich-text parsers, decompression,
  slow streams, cancellation, replay, expired leases, query-cost/API evasion,
  policy revocation, malformed codecs and durable state, artifact tampering, and
  resource exhaustion.
- Fixed the final consistency finding: older public examples no longer teach or
  require source-visible secret handles. Retained exact hashes, regression
  receipts, a coverage ledger, trust boundary, and explicit untested areas.

## Definition of done

A checkpoint is complete only when its safe behavior is the default executable
path, omission cannot silently weaken it, real-interface positive and adversarial
checks pass against the final artifact, and the public guide teaches intent rather
than a security ritual. Documentation-only syntax does not count as implemented.
