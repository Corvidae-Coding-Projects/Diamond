# LP2 connected language evidence

LP2 connects typed named paths, declared Text forms, parameterized events,
component outputs, and explicit database actions. The [source contract](../../docs/language-spine.md)
and [example](../../examples/language-spine/README.md) describe the supported
slice. This is bounded language qualification, not a production release or a
whole-codebase security audit. LP3–LP8 remain staged in the [plan](../../.design/language-production-readiness.md).

## Current candidate and real consumers

The candidate CLI SHA-256 is
`34f31487113e5d6b14e0aa8c214aca44be80a71c16add9eab77945c9b6d72262`.
It is language 0.17.0, manifest schema 1, artifact format 2, wire 4, API contract 1.
The app contract is
`616e93176c5402badea27b80b395a00437f096ba00cd1b4f483aa75735ddb754`.
These are working-tree observations; HEAD still identifies the prior committed
source. Source fingerprints and final quality commands are retained separately.

Final [quality.json](quality.json) records successful locked build, warning-free
all-target/all-feature Clippy, 10 existing unit tests, 38 doctests, format check,
and `git diff --check`, with fingerprints of the Rust/JS sources and manifests.
The tested binary remained unchanged through those final checks. The owned
browser fixture port was closed and its temporary root removed after testing.

| Receipt | Boundary actually exercised |
| --- | --- |
| [results.json](results.json) | Real CLI build, two native HTTP servers, PostgreSQL, independent SQL, fresh clients, roles/scopes, strict input rejection, rollback, submission proof races/expiry/binding/quotas, real lock-timeout abort and recovery |
| [browser.json](browser.json), [screenshot](browser.jpg) | Actual Chrome route/form/event/output/action journey, Unicode text, reload/history, delayed replies, dropped response, unmount and identity fences, later valid action |
| [browser fixture lifecycle](browser_fixture_lifecycle.json) | Owned fixture setup/backend observations and clean server shutdown; browser actions are recorded separately |
| [compiler.json](compiler.json) | Valid source/build/load; wrong nominal event/output data, missing effects, unsupported modes/fields, restricted nested sinks, exact source retention, private/public assets, old artifacts/tamper rejection; all eight Action constructors/matches actually run |
| [independent.json](independent.json) | Separate verifier: literal action/API binding named `prepare`, duplicate/concurrent proofs, real external Rust consumer without native grant, profile and secret-result rejection, current policy revocation, SQL/readback |
| [baseline.json](baseline.json) | LP1's pure/HTML/storage/artifact/HTTP sample replayed with explicitly selected new version expectations; historical LP1 receipt unchanged |
| [examples.json](examples.json) | Existing callable catalog, pure examples, real SQLite and HTTP helper examples, project checks, and local documentation links |
| [l1](l1.json), [l2](l2.json), [l3](l3.json), [l4](l4.json), [l5](l5.json), [l6](l6.json), [l7](l7.json) | 92 retained compiler observations. Two are source markers, not runtime execution. No historical external-adapter campaigns are relabeled current |

## Findings and fixes

1. **Public document navigation rejected.** A real initial Chrome request and an
   independent four-request comparison showed that cross-site navigation to a
   pure public root/named shell returned 400. The request guard now allows only
   GET/HEAD document navigation to checked public shells. Cross-site subresource
   and action requests remain denied. Both positive and negative HTTP requests
   and a real Chrome load passed on the candidate.
2. **Transparent POST retry could repeat a mutation.** The first implementation
   made no application retry, but Chrome resent a completed action after a proxy
   closed the response with zero bytes. [The retained counterexample](response_loss_before.json)
   records multiple dispatches; its idempotent title overwrite alone did not
   prove multiple business increments. The reference app now increments a saved
   revision. A native single-use proof binds checked inputs/current subject and
   is consumed before execution across servers. Concurrent/duplicate raw requests
   and a real dropped browser response now cause one saved increment, with
   Unknown on lost/repeated completion. Proofs store no result; LP5 still owns
   durable receipts and reconciliation.
