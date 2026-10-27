# Feature: Diamond language production readiness

## Summary

Make Diamond a dependable language, compiler, and runtime for substantial web
applications. Establish one current language contract, complete typed navigation,
forms and component interaction, introduce reliable mutation actions, strengthen
runtime guarantees, and finish the tools needed to write and debug Diamond.

**Status:** LP1–LP8 complete, 27 October 2026. Initially written
against runtime source commit `4c0195f`; the current working-tree candidate is
language 0.20.0, artifact format 5, and wire 7. The [LP5 design](lp5-actions.md),
[action guide](../docs/actions.md), and [LP5 evidence](../verification/lp5/) record
the current reliable-mutation contract and qualification. The
[LP6 design](lp6-hardening.md) and [LP6 evidence](../verification/lp6/README.md)
record cross-boundary security, current-authority, budget, cleanup, and prolonged
resource qualification plus the retained failures repaired in LP6. The
[LP3 ledger](../verification/lp3/README.md) records typed-input qualification;
the [LP2 ledger](../verification/lp2/README.md) records the earlier
connected slice, real failures/fixes, final hashes, and remaining limits.
LP1's [contract, source map, fresh baseline, and gaps](../verification/lp1/README.md)
meet its bounded exit gate; they do not qualify the unimplemented expansion.

**North star:** If a developer needs to remember something for an application to
be secure, the developer should not have to remember it.

Application authors still decide business policy: who may read a document, who
may approve a change, and which external destination is appropriate. Diamond
owns the mechanical enforcement of those decisions, including validation,
escaping, authority checks, bounded execution, identity changes, and safe
handling of retries and uncertain outcomes.

This plan uses **LP1–LP8**. The completed MVP, W1–W10, G1–G6, and L1–L8 plans
remain historical records with their own evidence boundaries.

## Requirements

- REQ-1: Publish one authoritative description of the implemented language,
  supported execution profiles, compatibility rules, wire and artifact versions,
  evaluation order, errors, transactions, Unicode behavior, and resource limits.
- REQ-2: Preserve transitive effect checking, explicit native grants, default-deny
  resource policies, recursive restricted-value analysis, and server/browser
  separation across every new language construct.
- REQ-3: Add named reactive routes with typed path and query parameters and
  checked internal link construction. Direct requests and client navigation must
  use the same bounded parsing and canonicalization contract.
- REQ-4: Add typed reactive forms derived from checked schemas, with bounded raw
  field state, canonical decoding, shared pure validation, structured field
  errors, and authoritative validation on the server.
- REQ-5: Add typed event arguments and component outputs, preserve keyed component
  identity and focus, and supply accessible form/control behavior through checked
  primitives rather than repeated application conventions.
- REQ-6: Give server actions typed states for submission, success, validation
  rejection, authorization rejection, confirmed abort, and uncertain completion.
  Retain explicit application errors without exposing internal diagnostics.
- REQ-7: Support bounded durable identities and transactional receipts for eligible
  database actions. Duplicate submissions must not repeat a committed mutation;
  external effects must retain their declared retry and uncertainty contracts.
- REQ-8: Recheck current identity, permissions, binding, and resource scope when
  consuming handles, retrieving action status/results, or executing delayed work.
  A previously authorized result must never become an authorization bypass.
- REQ-9: Enforce execution, value, rendering, request, concurrency, and lifetime
  budgets consistently. Cancellation and failures must release owned resources
  and leave subsequent valid operations usable.
- REQ-10: Provide precise source diagnostics, browser event source mapping, a
  semantics-preserving formatter, and useful editor support using the compiler's
  actual parser, resolver, and checker.
- REQ-11: Verify semantics and security through independently defined expectations,
  generated valid and hostile programs, Rust/browser comparisons, real interfaces,
  fault sequences, and retained minimal reproductions.
- REQ-12: Exercise the completed language through Diamond Forge and a non-Git
  application, and update current documentation and examples to teach the safe
  default path without historical checkpoint contradictions.

## Acceptance Criteria

These criteria are complete for the bounded language profile and exact candidate
recorded in the LP8 ledger. They do not certify deployment infrastructure or
unexercised provider/assistive-technology combinations.

- [x] AC-1 (REQ-1): A capability/semantics matrix maps the canonical specification
  to source and executable observations. Current guides identify implemented
  behavior, intentional profile differences, and unsupported behavior consistently.
