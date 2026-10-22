# Independent W4 observable oracles

These expectations were written from `verification/web_four.md`, REQ-7/8/9,
and AC-7/8/9 before executing the W4 implementation. They are a qualification
plan, not a passing report. The user's later scope correction removes old/new
upgrade compatibility, compatibility ranges, rollback windows, and version
leases because no users exist yet. Current-schema migrations, resumable bounded
backfills, audit, and a runtime role without owner authority remain required.
The exact accepted syntax is engineering input;
implementation outputs never become expected answers.

The initial HTTP adapter expectation was corrected before final qualification:
the inherited connected RPC profile refuses anonymous calls at the fixed
authentication gate (HTTP 403), instead of returning an empty SQL result. That
is stronger denial than the data model's empty authorized relation. Malformed
large cursors may fail either the envelope bound or cursor decoder; 400/413
are both bounded input rejection. These corrections do not change any expected
authorized row, value, mutation, membership decision, or cursor authority.
Capacity admission is a service-availability rejection (HTTP503 unavailable),
distinct from the inherited login-rate rejection (HTTP429). The independent
pressure oracle is bounded connections/admitted work and subsequent recovery;
it does not require that an unspecified capacity failure use a rate-limit code.

## Independent model

The retained Python model is an ordinary in-memory list of typed records and
explicit membership grants. It does not call Diamond or reproduce its SQL
translator. Its data has seven workspaces, distinct identities, nonuniform
roles, null and non-null optional values, repeated sort keys, mixed Unicode,
NUL, and Int64/Decimal/time boundary values. For large qualification it emits
100,003 issue rows. SQL fixture loading is trusted setup, not application
behavior: ordinary protected observations and writes must use generated RPC or
the public native interface. Independent SQL reads inspect committed rows and
physical plans after those actions.

The fixture relationship is Issue to a distinct Project resource, keeping the
graph acyclic. Projects may be hidden from a workspace member; this intentionally
makes join expectations differ from merely authorizing the issue relation.
The model initially sketched issue self-parenting while the driver was being
prepared; it was corrected before any execution because the contract explicitly
rejects cyclic relationships.

Read access means a membership for the authenticated identity in the row's
workspace and, for issues, rank below1000. The corpus starts entirely below that
ceiling; a write proposing rank1001 exercises failed return-read and complete
request rollback after an earlier successful write. Editor and administrator
roles may mutate; reader cannot. No global
role or role in a different workspace implies a scoped role. Explicit owner
fixtures use exact principal equality, with optional absence unequal to a
verified user and anonymous identity. The fixture's write policy permits an
editor to reference an existing same-tenant parent; a valid ID alone does not
grant a read of that parent or permit cross-tenant relinking. Hidden parents
must stay absent from joins, including their projected values. These fixture policies
are choices for the qualification app, not built-in product policy.

Independent expected pages sort distinct records by the declared field then
nominal ID. Concatenated forward pages must equal the complete independently
authorized sorted record set exactly once. A cursor is position information;
reusing it with a different identity/scope cannot transfer authority. Where a
cursor is explicitly query-bound, a changed query must reject rather than
silently reuse incompatible position metadata. An accepted malformed cursor
may never disclose a row that the same principal could not otherwise read.

Optional equality is language equality: absence equals absence, not a third
unknown value; absence is unequal to a present value. This is checked through
the declared supported policy/query subset, including negation and Boolean
composition when supported. Text compares exact scalar sequences; Unicode
normalization is not implicit and NUL remains data. Aggregates use only the
authorized filtered relation. Empty count is zero; sums and overflow use the
documented numeric result/error contract, never wrapping or including hidden
values. Join expectations authorize both base relations independently before
combination; inaccessible children and parents cannot contribute counts,
aggregate values, or projected fields.

The issue read policy also uses a two-valued tautology over different optional
fields: `optional == mirror or optional != mirror`. The fixture deliberately
contains None/None, None/Some, Some/None, equal Some/Some, and unequal Some/Some
rows. This keeps native/reference and SQL-null checks substantive after the
compiler simplifies the separate self-comparison regression clauses.

## Real interfaces and required observations