3. **Focused source-review edge cases.** API proof replies initially omitted the
   API version; action bindings without the required relational/account profiles
   could check; a binding literally named `prepare` could collide with the
   preparation stage parser. These were corrected before independent runtime
   replay. The independent receipt distinguishes source review from executed
   final-candidate cases.

The initial compiler harness also contained invalid test programs (wrong helper
name, a shadowed binding, and unsupported property-default syntax) and some cases
failed at an earlier type boundary than intended. Those inputs were corrected to
reach the named checks. They were harness mistakes, not reported language defects.

## Source coverage of the delta

| Area | Paths reviewed / consumer |
| --- | --- |
| Syntax and qualification | `src/parser.rs`, `syntax.rs`, `project/resolve.rs`, `project/schemas.rs`: source declarations, locations, nominal schemas and target rules; actual check/build |
| Types, effects, restricted sinks | `checker.rs`, `security.rs`, `value.rs`, `action.rs`, `eval.rs`, `project/rpc.rs`, `api.rs`: complete types, declared budgets, action separation and reference matching; CLI/native/HTTP |
| Browser checking and lowering | `project/lower.rs`, `lower/interactions.rs`, `lower/asynchronous.rs`, `lower/reach.rs`: signatures, form control inventory, route URL types, required parent bindings; compiler corpus |
| Browser lifecycle | `project/runtime.js`, `interactions.js`, `browser.rs`: postcommit output delivery, generation/binding/auth fences, pending/uncertain outcomes, route lifetime; real Chrome journey plus source review of chain budget |
| Native execution | `project/actions.rs`, `actions/tickets.rs`, `connected.rs`, `connected/profiles.rs`, `storage.rs`, `server.rs`: shared proof control, fresh authorization, request transaction, rollback/abort/unknown distinction, document navigation guard; real two-server/PG/fault interfaces |
| Rendering and publication | `project/render.rs`, `assets.rs`, `artifact.rs`, `mod.rs`, `template.rs`: typed href/outlet public shells, exact source retention/rechecking, version and asset boundaries; SSR, browser reload, old/tampered artifacts, public canary inspection |

This records targeted delta coverage. It does not imply that unrelated Git,
delivery, OAuth-provider, mail, job, or deployment implementation was re-audited.

## Reproduce

From the repository root, with PostgreSQL tools available:

```sh
cargo build --locked
python3 verification/lp2_compiler.py
python3 verification/lp2_spine.py
python3 verification/lp1_contract.py --language-version 0.17.0 \
  --format-version 2 --wire-version 4 --report .diamond/lp2-baseline.json
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-features
cargo fmt --check
```

For the browser workflow use `verification/lp2_spine.py --serve`. It serves actual
Diamond output at http://127.0.0.1:39171/ and prints its synthetic login. Stdin
`arm` holds the next completed rename reply; `observe` reads actual SQL; `drop`
closes the reply with no bytes; `release` delivers it; `stop` removes owned
fixture processes/data. Do not call the gate a business-logic mock: it only
forwards real request/response bytes. The detailed UI action order and observed
values are in browser.json. Six explicit saves produced five committed revisions
and one declared rollback; seven forwarded execution requests include a transport
repeat. No persisted revision was repeated by that transport retry.

## Explicit remaining limits

- LP2 paths are canonical ASCII nominal-ID paths; queries/wider scalars and wider
  form fields belong to LP3. Component ergonomics/accessibility belong to LP4.
- Proof consumption and mutation are separate transactions. A lost attempt after
  consumption can have no mutation and no recoverable result. There is no
  exactly-once completion guarantee, durable receipt, recovery API, or external
  effect action mode. LP5 must close that gap without weakening duplicate safety.
- Browser tests use one Chrome environment, one synthetic app, and controlled
  response delays/loss. They do not establish cross-browser conformance, arbitrary
  commit-acknowledgement recovery, prolonged cleanup, or exhaustive output-loop
  behavior. Chain limits are source-reviewed, not stress-qualified here.
- Existing source/manifest grants and business policies remain required. The
  runtime owns enforcement and submission proof mechanics; it cannot choose the
  application's intended membership policy.
- Original source/comments and node locations survive, but formatter/editor
  behavior and per-event browser maps remain LP7. Pipeline work is outside scope.
