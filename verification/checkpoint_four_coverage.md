# Checkpoint-four source and boundary coverage

Reviewed on 21 October 2026 against the final 0.4.0 source. All 20 authored Rust
source files (6,783 lines) were read in full, then the final changed paths and
their callers were checked again. This is an inspection record, not measured
branch coverage. The exact source, manifest, lockfile, example, driver, fixture,
design, executable, toolchain, commands, exits, and observations are fingerprinted
in [checkpoint_four_results.json](checkpoint_four_results.json).

The review followed source input through checking, sealed checked programs,
interpretation, scope/policy checks, SQLite effects, and output/transport. It also
followed expected errors, caught failures, rollback, indeterminate outcomes,
connection discard, and restart. Green checks and file inventories alone were
not used as evidence that these paths had been inspected.

| Source, read in full | Callers, enforcement and recovery inspected | Behavioral evidence |
| --- | --- | --- |
| `src/lexer.rs` | Source limits, indentation, identifiers, strings, token locations, lexical failures. | Earlier checkpoint language rejections and five example checks. |
| `src/parser.rs` | All supported declarations/statements/expressions, precedence, matching, transactions, routes/templates; unsupported syntax boundaries. | Checkpoints one/three and compiler expression contexts; excluded callbacks/query shapes rejected. |
| `src/syntax.rs` | Every parsed statement/expression/type form mapped to checker/evaluator handling. | Compiler campaign covers 20 expression contexts and the supported operation families. |
| `src/checker.rs` | Declaration/type/resource/policy checking; complete transitive effect closure; second transaction pass; route read-only checks; lexical scopes; forms and output sinks. | 395 actual CLI invocations, including recursive graphs, GET/HEAD, resource budgets, no host grant, allowed ordinary values, schema/form exclusions. |
| `src/hir.rs` | Checked program construction remains private; function effect/transaction metadata and typed route/resource handles consumed by runtime. | Existing native consumer; compiler transaction graphs and all five examples. |
| `src/authority.rs` | Scope creation, principal predicates, explicit resource grants, random operation IDs; no scope/principal/repository value constructors. | Missing/read/write budget matrix, direct delete with only write grant, anonymous/authenticated policy comparisons. |
| `src/eval.rs` | Every expression/statement dispatch; function scope and call limits; iteration/match scope; contextual request shadowing; repository failure marking; transaction finish on success/error/returned Err; `?` propagation. | Prior language/rollback regressions, compiler transaction readbacks, caught invalid-selection rollback, later abort after successful selection, real transaction faults. |
| `src/value.rs` | Type compatibility and recursive Option checks; opaque IDs; owned records/variants/lists; safe Result failure classes; display restrictions. Unknown/internal failures stay outside ordinary caught DataError. | Stored primary/sibling/optional ID regressions, restricted sink checks, native display, typed error/transaction campaigns. |
| `src/policy.rs` | Pure closed scalar policy grammar; all comparison/Boolean operators and principal predicates; reference authorization and structurally bound SQL lowering; missing actions deny. | Three separately stated reference policy families: 162 get, 108 select, 36 create, 120 update, 108 delete comparisons; optional policies and arbitrary calls rejected. |
| `src/data.rs` | Exact schema and trigger rejection; parameter binding; get/create/update/delete grants and policies; defaults/derived authorship; before/after state; returned-row read checks; full base-relation validation in one read snapshot; limits; commit/rollback classification and poison handling. | Independent SQLite state after every mutation, 14 action-allowed/read-denied rollbacks, malformed/filter/pagination replays, 10,000/10,001-row boundary, CLI/HTTP commit fault matrix and restart readbacks. |
| `src/error.rs` | Diagnostic/runtime separation; abort/unknown classes, safe native error strings, operation correlation. | Exact compile diagnostics, CLI unknown ID, sanitized HTTP errors and 409/503 distinction. |
| `src/forms.rs` | URL decoding, UTF-8, duplicate/unknown/missing fields, scalar validation, CSRF reserved field, typed Result construction. | Earlier authenticated malformed mutation checks; valid create/edit in HTTP and Chrome; compile-time sink exclusions. |
| `src/template.rs` | Allowlisted HTML/attributes, text/attribute escaping, typed fragments/URLs, static URL restrictions, output limits, automatic CSRF fields. | Restricted sink compile checks; hostile-looking text and Unicode through create/edit/render, literal browser display, typed URL stored-ID regression. |
| `src/auth.rs` | Opaque issuance and token lookup, principal provenance, constant-time CSRF checks, expiration, revocation, redacted debugging, bounded registry. | Real native embedding exercises issue/check/revoke/actual short expiry/redaction; authenticated/anonymous HTTP and browser journeys. |
| `src/accounts.rs` | Password input bounds, Argon2id storage/verification, account identity validation, file permissions, create-only provisioning, local-provider role boundary. | Provisioning checks and separate disposable accounts, failed authentication, native stdin provisioning and real browser login. |
| `src/browser_auth.rs` | Runtime namespace, login/logout, anonymous/authenticated session selection, cookie attributes, Origin/CSRF verification, rate limit, provider lifecycle. | Earlier auth/CSRF rejection replays; new error-cookie reuse; Chrome cookies, restart invalidation, fresh login and logout. |
| `src/http.rs` | Bounded HTTP parser, Host/Origin and header restrictions, safe methods, failures, typed response headers, HEAD body suppression, transport errors. | Earlier raw HTTP rejections, exact HEAD regression, sanitized stored-data errors; unknown responses include opaque ID and no-retry instruction. |
| `src/web.rs` | Loopback/source/host startup grants before opening; account/data separation; explicit HEAD before GET fallback; private gate; CSRF/session dispatch; interpreter scopes; cookies on sanitized errors. | Startup rejects undeclared DB without file creation; private HEAD401/public HEAD200; anonymous error cookie reuse; entire board in HTTP and Chrome. |
| `src/lib.rs` | Public compilation/evaluation/database/server entry points, sealed checked values, explicit host paths, error forwarding. | Eight doctests, real native embedding, CLI/server consumers. Native signatures and Rust1.96 minimum unchanged. |
| `src/main.rs` | CLI argument boundary, source checking before evaluation, explicit database/auth paths, private password input, result display and failure exits. | All earlier and new CLI campaigns; no-database denial; account provisioning; CLI fault matrix. |