- [x] AC-2 (REQ-2): New route, form, event, component-output, and action paths reject
  missing effects/grants, forged identity, restricted-value escape, and unauthorized
  reads/writes through the compiler or the authoritative runtime boundary.
- [x] AC-3 (REQ-3): A typed detail URL opens the same resource through direct HTTP,
  client navigation, reload, and back/forward. Malformed or ambiguous parameters
  reject without reaching application mutation code or escaping the application.
- [x] AC-4 (REQ-4): The same declared schema produces matching browser/server
  decoded values and validation results. A raw client bypassing browser validation
  cannot submit malformed, extra, duplicated, forged, or out-of-bounds fields.
- [x] AC-5 (REQ-5): A keyed child component emits a checked resource-specific event
  to its parent without manual ID copying. Reorder/removal preserves surviving
  state and focus and cannot deliver a stale callback to another instance.
- [x] AC-6 (REQ-6): Real client journeys distinguish ordinary application rejection,
  denied access, confirmed rollback, lost response, and uncertain completion while
  preserving committed server state and redacting internal details.
- [x] AC-7 (REQ-7): Concurrent and interrupted repeats of an eligible action identity
  produce one committed database mutation and a recoverable typed receipt. Changed
  inputs, identity/scope, version, or expired receipt identity cannot silently replay.
- [x] AC-8 (REQ-8): Revocation, account disablement, logout, and identity replacement
  prevent access through action receipts, callbacks, blobs, streams, and delayed
  work under the documented concurrency/transaction ordering rules.
- [x] AC-9 (REQ-9): Boundary and prolonged-run campaigns observe bounded resources,
  completed cleanup, explicit overload/failure behavior, and successful subsequent
  work after cancellation, exhaustion, and dependency failures.
- [x] AC-10 (REQ-10): Formatter round trips preserve parsed meaning and comments;
  editor errors and navigation use real compiler locations; browser failures map
  to the relevant Diamond source without publishing server source or secrets.
- [x] AC-11 (REQ-11): The assembled compiler/runtime passes the retained failures
  and generated language campaigns against recorded final source/artifact hashes.
  Results distinguish verified behavior, failures, and unexercised boundaries.
- [x] AC-12 (REQ-12): Real browser journeys in Forge and a non-Git app use the new
  primitives for navigation, validated forms, reusable components, and reliable
  mutations. A developer can follow the current guides without implementing
  duplicate security plumbing.

## Architecture

### Current implementation to build on

| Area | Existing source | Relevant current behavior |
| --- | --- | --- |
| Parsing and source types | `src/lexer.rs`, `src/parser.rs`, `src/syntax.rs`, `src/hir.rs` | Indented syntax, checked declarations and expression trees, source locations |
| Types, effects and authority | `src/checker.rs`, `src/checker/integrations.rs`, `src/authority.rs`, `src/value.rs` | Transitive effects, sealed checked programs, nominal and restricted values |
| Reference evaluation and utilities | `src/eval.rs`, `src/standard.rs` | Bounded server evaluation, checked arithmetic and utility semantics |
| Policy and relational state | `src/policy.rs`, `src/policy/postgres.rs`, `src/project/storage.rs`, `src/relational/` | Policy-aware operations, serializable requests, bounded pools and explicit uncertain outcomes |
| Browser compiler | `src/project/lower.rs`, `src/project/lower/asynchronous.rs`, `src/project/resolve.rs` | Typed browser IR, static route paths, named events, asynchronous expression lowering |
| Browser execution and rendering | `src/project/runtime.js`, `src/project/render.rs`, `src/project/browser.rs` | Keyed rendering, hydration, cancellation, identity epochs and generated source maps |
| Input, output and transport contracts | `src/forms.rs`, `src/template.rs`, `src/project/wire.rs`, `src/project/rpc.rs`, `src/project/api.rs` | Checked forms/HTML, strict wire decoding, explicit exports and safe error encoding |
| Durable and opaque primitives | `src/project/jobs.rs`, `src/project/workflows.rs`, `src/project/events.rs`, `src/project/files.rs`, `src/project/secrets.rs` | Fenced work, fresh authority, nominal streams/blobs and adapter-owned secrets |
| Artifacts and diagnostics | `src/project/artifact.rs`, `src/error.rs`, `src/diagnostic_help.rs`, `src/main.rs` | Rechecked deterministic artifacts, versioned contracts, CLI diagnostic explanations |

