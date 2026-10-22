# Diamond implementation checkpoints

## Checkpoint one — broad implementation

**Status: complete, 21 October 2026.** Package `diamond-lang` 0.1.0,
Rust/Cargo 1.96.0, Linux. The language specification remains v0.3.

Acceptance: every major subsystem has an initial implementation, the whole
project builds, and unsupported features fail explicitly. The compiler/runtime
boundaries must remain suitable for the subsequent integration passes.

### Implemented layers

| Layer | Source | Initial behavior |
| --- | --- | --- |
| Syntax | `src/lexer.rs`, `src/parser.rs`, `src/syntax.rs` | Bounded indentation-aware parsing, declarations, expressions, source locations |
| Checking | `src/checker.rs`, `src/hir.rs` | Local types, declared records/IDs, closed-call-graph effect inference, policy coverage, route compatibility, sealed executable programs |
| Values and authority | `src/value.rs`, `src/authority.rs` | Owned immutable values, borrowed native scopes, explicit host grants; authority is not a language value |
| Evaluation | `src/eval.rs` | Functions, records, checked arithmetic, Result propagation, typed HTML, structured repository calls, transaction scopes |
| Policies and SQLite | `src/policy.rs`, `src/data.rs` | Restricted predicates, reference evaluation and scalar SQL lowering, bound CRUD, pre/post-state updates, rollback and conservative indeterminate outcomes |
| Web input/output | `src/template.rs`, `src/forms.rs`, `src/web.rs` | Allowlisted typed templates, strict bounded forms, typed paths, loopback HTTP, safe response mapping |
| Sessions | `src/auth.rs` | Trusted-host issuance, expiration, hashed token storage, CSRF binding, redacted token debugging, revocation |
| Entry points | `src/lib.rs`, `src/main.rs` | Native library plus `check`, `run`, and explicit `db-init` commands |

### Observed verification

| Check | Result |
| --- | --- |
| `cargo build --locked` | PASS |
| `cargo fmt --check` | PASS |
| `cargo clippy --locked --all-targets --all-features -- -D warnings` | PASS, no lint allowances |
| `cargo test --locked` | PASS, five doctests; no generated unit-test suite |
| `RUSTDOCFLAGS='-D warnings' cargo doc --locked --no-deps` | PASS |
| `cargo run --locked --example embedding` | PASS: native evaluation, session/CSRF binding, redaction, and revocation |
| `python3 verification/checkpoint_one.py` | PASS: bounded real CLI/HTTP/SQLite campaign, seed 858637 |

The campaign checked four supplied Diamond examples, 36 CLI evaluations
(including 32 generated arithmetic/function cases), 22 compile rejections,
two runtime arithmetic rejections, ten independently observed SQLite state
scenarios, and 29 HTTP requests. These are observations of this subset, not a
coverage percentage or an exhaustive conformance claim.

SQLite checks independently inspected actual rows through Python's SQLite
driver. They covered persistent create/reopen/read/update/delete, literal SQL
injection payload storage, failed-transaction rollback, denied-update rollback,
indistinguishable missing/hidden record failures, and incompatible-schema
rejection. Each CLI invocation used a fresh process and isolated temporary data.

HTTP checks covered rendered text/links, HEAD, typed parameters, private-route
denial, invalid cookies, malformed/duplicate/overlong/unknown input fields,
Unicode and markup payloads, wrong Host/Origin, untrusted proxy headers, upgrades,
and duplicate headers. DOM-text expectations accept equivalent HTML character
references; network checks consume complete status lines rather than assuming a
single TCP read contains an entire line.

A separate real Chrome session opened the actual development server, displayed
the literal heading `Diamond <checkpoint one>`, clicked `A typed route`, and
observed `Note demo` at `/notes/demo`. This verifies that rendering/navigation
path only. The verification tab/server and disposable campaign databases were
closed or removed afterward.

No authored unsafe Rust or lint suppressions were introduced. Miri was not run
because this implementation forbids authored unsafe code. This is the first
public library surface at 0.1.0; the declared minimum toolchain is Rust 1.96,
which was used for the checks above.

### Boundaries at checkpoint one

Browser login issuance, session-backed POST-form rendering/submission, and the
complete message-board user journey remain for checkpoint two and subsequent
passes. The session API has been exercised through a native consumer; successful
authenticated browser requests have not been established. WebSockets, general
queries, concurrent tasks, general ADTs/matching, and the other exclusions in
README.md remain unsupported.