Also read: `Cargo.toml`, `Cargo.lock`, all five `.dia` examples and the native
embedding example, the enforcement-relevant design sections (1.1–1.4, 7.1–7.5,
9.2.1–9.3, 12.2–12.4), checkpoint contracts and the new replay drivers/fixtures.
The design file is unchanged. Existing Rust lints forbid authored unsafe code;
the strict C shim is separate verification-only code loaded exclusively into
disposable children. No unsafe Rust or lint allowances were added.

## Limits of the evidence

- This reviews the supported interpreted MVP. There is no implemented closure,
  callback, dynamic-dispatch, package, task/concurrency, interop, secrets, raw-SQL,
  join, aggregate, bulk, or production profile whose guarantees could be tested.
- The policy campaign covers all supported scalar operator families with bounded
  examples, not every expression or state. The local account provider grants no
  roles; `has_role` denial is observed, and positive application role grants are
  not exercised through this provider.
- SQLite/rusqlite, the allocator, cryptography, OS/filesystem and the trusted native
  host remain dependencies of the boundary. Their source and supply chain were
  not audited, and same-user/root tampering is not isolated.
- The actual contention and unlink-acknowledgment faults establish the observed
  commit classifications. They do not emulate power loss, prove disk durability,
  cover all I/O/rollback failures, or qualify crash recovery on other platforms.
- Step/call/text/list/output and selection limits are bounded prototype controls.
  There is no hard aggregate managed-heap or per-request allocation quota. This
  pass does not establish memory-exhaustion resistance or timing noninterference.
- The development server remains single-threaded and loopback-only, without TLS
  or production deployment/authentication guarantees. Broader hostile input,
  cancellation, resource exhaustion and recovery campaigns remain checkpoint five;
  syntax/diagnostic/install/performance polish remains checkpoint six.

Confirmed enforcement gaps from this pass are repaired and replayed. The limits
above are coverage/profile boundaries, not claims that unexecuted fault schedules
passed.