The reactive IR currently uses `Route(String, ...)`, `Event(String)`, and
text-based `Binding(String)` entries. The lowerer requires named click/submit
handlers and primarily `Text` value bindings. Existing single-file typed forms
and typed routes supply useful semantics, but do not already provide the proposed
reactive route/form/component APIs.

The existing relational request transaction and workflow fences are the starting
point for reliable actions. A new action abstraction must extend their guarantees;
it must not place a second incompatible transaction/retry engine around them.
Existing secret, blob, stream, query, and workflow protections remain required.

### Architectural decisions

1. Keep one semantic definition for shared language operations and generate or
   explicitly compare the Rust/browser implementations from that definition.
   Shared schema metadata is authoritative; the browser remains untrusted input.
2. Represent routes, form fields, event arguments, component outputs, and action
   policies in checked IR. Resolve declarations and effects before code generation.
   Do not encode these constructs as arbitrary JavaScript or application strings.
3. Extend checked internal URL construction. Dynamic URLs must originate from
   typed routes or another separately constrained URL type, never unrestricted
   interpolation into `href`, `action`, or `src`.
4. Distinguish editable form drafts from validated application values. An incomplete
   numeric input can exist as a bounded draft without becoming an invalid `Int`.
   Do not echo password/secret fields into errors, saved drafts, or logs.
5. Treat component outputs as declared typed messages with bounded dispatch and
   lifetime rules. They carry data, not executable closures or captured authority.
6. Limit transactional action replay to effect sets the runtime can commit with a
   receipt atomically. For external effects, use existing declared durable adapters
   where supported or expose an explicit non-replayable/uncertain result.
7. Introduce structured error categories and source spans at the beginning. Final
   wording, presentation, formatting and editor polish follow the working paths.
8. Preserve the interpreter architecture while measuring its actual costs. Native
   compilation is not a dependency of any checkpoint in this plan.

### Working sequence

Build breadth early, then deepen the connected paths. Compiler and runtime safety
checks accompany every stage; LP6 broadens and verifies them rather than enabling
previously permissive behavior. LP2 scaffolding may reject unsupported execution
explicitly, but a stub or accepted declaration that does nothing is never counted
as working behavior.

| Checkpoint | Deliverable | Depends on |
| --- | --- | --- |
| LP1 | Current contract, invariants and observable expectations | Current source/evidence review |
| LP2 | Checked compiler/runtime spine and a minimal connected application | LP1 |
| LP3 | Complete typed reactive routes and forms | LP2 |
| LP4 | Parameterized events, reusable components and accessible controls | LP2–LP3 |
| LP5 | Reliable actions, receipts and mutation reconciliation | LP2–LP4 |
| LP6 | Cross-boundary security and runtime hardening | LP3–LP5 |
| LP7 | Diagnostics, formatter, editor support and current guides | LP3–LP6; source-span work starts in LP2 |
| LP8 | Assembled language qualification and application readback | LP1–LP7 |

### LP1 — Establish the language contract

**Completed 27 October 2026.** Deliverables: [implemented specification](../docs/language-specification.md),
[profile/security/budget/drift matrices](../docs/language-profiles.md),
[LP2–LP8 scenarios](../verification/lp1/SCENARIOS.md), and
[source/evidence ledger](../verification/lp1/README.md). The unchanged runtime
passed 152 fresh automated observations and eight real-browser cases; coverage
gaps and historical evidence are explicitly separated in the ledger.

**Purpose:** Decide exactly what Diamond promises before enlarging its surface.

Work:

- Inventory implemented behavior across the single-file, project/server, shared,
  and browser profiles. Mark real intentional differences and accidental drift.
- Draft a canonical specification covering evaluation order, argument evaluation,
  matching, numeric boundaries, Unicode operations, errors, transaction poisoning,
  cancellation, current-authority checks and resource limits.
- Record the allowed sinks for every restricted type, including nested records,
  enums, options and lists. Separate identifiers from authorization-bearing state.
- Define language/wire/artifact compatibility rules for this expansion. An
  incompatible build must fail clearly; supporting deployed prototype versions
  is not required. Establish forward compatibility policy before the supported
  language release gains users.