| ID | Operation / schedule | Expected independent observation |
| --- | --- | --- |
| S01 | Check supported scalar, Option, unit enum, Decimal/time and nominal ID columns; check unsupported recursive/compound shapes | Supported shapes build; unsupported shapes fail before startup and before schema mutation |
| S02 | Inspect physical schema after initialization | Typed constrained columns, primary/unique/FK constraints and declared indexes exist; explicit profile/version metadata exists |
| S03 | Create exact Unicode/NUL/empty/optional/boundary records; fresh-server readback | Exact typed value equality; no UTF-8 replacement, truncation, coercion or nominal-ID collapse |
| S04 | Duplicate declared unique key, invalid FK, wrong nominal ID, null in required field | Declared constraint or typed-input rejection; independent committed relation unchanged |
| S05 | Deliberately corrupt a supported stored value using schema-owner test setup, then fetch/audit | Fetch fails closed; bounded explicit audit identifies invalid data; no authority or plaintext SQL escape |
| Q01 | Different users list, get, filter, page, count and aggregate | Results equal independent model after per-resource policy; unauthorized IDs never become a count or aggregate contribution |
| Q02 | Several page sizes, duplicate sort values, first/last/empty page, resumed fresh client | Stable deterministic order, exact-once IDs, finite termination, consistent counts and values |
| Q03 | Malformed/truncated/oversize cursor; changed identity, workspace, filter and sort | Safe bounded rejection or safe authorized position semantics as documented; no authority transfer |
| Q04 | Declared one-hop inner/left-equivalent supported join, with hidden parents/children and optional keys | Both source policies apply; no hidden field, existence/count or cross-tenant reference disclosure |
| Q05 | 100,003 rows; PostgreSQL EXPLAIN (ANALYZE, BUFFERS) on actual equivalent plans | Indexed bounded pagination; inspected actual plan/rows; ordinary request does not managed-decode the full relation |
| P01 | User A reader/editor/admin in different workspaces; user B differs; anonymous; forged role/principal wire fields | Only declared verified membership/role grants apply; no global-role or client-field escalation |
| P02 | Optional ownership with both null and present principal IDs; Boolean compositions | SQL behavior equals native/reference language equality, including absence equals absence |
| P03 | Lookup relation lacks ordinary read grant; terminal declared lookup still authorizes scoped operation | Purpose-limited lookup works without returning the lookup relation; arbitrary lookup, unindexed/wrong-type lookup and cycles reject |
| P04 | Read/mutate/delete/relink same ID after revocation commits on A and B | Every request begun afterward denies; no session-affinity authorization cache |
| T01 | Two concurrent read-modify-write operations on one record | Both commit with sequential result, or one aborts distinctly; committed state never loses a confirmed increment |
| T02 | Policy pre-state passes but post-state/relink/return-read would fail | Mutation rejects and whole transaction remains uncommitted, including preceding writes |
| T03 | Hold earlier snapshot, revoke membership elsewhere, release it | Earlier operation either fits a valid serial ordering or aborts; subsequent new request always denies |
| T04 | Serialization abort, server termination, commit-ack loss, HTTP-delivery loss | Distinguish confirmed abort, unknown outcome and confirmed success; independently inspect SQL; no implicit replay |
| C01 | Exhaust bounded admission; unavailable DB; release a failed/aborted request; recover | Bounded rejection/wait; no runaway connections; cleaned reuse and subsequent healthy request; owned work drains |
| M01 | Missing/incompatible storage metadata and shape hash | Startup refuses safely before serving ordinary operations; no implicit migration |
| M02 | Stop servers, migrate current schema explicitly, restart both servers | New schema is checked and consistently served; unsupported changes refuse rather than silently rebuilding data |
| M03 | Backfill small deterministic batches; kill between batches; resume repeatedly | Only committed checkpoints advance; final rows equal model; no duplicate/lost transformation and bounded batch size |
| M04 | Two migration coordinators contend | Serialized execution or explicit refusal; no concurrent unchecked schema transition |
| M05 | Request unsupported destructive schema changes or arbitrary migration SQL | Explicit refusal; committed current data remains unchanged |
| M06 | Resume interrupted migration/backfill and restart servers | Supported current-schema transition finishes from committed progress; no implicit request replay |
| N01 | Compile/run external native consumer using only public API | Same typed result/policy/error behavior; no use of crate-private types or handwritten substitute implementation |

## Evidence discipline and boundaries

- Drivers pin executable/library SHA-256 and retain exact fixture inputs, seed,
  schedules, commands, expected/observed values, and distinct failure receipts.
- Each scenario is PASS, FAIL, BLOCKED, or NOT RUN. Counts measure executed
  observations and never imply whole-codebase coverage or total security.
- The model's result sets, membership relations and sorted values are stable
  before a Diamond run. Any oracle correction names its independent reason.
- The two-server fixture uses real PostgreSQL and actual generated RPC. A
  transport/failure proxy may withhold actual bytes or terminate a connection;
  it may not implement application responses, credentials or expected answers.
- Root operates the application through real Chrome. HTTP/CLI/native evidence
  here cannot establish browser controls, rendering, navigation or focus.
- W4 does not supply W5–W10 features. Downloads/jobs/subscriptions and public
  deployment remain later boundaries. W2 document and SQLite compatibility are
  separate required replays; they are not inferred from relational success.
- SQL owner credentials exist only for isolated setup, corruption and
  independent observation. Running Diamond servers must use the constrained
  managed runtime role, with migration authority absent. Passing as schema
  owner is not accepted as runtime qualification.