Commit-acknowledgment loss, low-level SQLite I/O faults, process-crash recovery,
all supported policy combinations, hostile-dependency confinement, full browser
XSS behavior, formal proofs, and production deployment are not verified by this
campaign. The conservative unknown-outcome code is an initial contract, not
evidence that all failure schedules have been tested.

Checkpoint two begins by connecting a complete browser-authenticated application
journey through these existing layers. Completion of checkpoint one does not
establish the full specification's advertised security guarantees.

## Checkpoint two — connected browser application

**Status: complete, 21 October 2026.** Package `diamond-lang` 0.2.0,
Rust/Cargo 1.96.0, Linux. The language specification remains v0.3.

Acceptance: run the supplied message-board program through a real browser login,
checked forms and evaluator, policy-aware SQLite writes, typed redirect and read,
then restart against the same data and retrieve the saved post after signing in.

### Connected behavior

- Native `user-add` provisions private local accounts with independent random
  salts and Argon2id hashes. It prompts without echo by default and accepts an
  explicit stdin mode for automation. Accounts cannot be replaced by this command.
- `--auth` grants a separate account database to the runtime; application data
  authority does not expose it. A session declaration is required. Public native
  APIs add `add_local_user` and `CheckedProgram::serve_with_auth`; earlier APIs
  remain available. The package minor version increases to 0.2.0; MSRV stays 1.96.
- Runtime-owned sign-in rotates the session token, sign-out revokes it, and cookies
  carry Secure, HttpOnly, SameSite Strict, host-only, and path restrictions.
  CSRF tokens are supplied automatically to rendered POST forms and checked
  before mutation handlers. Request/response debug output redacts sensitive data.
- The Diamond example contains the home form and business handlers. It parses
  the checked form record, derives the author from the verified principal,
  commits through the existing adapter/policies, and redirects to a typed URL.
  Post detail links back to the home form. Authorship and unpublished visibility
  remain enforced by the declared policies.

### Observed verification

| Check | Result |
| --- | --- |
| `cargo build --locked` | PASS |
| `cargo fmt --check` | PASS |
| `cargo clippy --locked --all-targets --all-features -- -D warnings` | PASS, no lint allowances |
| `cargo test --locked` | PASS, seven doctests; no generated unit-test suite |
| `RUSTDOCFLAGS='-D warnings' cargo doc --locked --no-deps` | PASS |
| `cargo run --locked --example embedding` | PASS |
| `python3 verification/checkpoint_one.py` | PASS, original bounded campaign and counts above |
| `python3 verification/checkpoint_two.py` | PASS, 56 real HTTP requests, seed 858626 |
| Actual Chrome forms and restart readback | PASS |

The checkpoint-two contract is recorded in `verification/checkpoint_two.md`.
The driver provisions two distinct users, independently inspects the hash records
and file mode, checks duplicate provisioning leaves them unchanged, and drives
login/create/read/logout/restart through the actual binary. Three generated post
journeys vary SQL-looking text, Unicode/markup, and the title-length boundary.
Twelve rejected mutations check missing/wrong/cross-session/stale CSRF, forged
authorship, invalid field bounds, hostile Origin, conflicting tokens, duplicate
cookies, and another user's delete. Python's SQLite driver independently checks
rows and unchanged state. Wrong-password and nonexistent-user responses match;
pre-login, revoked, and pre-restart cookies cannot retain authenticated access.
The default interactive `user-add` path was also operated in a real terminal:
the password was not echoed, the command succeeded, and an independent SQLite
read found the expected account hash in a mode-0600 file.

In Chrome, a disposable local account signed in from the runtime page, filled
the Diamond-authored form, and submitted **Create post**. The browser followed
the redirect and displayed `Browser post <checkpoint two>` and the literal body
`Saved from Chrome. Unicode: café 雪. <script> stays text.` An independent SQLite
read confirmed the author, exact strings, and unpublished state. The server then
restarted against the same files. The home page required authentication again;
after signing in, the original detail URL displayed the original post. Browser
**Sign out** also returned to the authentication-required page. This establishes
localhost cookie acceptance and actual form submission, beyond the HTTP driver's
explicit cookie transport. `verification/checkpoint_two_browser.jpg` preserves
the final persisted-post view. Test servers, tabs, and disposable databases were
removed after verification.