- Write independent expectations for ordinary and hostile programs and a mapping
  from each promise to its implementation and real consumer interface.

**Exit gate:** AC-1 has a concrete specification outline and source map; every
subsequent checkpoint has observable positive and negative scenarios. Existing
implemented protections are recorded as prerequisites, not features to rebuild.

### LP2 — Put down the compiler and runtime broad strokes

**Completed:** See the [selected source contract](lp2-spine.md),
[implemented guide](../docs/language-spine.md),
[minimal app](../examples/language-spine/README.md), and
[real-interface evidence](../verification/lp2/README.md).
All five constructs reach checking, rendering/lowering, dispatch, and their real
consumer. Invalid types/effects/authority/restricted sinks and unavailable modes
reject. The Chrome/HTTP/PostgreSQL journey passed, including lost-response and
stale-output regressions. A runtime-owned single-use submission proof was added
after Chrome transparently retried a lost POST response. This prevents repeated
execution; LP5 subsequently supplied the durable receipt and reconciliation path.

**Purpose:** Connect the whole design early without polishing each subsystem first.

Work:

- Extend the AST/HIR/browser IR for named typed routes, declared form schemas,
  parameterized events, component outputs, and server-action declarations/results.
  Choose and document the concrete source spelling during this checkpoint.
- Add symbol resolution, finite type checks, source spans, effect propagation,
  restricted-value checks, and versioned artifact/wire metadata for those nodes.
- Carry checked declarations through server rendering, browser emission, request
  dispatch, and the reference evaluator. Reuse existing codec/policy machinery.
- Establish the action execution boundary and result categories before implementing
  durable deduplication. Until LP5, unavailable replay/recovery modes reject
  explicitly and no mutation is automatically repeated.
- Build one minimal connected Diamond app: a named detail route, one typed form,
  a row-specific event, a child output, and one protected database action.
- Preserve enough source structure and comments for the later formatter and editor
  work; do not force those tools to reconstruct lost source information.

**Exit gate:** The new source shapes check and reach their intended layers. The
minimal app performs its implemented journey through real HTTP/browser/database
interfaces. Invalid types, missing authority and unsupported modes fail explicitly.
No new node is accepted and silently ignored. Supports AC-2 and AC-3–AC-6.

### LP3 — Finish typed navigation and forms

**Completed:** See the [input contract](lp3-routes-forms.md),
[current guide](../docs/typed-inputs.md), [example](../examples/typed-inputs/README.md),
and [qualification ledger](../verification/lp3/README.md). Real native/static/
connected/browser paths agree on typed URLs, scalar/default/optional fields,
validation errors, corrected saves, and hostile rejection. Password/secret inputs
cannot enter ordinary schema drafts. Unit-enum defaults reject explicitly.

**Purpose:** Make application URLs and input schemas useful without manual parsing.

Work:

- Implement named routes with bounded nominal IDs, supported scalar path/query
  fields, optional/default query fields and a checked internal link builder.
- Define duplicate/missing/unknown query handling, percent-decoding boundaries,
  Unicode behavior, ambiguous-route rejection, reserved paths and canonical URLs.
- Make direct requests, client navigation, reload and browser history agree.
  Sensitive data and verified principal information never come from URL state.
- Add typed forms for bounded text, integers, Booleans, nominal IDs, unit enums,
  and supported optional fields. Reuse or reconcile single-file form decoding.
- Generate field parsing and pure bounded validation metadata from checked schemas.
  The server validates independently before invoking the action. Server-derived
  fields, undeclared fields and forged identity data remain unassignable.
- Preserve bounded invalid drafts and ordinary non-secret edits after validation
  errors; produce typed field errors and distinguish local errors from server denial.
- Exercise multibyte text, empty fields, invalid numeric intermediates, overflow,
  duplicate keys, overlong input, malicious URLs and a raw client bypassing the UI.

**Exit gate:** AC-3 and AC-4 pass. The minimal app deep-links to the correct record,
submits a valid typed form, displays field errors, and recovers from invalid input.
Equivalent browser/server decoders agree; URL construction cannot produce an
external destination or reinterpret untrusted text as executable markup.

### LP4 — Finish event arguments and reusable components

**Completed, 27 October 2026.** [Guide](../docs/components.md),
[source contract](lp4-components.md), and
[qualification ledger](../verification/lp4/README.md). Language 0.19.0,
artifact format 4, wire 6. Actual Forge/workspace journeys, native controls,
keyed focus/selection, stale outputs, identity clearing, rejection/recovery,
and independent boundary replays pass. Speech-based screen-reader observation
was unavailable; the ledger bounds accessibility and security conclusions.

**Purpose:** Let applications express direct interactions and reuse safe controls.

Work:

- Implement typed arguments on declared click/submit handlers, including values
  bound by keyed iteration. Reject signature mismatches before browser emission.
- Add declared component outputs and checked parent handlers. Specify ordering,
  recursion/dispatch budgets, cancellation, unmount and identity-change behavior.
- Preserve component identity, native focus, selection and user drafts through
  reorder, insertion, removal, validation errors and unrelated live updates.
- Supply reusable field/form primitives with generated unique IDs, associated
  labels/descriptions/errors, native keyboard behavior and accessible status.
  Require an accessible name when the compiler cannot safely derive one.
- Treat contextual content such as image descriptions as an explicit author input;
  Diamond can enforce its presence and association, not infer its meaning.
- Update a focused Forge journey so clicking a repository/issue card opens that
  exact resource and a child editor reports completion to its parent. Add the same
  pattern to the workspace or catalog application.

**Exit gate:** AC-5 passes, including stale child messages after reorder/removal,
duplicate instance identity, logout during interaction and nominal-ID mismatches.
Both example journeys work without a selection-and-copy convention or application
code manually constructing field IDs and error relationships.

### LP5 — Add reliable server actions and reconciliation

**Completed, 27 October 2026.** [Selected contract](lp5-actions.md),
[developer guide](../docs/actions.md), and
[real-interface evidence](../verification/lp5/). Language 0.20.0, artifact format
5, wire 7. The runtime owns invocation identity, transactional mutation/receipt
commit, encrypted bounded receipts, current-policy recovery, API/cookie binding,
server-side reload discovery, accessible recovery controls, and cleanup bounds.
The campaign covered concurrent servers, canonical inputs, business rollback,
HTTP/process/PostgreSQL commit-boundary loss, changed bindings/authority, revocation,
corruption/expiry/capacity, deleted and recreated parents, actual Chrome
double-click/reload/logout, and an independently authored oracle. Red-teaming found
and fixed an unrecoverable over-footprint abort, deleted-parent recovery denial,
a malformed-resource compiler panic, and duplicate action-binding ambiguity; their
before/final evidence is retained.

**Purpose:** Make safe mutation handling a language/runtime behavior.

Work:

- Finalize the typed action state model: idle, submitting, succeeded, validation
  rejected, access denied, confirmed aborted, and completion unknown. Keep pending
  transport state distinct from a durable business outcome.
- Have the runtime issue and track bounded invocation identities. Bind identities
  to the application, checked action version, principal/API restriction, declared
  parent scope and canonical validated input fingerprint. An ID grants no access.
- For eligible transactional actions, commit the mutation and receipt in the same
  database transaction. Concurrent duplicate submissions serialize to one outcome.
  Record confirmed application rejection consistently with its rollback semantics.
- Reconcile a lost response or lost commit acknowledgement by looking up the
  original identity. Do not turn transport cancellation into a rollback claim.
- Reauthorize receipt status/result access using current account, scopes and
  resource policies before returning any protected result. A stale cached result
  cannot bypass a subsequent revocation or expose another user's data.
- Reject identity reuse with changed input, action version, principal or scope.
  Bound receipt size, count, lifetime and cleanup. An expired identity must report
  expiration rather than silently becoming a fresh execution.
- Define reload recovery without saving secrets, form bodies, or authority-bearing
  values in browser storage. Identity changes clear private browser action state.
- Classify actions by effects. Direct HTTP, Git, or other external effects cannot
  gain automatic transactional replay. Reuse a supported transactional outbox or
  durable adapter only where its existing contract permits it; otherwise retain
  explicit uncertainty and reconciliation. No distributed exactly-once promise.
- Make the supported route, generated client, and API action paths share the same
  checked dispatcher. A parallel raw-RPC path must not bypass action restrictions.