No authored unsafe Rust or lint suppressions were introduced. Miri was not run
because authored unsafe code is forbidden. The checks above exercise this local
subset; they do not establish exhaustive conformance or the design's full
security guarantees.

### Boundaries at checkpoint two

This stops at the connected application journey. General queries/list rendering,
ADTs/matching, concurrency, module budgets, and the other language exclusions
remain for later passes. Authentication is a Unix local-account development
provider with process-local sessions and no registration, reset, or role
management. Restart persistence applies to accounts and application records,
with a new login required. TLS termination, production serving, distributed rate
limits/session storage, hostile-dependency confinement, all policy combinations,
and the fault/recovery schedules listed at checkpoint one remain unverified.

## Checkpoint three — MVP feature completeness

**Status: complete, 21 October 2026.** Package `diamond-lang` 0.3.0,
Rust/Cargo 1.96.0, Linux. The language specification remains v0.3.

Acceptance: the message board's complete intended journey is written in Diamond,
using functions, records, basic variants/matching/results, typed routes and forms,
validation and redirects, the auth lifecycle, CRUD policies, bounded selections,
transactions, and ordinary error handling. The accepted six-pass plan is now
preserved in `IMPLEMENTATION_PLAN.md`.

### Expanded surface

- Non-generic nominal enums support unique named constructors and positional
  payloads. Matches over enums, Bool, Option, and Result require exhaustive,
  compatible branches and checked bindings. Immutable lists, `len`, and bounded
  `for` expressions supply typed HTML fragments for post lists.
- Expected form/repository failures can be matched as Result values. DataError
  preserves its safe failure class through `?`; `error_message` supplies safe
  application feedback. `bad_request` accepts typed HTML and returns status 400.
  Internal failures and indeterminate transaction outcomes still fail the runtime.
- `select` supports named scalar equality filters with bound values, required
  limits of 1–100, offsets of 0–10,000, and stable binary ID order. Read policies
  restrict the relation before ordering/pagination, with reference evaluation of
  every returned row. Optional filters have null-safe equality semantics.
- Transaction scopes roll back returned Err values and cannot commit after a
  caught repository failure. Nested transactions and nested early returns remain
  explicit exclusions. Existing/proposed state and returned records still pass
  the adapter's existing action/read policies.
- The Diamond message board lists a user's posts and public published posts,
  creates drafts, renders detail links, edits title/body, publishes/unpublishes,
  deletes, gives validation/retry feedback, and signs out. Trusted context fields
  supply identity; owner controls are presentation, and repository policies
  independently enforce mutations. Lists currently show at most 100 records.

### Observed verification

| Check | Result |
| --- | --- |
| `cargo build --locked` | PASS |
| `cargo fmt --check` | PASS |
| `cargo clippy --locked --all-targets --all-features -- -D warnings` | PASS, no lint allowances |
| `cargo test --locked` | PASS, eight doctests; no generated unit-test suite |
| `RUSTDOCFLAGS='-D warnings' cargo doc --locked --no-deps` | PASS |
| `cargo run --locked --example embedding` | PASS, including actual session/CSRF expiration |
| `python3 verification/checkpoint_one.py` | PASS: five examples, 36 evaluations, 22 compile rejections, two runtime rejections, ten SQLite checks, 29 HTTP requests |
| `python3 verification/checkpoint_two.py` | PASS: original authenticated campaign, 56 HTTP requests |
| `python3 verification/checkpoint_three.py` | PASS: seed 858627, counts below |
| Actual Chrome complete message-board journey | PASS |

The earlier enum-rejection fixture was replaced with an unsupported generic-enum
case because basic enums are now supported. The original negative requirement is
no longer applicable; the campaign still rejects unsupported type syntax and all
its other prohibited paths. The new example increases example checks to five.

The third campaign exercised eight direct language journeys and 24 generated
matching/iteration journeys, 15 language compile rejections, 24 independently
modeled authorized selections, four selection compile rejections, four runtime
limit/offset rejections, two nullable authorized selections, two transaction
rollback replays, and one successful commit readback. Selection inputs combine
hidden rows before visible ones, pagination, SQL-looking bound text, Unicode, and
optional values. Expected authorized relations and output values are computed in
Python independently of the implementation; actual SQLite rows are also inspected.

Its 38-request application journey used two distinct accounts and an anonymous
visitor for create/list/read/edit/publish/unpublish/delete/logout/restart. Rejected
edits left exact rows unchanged; three cross-user edit/publication/delete attempts
failed without writes. A published post survived restart, and deletion removed it
from storage and the public list. These bounded observations do not constitute
full language or security conformance.

The separate Chrome journey used the actual current binary and disposable local
accounts/databases. An empty form displayed the Diamond-authored validation page
and saved no rows. Retry created a draft containing literal markup and Unicode;
the generated list link opened it. The edit form displayed the original values,
saved new text, and followed the typed detail redirect. Publish changed both the
visible state and the public list. After a real restart, an anonymous browser
could read the saved published post without owner controls. The private home
required a fresh login. Unpublish removed the post from the public list, delete
returned to `No posts yet.`, and sign-out restored authentication-required state.
Independent SQLite reads confirmed exact text, authorship, publication state,
persistence, and final deletion. Screenshots are retained in
`verification/checkpoint_three_published.jpg` and
`verification/checkpoint_three_deleted.jpg`.

One new compiler/runtime mismatch was reproduced through the CLI: a function
parameter named `request` was checked as an ordinary record, but evaluation
substituted the runtime principal for its `authenticated` field. The initial
program printed `false` where the record required `true`. Context dispatch now
honors local shadowing, and the saved concrete replay prints `true`. This fixture
and the two transaction rollback cases are retained in
`verification/regressions/` and replayed by the third campaign. Temporary servers,
tabs, credentials, and databases were removed after the checks.

No authored unsafe Rust or lint suppressions were introduced. Miri was not run
because authored unsafe code remains forbidden. This minor version adds language
features and preserves native API signatures; MSRV remains Rust 1.96.

### Remaining boundaries

At this checkpoint, four's enforcement review, five's broader failure and
recovery campaign, and six's fine details remained pending. Generic
enums, nested/or/literal patterns, general Result error types, closures/callbacks,
mutation/concurrency, module budgets, general query syntax, joins/aggregates/bulk
operations, migrations, native/browser compilation, production auth/hosting,
and the other README exclusions remain unsupported. No production-readiness,
whole-codebase security, exhaustive policy coverage, low-level I/O/crash recovery,
or hostile-dependency confinement claim follows from this feature checkpoint.

## Four — enforcement review

**Status: complete, 21 October 2026.** Package `diamond-lang` 0.4.0,
Rust/Cargo 1.96.0, Linux. The language design remains v0.3 and is unchanged.

The supported implementation was reviewed from source input through checking,
scope/policy enforcement, persistence, failure recovery, and output. All 20
authored Rust source files (6,783 lines) were read, along with their native and
Diamond consumers. The explicit inspection/caller/evidence record is
[checkpoint_four_coverage.md](verification/checkpoint_four_coverage.md).
This record distinguishes source review from behavioral and branch coverage.

### Repairs and resulting behavior

- Transaction-opening behavior propagates through the closed call graph,
  including cycles. The checker rejects direct/transitive nested transactions
  and transaction blocks without a source database declaration. Valid sequential,
  recursive-but-nonnested, read-only and mutating transactions remain usable.
- SQLite and typed form fields reject unsupported shapes during checking.
  Reserved CSRF and server-derived form fields, derived generated IDs, and
  case-insensitive persistent column collisions also fail before execution.
- Explicit HEAD routes are selected before GET fallback, so declaration order
  cannot change their public/private boundary. HTTP startup checks the source
  database declaration before opening the supplied path. New anonymous cookies
  are attached to sanitized error responses, permitting session reuse.
- All stored nominal IDs satisfy the same opaque shape as constructed/form/path
  IDs, including optional fields. Selection validates the bounded full relation
  in the same read snapshot before policy filtering and pagination. Malformed
  hidden or skipped rows fail the entire operation. The prototype accepts up to
  10,000 rows per validated relation and fails larger scans with a resource error;
  stored text is capped at 1 MiB before copying into managed values.
- Unknown HTTP transaction outcomes preserve the opaque operation ID and the
  no-automatic-retry instruction. The existing commit/abort/unknown classification
  and affected-connection discard were verified with actual SQLite faults and
  preserved.