**Exit gate:** AC-6–AC-8 pass for double-clicks, concurrent clients, response loss,
process interruption before/after commit, account changes, revoked access, corrupt
receipts, expired identities and changed payloads. Independently inspect database
state and external side effects. Retry/recovery never depends on an application
author remembering to manufacture a deduplication key.

### LP6 — Harden the security and runtime boundaries

**Completed:** 27 October 2026. The bounded evidence and remaining coverage gaps
are recorded in `verification/lp6/README.md`.

**Purpose:** Verify the enlarged language as one system and repair root causes.

Work:

- Review all new callers and sinks through checker, lowering, rendering, wire
  codecs, action dispatch, policies, native embedding, jobs and delayed callbacks.
- Attack nested restricted values, forged records/enums/handles, helper-mediated
  effects, shadowed symbols, nominal-ID confusion, hidden return values, raw API
  calls and malformed persisted state.
- Exercise authorization changes at defined transaction boundaries. Include API
  attenuation and resource changes between submission, execution and receipt read.
- Compare Rust/browser arithmetic, Unicode, enums, options, results, validation,
  error categories and budget exhaustion using independently stated expectations.
- Exercise execution depth/fuel, materialization/copy work, render limits, form and
  action sizes, request admission, connection cleanup, slow dependencies and
  cancellation. Check allocation before expensive expansion wherever feasible.
- Run prolonged create/update/navigate/unmount/reconnect sequences and inspect
  memory, open descriptors, owned tasks, subscriptions and pool recovery. Resource
  observations must distinguish the Diamond process from native child processes.
- Fix observed behavior, preserve minimized real failures, rebuild, and replay the
  failed path plus directly affected valid paths. Refactor shared enforcement
  rather than adding per-application exceptions or suppressing failures.

**Exit gate:** AC-2, AC-8 and AC-9 pass within declared budgets and concurrency
rules. Every confirmed relevant finding is fixed and replayed, or is explicitly
recorded as blocking completion. Unexplored cases are listed; a clean bounded run
does not become a claim of exhaustive security or exact allocator isolation.

### LP7 — Finish diagnostics, tools and current documentation

**Completed 27 October 2026.** See the [tool contract](lp7-tools.md),
[tooling guide](../docs/tooling.md), and [LP7 evidence](../verification/lp7/README.md).
The real CLI/LSP/browser scenarios pass, project diagnostics retain their module
source, and all current examples check on the recorded candidate.

**Purpose:** Make the completed language understandable and practical to maintain.

Work:

- Improve errors with exact source/module spans, expected/actual types, relevant
  effect chains, stable diagnostic categories and a useful suggested correction.
  Runtime redaction remains mandatory; diagnostics never print secret contents.
- Extend browser mappings to component expressions and event/action handlers.
  Preserve server source privacy and exclude unreachable/private source content.
- Implement a formatter with a check mode, deterministic output, preserved comments
  and string contents, and documented handling of syntactically invalid source.
- Add editor support for open-document diagnostics, definitions, references, hover
  types and context-sensitive completion using the actual compiler model. Bound
  work on incomplete/malformed documents and cancel obsolete analysis.
- Consolidate standard utilities and examples around validated values and typed
  errors. Add a utility only when the reference journeys reveal a repeated need;
  do not expand host authority merely to reduce a little application code.
- Publish the canonical specification and update the guides/catalog to point to
  it. Clearly label historical checkpoint descriptions and remove contradictory
  claims from the current recommended path.
- Measure checking, initial render, event dispatch and update costs on the real
  reference apps. Optimize measured bottlenecks without weakening type checks,
  policy evaluation, resource accounting or error behavior.

**Exit gate:** AC-1 and AC-10 pass. Formatting a representative program twice is
stable and preserves meaning/comments; edited files receive accurate diagnostics;
browser faults identify the Diamond handler; current examples compile and run
using the documented syntax. Earlier runtime gates still pass after optimization.

### LP8 — Qualify the assembled language

**Completed 27 October 2026.** See the [qualification contract](lp8-qualification.md)
and [assembled evidence ledger](../verification/lp8/README.md). The final candidate
passed the retained LP1–LP7 replays, generated valid/hostile corpus, native
consumer, real Forge/non-Git browser and PostgreSQL/worker journeys, and actual
Chromium, Firefox, and WebKit responsive/keyboard/accessibility checks. Named
coverage limits remain bounded rather than becoming universal security claims.