Pre-edit observations, fix categories and compatibility decisions are in
[checkpoint_four_findings.md](verification/checkpoint_four_findings.md). Source
programs relying on excluded shapes now fail earlier. Native API signatures,
dependencies and the Rust1.96 minimum are unchanged. No authored unsafe Rust or
lint allowances were introduced.

### Final observed checks

`python3 verification/checkpoint_four.py --full` passed all 13 commands on one
stable executable. The replay fingerprints its inputs before and after execution;
the final receipt is [checkpoint_four_results.json](verification/checkpoint_four_results.json).

| Check | Final observation |
| --- | --- |
| Formatting, locked build, strict all-target/all-feature Clippy | PASS |
| `cargo test --locked` | PASS, eight doctests; no generated unit tests |
| Rustdoc with warnings denied | PASS |
| Real native embedding | PASS: evaluation, session issue/CSRF/redaction/revoke/expiry |
| Checkpoints one, two and three | PASS, original counts and bounded journeys retained |
| Compiler enforcement | PASS, 395 actual CLI invocations, seed858628 |
| Policy enforcement | PASS, 534 independent scalar-operation comparisons, 624 HTTP requests, seed858628 |
| HTTP regressions | PASS: private HEAD401/public HEAD200; no undeclared database created; error cookie reused |
| SQLite transaction faults | PASS, 24 actual CLI/HTTP cases, 40 HTTP requests, seed858628 |
| Separate actual Chrome complete journey | PASS, described below |

Compiler cases include 20 expression contexts with read/write and method budgets,
recursive transitive effects, 32 transaction graph checks, scope escape and output
sink rejections, valid ordinary values, explicit host denial, and existing examples.
The checkpoint-three early-return fixture now includes a database declaration so
it continues isolating its D900 rejection after the new prerequisite check.

The policy campaign states three independent business contracts and checks get,
select, create, update and delete through actual HTTP. An independent SQLite
connection verifies exact state after every mutation, including 14 cases where
the action permits a mutation but read permission on its returned record denies
it and leaves storage unchanged. A direct opaque-ID delete works without a read
grant. Hidden/missing records have the same external denial; policy, equality,
limit, offset and empty-output masks cannot hide malformed data. Caught invalid
selection errors roll back earlier writes; a successful selection does not commit
an enclosing transaction early. Sanitized stored-data failures and subsequent
same-process read recovery are also observed.

The transaction matrix crosses CLI/HTTP, implicit/explicit transactions,
success/COMMIT contention/acknowledgment loss, and two text payloads including
SQL-looking markup and a 120-character Unicode boundary. A separate SQLite
connection holds a real SHARED read lock: rejected COMMIT plus confirmed rollback
leaves zero rows, removes the journal, and permits another write. A narrowly
scoped test-only Linux shim performs actual journal deletion and then returns EIO:
Diamond reports unknown with operation ID, exactly one committed row is independently
visible, further database access is denied on that connection, and a fresh process
reads the row. Full observations are in
[checkpoint_four_transactions.json](verification/checkpoint_four_transactions.json).
This follows SQLite's documented DELETE-journal commit point; it does not emulate
power loss or establish physical durability.

The before-repair full matrix in
[checkpoint_four_transactions_before.json](verification/checkpoint_four_transactions_before.json)
ran an intermediate pre-HTTP-repair executable, explicitly fingerprinted there;
its two failures are missing HTTP operation IDs. The original 0.3.0 raw fault
observations independently established the same missing-ID defect. The receipts
do not conflate those two executable fingerprints.

### Current Chrome observation

The same final build served isolated accounts/data on localhost. Chrome signed
in with its real cookie jar, rejected an empty form without saving rows, created
a draft with literal script-looking text and Unicode, edited it through the
prefilled form and typed redirect, and published it. The rendered markup stayed
literal. A server restart retained the published record; an anonymous browser
could read it without owner controls, while the private home required a new login.
The account survived restart. After login, unpublish removed the record from the
public list, deletion returned `No posts yet.`, and logout restored
`Authentication required`. Independent SQLite reads checked exact title/body,
authorship, publication state, persistence and final deletion. The observed
published view is [checkpoint_four_browser.jpg](verification/checkpoint_four_browser.jpg).
All disposable browser tabs, processes, accounts and databases were removed.

### Remaining work

Checkpoint five's broader hostile-input, resource-exhaustion, cancellation and
recovery campaign and checkpoint six's fine details remain pending. The existing
README exclusions still apply. SQLite/native dependencies and the trusted host
were not audited; positive application role grants are not provided by the local
account provider; there is no hard aggregate heap quota. The bounded fault checks
do not establish all-fault or cross-platform durability. This is completion of
the supported MVP enforcement pass, not a production-readiness or security proof.

## Checkpoint five — broad failure and recovery evidence

**Status: complete, 21 October 2026.** Package `diamond-lang` 0.5.0,
Rust/Cargo 1.96.0, Linux. The language design remains v0.3 and is unchanged.
The final executable SHA-256 is
`b8ae4f208469c846114a92bd5b0229954bfe8f08f8fd3467d64d7928db1e1afc`.

Acceptance: exercise varied real programs and user sequences through the CLI,
native library, loopback HTTP server, Chrome and SQLite; include malformed input,
hostile text, authority failures, expiry, transaction failures, process/client
cancellation and recovery. Save every confirmed failure before repair and retain
positive and negative evidence for the advertised interpreted MVP.

### Repairs and resulting behavior

- Managed values now have a maximum depth of 64, a 4 MiB weighted per-value
  limit, and 64 MiB of cumulative value materialization/copy work per evaluation.
  Runtime copies share the budget; SQLite cells are inspected before owned string
  allocation, and oversized mutation return values fail before commit. The saved
  baseline program previously aborted under a child-only 512 MiB address-space
  ceiling; it now returns an ordinary uncatchable resource-limit error. Explicit
  transactions roll back and the same server remains usable.
- Invalid values for supported `Id` and `Option<Id>` form fields now produce an
  input failure and reach the Diamond program's typed validation path. They no
  longer fall through to the unsupported-feature response. Storage remains
  unchanged in both cases.
- Process-local sessions and CSRF validity now use monotonic elapsed deadlines.
  A backward wall-clock jump cannot revive an expired session, and a forward
  jump cannot prematurely expire a live one.
- Host and Origin validation still occurs before dispatch, after the complete
  bounded request body is consumed. This prevents unread request data from
  erasing a rejection response. At the 65,536-byte body boundary, the server
  drains at most 1 KiB of an oversize body under the existing five-second
  deadline before returning 413. Larger or incomplete bodies remain bounded and
  are not promised a complete response.

The exact pre-repair observations, fix categories and evidence boundaries are in
[checkpoint_five_findings.md](verification/checkpoint_five_findings.md). Minimal
language and transport replays remain under `verification/regressions/cp5_*`.
Public native signatures, dependencies, the Rust 1.96 minimum, and the no-unsafe
posture are unchanged.

### Final observed checks

`python3 verification/checkpoint_five.py --full` passed all 22 commands on one
stable final executable. The collector fingerprints every source, example,
verification input, manifest, lock file, design, README and checkpoint contract
before and after execution. The complete outputs are in
[checkpoint_five_results.json](verification/checkpoint_five_results.json).

| Check | Final observation |
| --- | --- |
| Cargo and standalone-consumer formatting | PASS |
| Locked build and strict all-target/all-feature Clippy | PASS, no lint allowances |
| `cargo test --locked` | PASS, eight doctests; no generated unit-test suite |
| Rustdoc with warnings denied and native embedding | PASS |
| Checkpoints one through three | PASS, original bounded campaigns retained |
| Checkpoint-four compiler/web/policy/transaction campaigns | PASS, including 395 CLI compiler invocations, 624 policy HTTP requests and 24 SQLite fault cases |
| Language exploration | PASS, seeds 858629 and 858630; 125 and 124 cases plus two native lifecycle runs |
| Managed-value enforcement | PASS, 43 CLI invocations, 10 SQLite checks and 8 HTTP requests |
| Generated board workflows | PASS, 288 shuffled actions, 300 independent state/list checks and 1,494 HTTP requests across two seeds |
| Transport/forms/accounts | PASS, 322 HTTP requests including the actual one-minute rate-window recovery |
| Process/client cancellation | PASS, 20 controlled journal-boundary cases |
| Forward/backward wall-clock faults | PASS, active faults independently observed |
| Actual Chrome failure/retry/logout journey | PASS, described below |