**Purpose:** Establish evidence for the final integrated source and artifacts.

Work:

- Freeze the specification and examples for the candidate, build it, and record
  source/artifact hashes for each observation. Rebuild and refresh affected
  evidence whenever the implementation changes.
- Drive Forge and a non-Git app through actual browser, CLI, HTTP, PostgreSQL and
  worker interfaces. Verify saved state independently and after a fresh process
  or client. Native consumers exercise the supported checked-program APIs.
- Cover deep links, typed forms, field errors, component outputs, mutation retries,
  identity changes, denied access, jobs/live updates, and recovery after failures.
- Run real browser journeys in Chromium, Firefox and WebKit, including relevant
  mobile/reflow and keyboard cases. Include a practical screen-reader journey for
  the generated field/status relationships; identify the exact combinations used.
- Replay the retained language/transport/authority failures and run bounded fresh
  generated/fuzz campaigns over parsing, checking, codecs and stateful actions.
  Generated valid programs must reach execution, not only exercise early rejection.
- Publish a source-to-guarantee coverage record naming actual callers, outcomes,
  recovery paths, budgets, seeds, minimized failures and unexercised boundaries.
  Keep earlier W/G/L results as historical evidence rather than relabeling them
  as observations of a later binary.

**Exit gate:** AC-1–AC-12 are supported by observations of the assembled candidate;
there are no unresolved confirmed findings in the promised scope. Ordinary
application journeys teach the safe primitives and the supported limitations are
explicit. This qualifies the language contract within the tested profile;
operational deployment acceptance belongs to the separate infrastructure work.

## Verification and checkpoint completion rules

Before implementing each checkpoint, write its observable scenarios and expected
results from the specification or an independent reference. Compiler rejection,
server enforcement and browser feedback are separate observations. A success
message or HTTP 200 alone does not establish that the intended state changed.

Use real programs and dependencies for acceptance. For mutations, compare actual
saved state and later readback; for security rejection, inspect the absence of
unintended effects; for cancellation, inspect cleanup and a following valid action.
Use generated cases to vary values, encodings, ownership, ordering, lifecycle and
concurrency rather than collecting large numbers of equivalent early rejections.

Record checkpoint completion alongside the exact final code exercised, retained
failure replays and explicit gaps. Perform the project's relevant Rust quality
checks when Rust implementation changes, but do not substitute their green status
for real language/application behavior. Pipeline integration is handled elsewhere.

## Open Questions

There are no unanswered scope questions blocking this plan. The user has selected
language/compiler/runtime work and excluded pipeline work.

Concrete LP2 declaration spelling, IR layout, version boundaries, and temporary
one-attempt submission semantics are recorded in [the spine decision](lp2-spine.md).
Durable receipt identity/storage/recovery is fixed by the
[LP5 contract](lp5-actions.md); `replay = "never"` retains LP2's result-less
single-attempt profile. LP1 distinguished profile differences from defects
before expansion. Later decisions cannot relax the requirements or exit gates.

## Out of Scope

- CI/CD workflows, hosted runners, packaging/distribution automation, release
  signing, provenance publishing and deployment-pipeline configuration.
- DNS, TLS termination, reverse-proxy/CDN rules, infrastructure provisioning,
  production monitoring installation and operator backup schedules.
- New SMTP/provider integrations, replicated storage and remote database/HA
  deployment work. Existing adapter contracts remain protected and regression
  inputs, but adding those services belongs to a separate workstream.
- Git product expansion or a general redesign of the Forge application. Changes to
  Forge here demonstrate and validate the language primitives.
- A native compiler rewrite, unrestricted JavaScript/native plugins, arbitrary
  shell access or new authority-bearing escape hatches.
- Automatic compatibility with abandoned prototype artifacts, a distributed
  exactly-once guarantee, or claims of universal security/WCAG conformance.

## Overall Definition of Done

A developer can build a substantial Diamond web application using checked routes,
typed forms, reusable components and reliable actions; understand an error at its
source; and rely on the documented enforcement and resource contracts. The same
program means the same thing in the supported server and browser profiles, and
the language's security behavior is backed by real executable evidence.

Completion of this document authorizes no implementation, commit, push or
deployment by itself. Each LP checkpoint can be requested and delivered as a
bounded increment with its own evidence and handoff.