The language campaign uses an independent Python arithmetic and state oracle,
fresh child processes, and 512 MiB/5 CPU-second/no-core bounds. It covers checked
arithmetic, required Boolean evaluation, records, enums, Option/Result, matching,
lists, iteration, typed HTML/URLs, Unicode, structured malformed input, declared
resource limits and the saved record-expansion regression. The native consumer
checks invalid principals, lifetime bounds, token families, 4,096-session
capacity/reclamation, real expiry and typed route URLs.

The generated board campaign drives two accounts through login, create, read,
edit, publish, delete, logout and restart in shuffled orders. Its model checks
exact SQLite rows and public/owner lists after every action, including stale
sessions, foreign IDs, cross-session CSRF, unauthenticated mutations, validation
boundaries, SQL-looking strings, markup and Unicode. The transport campaign adds
framing, fragmentation, header/body boundaries, account-file safeguards,
corrupted bounded password hashes, typed forms, pipelined streams, partial
requests, session rotation and the real login-rate window.

The cancellation campaign pauses actual DELETE-journal removal and sends SIGKILL
before or after that point across CLI/HTTP, implicit/explicit transactions and
two payload families. Before deletion, a fresh Diamond process and independent
SQLite read see the pre-transaction state; after deletion they see the complete
one- or two-row commit. Losing the HTTP client may still leave one complete
commit, so callers must inspect state instead of blindly retrying. This is
process-crash evidence, not a power-loss or physical-durability result. The full
receipt is [checkpoint_five_cancellation.json](verification/checkpoint_five_cancellation.json).

### Current Chrome observation

The same final build served a disposable account and database on localhost.
Chrome filled a create form, the server restarted, and submission with the stale
session was rejected without a row. A new login and empty submission displayed
the Diamond-authored validation page, again with zero rows. The retry created
exactly one draft titled `Browser checkpoint five recovery`, with literal
script-looking text and Unicode `café 雪`; Chrome rendered the markup as text.
An independent SQLite query matched the opaque ID, author, title, body and draft
state. Sign-out restored `Authentication required`. The observed saved view is
[checkpoint_five_browser.png](verification/checkpoint_five_browser.png).
The tab, server, account and application database were removed after verification.

### Boundary at checkpoint five

At checkpoint five, diagnostic spans and wording, syntax consistency, parser
edges, startup/install experience, documentation organization and measured
performance remained pending for checkpoint six. The existing README exclusions
still apply. These
bounded campaigns do not establish exhaustive language or policy conformance,
an exact allocator/heap quota, hostile-dependency confinement, all SQLite fault
schedules, cross-platform clock behavior, power-loss durability, production HTTP
serving, production authentication or production readiness.

## Checkpoint six — final interpreted MVP polish

**Status: complete, 21 October 2026.** Package `diamond-lang` 0.6.0,
Rust/Cargo 1.96.0, Linux. The final executable SHA-256 is
`fae37713cf0ca82c26928bc5b2fcefbac8d35965cefad8cba14c60fa7afeda69`.
The architectural design remains a proposal and is unchanged.

Acceptance: complete path-bearing diagnostics, close syntax and parser edge
cases, qualify startup and a clean installation, organize the user documentation,
measure the prototype, and replay every earlier automated checkpoint on one
fingerprinted final binary. The fixed contract is
[checkpoint_six.md](verification/checkpoint_six.md); pre-edit observations and
fix decisions are in
[checkpoint_six_findings.md](verification/checkpoint_six_findings.md).

### Resulting behavior

- Compiler failures render `error[CODE]`, a sanitized path, one-based Unicode
  line and column, a bounded source excerpt, and a caret. The public immutable
  `Location` exposes `line()` and `column()`. `diamond explain CODE` covers every
  emitted category and accepts lowercase code spelling at the CLI boundary.
- Empty/comment-only source and malformed integer separators now fail. Newline
  spans point to the physical end of line; mismatched HTML closing tags point to
  their opening `<`; leading whitespace in multiline interpolation preserves its
  original location; tabs inside templates are rejected consistently. Type,
  route, static-URL, and ID-literal checker paths no longer produce synthetic
  `0:0` locations.
- Database-backed routes fail startup when `--database` is absent. Listener bind
  validation precedes opening account or application stores, so an occupied port
  cannot create those requested files. Non-loopback serving, overlapping stores,
  missing host grants, and incompatible startup inputs continue to fail closed.
- A clean `cargo install --locked --path . --root ROOT` produced an independent
  release executable. It checked every `.dia` example and ran `hello.dia` outside
  the checkout. The README now leads from installation to that result and links
  the focused language, diagnostic, and ordered-example guides.

### Final observed checks

`python3 verification/checkpoint_six.py --full` passed its final quality and
real-interface matrix while fingerprinting all source, examples, contracts,
guides, verifiers, and saved regressions. It includes the complete 22-command
checkpoint-five replay, which itself retains checkpoints one through four and
the broad generated, hostile-input, transaction, transport, cancellation,
recovery and clock campaigns. The machine-readable receipt is
[checkpoint_six_results.json](verification/checkpoint_six_results.json).

| Check | Final observation |
| --- | --- |
| Formatting, locked build, strict Clippy | PASS, all targets/features, warnings denied, no lint suppression |
| `cargo test --locked` and rustdoc | PASS, eight doctests, documentation warnings denied |
| Native embedding | PASS, including public diagnostic spans and session lifecycle |
| Checkpoint-one-through-five replay | PASS, 22 retained commands on the final binary |
| Diagnostics/parser edges | PASS, 71 CLI cases and all 36 emitted diagnostic categories |
| Clean install and startup | PASS, 16 cases including a real bounded loopback request |
| Documentation/organization | PASS, local links resolved; no authored unsafe construct or lint allowance |
| Performance | PASS, 120 fresh CLI processes plus measured board and 4,000-declaration checks |

On this qualification host, the fresh-process p95s were 1.1 ms for checking
`hello.dia`, 2.2 ms for checking `message_board.dia`, and 1.0 ms for running the
hello program. The 82,892-byte source with 4,000 declarations checked in 17 ms
with 7.3 MiB peak resident memory; the message board peaked at 5.4 MiB. The
predeclared budgets were one second p95, five seconds for the large source, and
128 MiB per measured process.

The checkpoint-five Chrome journey remains the current browser evidence because
checkpoint six changes diagnostics, parser edges, startup ordering, packaging,
and documentation rather than browser behavior. No checkpoint remains after
this one. The explicit README limits and every earlier evidence boundary still
apply: this is a complete interpreted MVP pass, not a production-readiness,
security, dependency, cross-platform, or physical-durability proof.

## General-web expansion W1 — complete

Diamond 0.7.0 completes W1 of the separate ten-checkpoint
[general-web plan](.design/general-web-apps.md). The new path supports bounded
multi-file projects, explicit module targets/exports/effect budgets, typed scalar
browser lowering and generated JavaScript, a reactive component, deterministic
versioned artifacts with rechecked loading, two explicitly granted read-only
PostgreSQL operations, and a concurrent loopback asset server. The existing
single-file interpreter remains available.

The [W1 evidence record](verification/web_one_results.md) includes actual Chrome
clicks and scalar semantics, isolated PostgreSQL parameter binding/deadlines,
concurrent HTTP requests, module/artifact boundary exploration, repaired resolver
failures, and compatibility/red-team replay receipts. The
[project guide](docs/projects.md) documents the supported subset and commands.

Nine expansion checkpoints remain, W2–W10. W2 connects shared identity, generated
RPC, protected PostgreSQL CRUD and a Diamond application across two servers.
W1 is a foundation, not the complete general-web release or production profile.

## General-web expansion W2 — complete

Diamond 0.8.0 completes the first connected two-server slice: explicit scalar
RPC contracts, shared PostgreSQL accounts/sessions, checked resource CRUD and
ownership policies, and a reactive workspace tracker with its business handlers
and UI in Diamond. Login through A, creation through B, reads/edits through A,
restart/read through B and cross-user denial were observed on actual processes.

The [W2 evidence record](verification/web_two_results.md) retains actual Chrome,
native API, generated/independent HTTP/PostgreSQL, rollback/delivery/commit-fault
and compatibility observations, plus repaired failures. The
[connected guide](docs/connected-apps.md) defines the exact development profile.

Eight checkpoints remain, W3–W10. W3 completes the language/frontend slice;
production release qualification remains W10.
