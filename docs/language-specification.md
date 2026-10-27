# Diamond language contract

This is the canonical description of the **implemented Diamond 0.20.0 language**.
LP1 established the baseline against `4c0195f`; LP2 adds the connected language
spine; LP3 completes typed navigation/forms, LP4 adds managed native controls,
LP5 adds durable transactional database actions and reconciliation, LP6 hardens
cross-boundary runtime behavior, LP7 adds compiler-backed tools, and LP8 qualifies
the assembled profile. It describes a bounded interpreter and managed browser
runtime, not infrastructure or deployment certification.

Read this with the [execution-profile and security matrix](language-profiles.md),
the [function catalog](../examples/functions/README.md), and the
[LP5 action evidence](../verification/lp5/). Stable contract identifiers
`C01`–`C18` below connect promises to implementation and observations. “Must” and
“rejects” describe the current supported contract; evidence strength and known
limits are stated separately in the ledger. Historical W/G/L checkpoint guides
do not override this contract.

## C01 — Source and declarations

Source is bounded UTF-8 text. Indentation uses spaces; tabs are rejected, including
literal tab characters inside source strings. Use the escape `\t` for a tab value.
Blank lines and `#` comments outside strings do not affect indentation. Identifiers
use ASCII letters, digits, and `_`, starting with a letter or `_`. Project names
beginning `__diamond_` are reserved for compiler qualification.

Text literals support `\n`, `\r`, `\t`, `\"`, and `\\`. Other backslash escapes and
inline `#{...}` string interpolation reject. Actual Unicode characters can appear in
strings. HTML interpolation is a separate checked construct.

Core declarations are aliases, records, enums, resources, and functions.
Single-file applications add `app`, `policy`, `route`, and `public route`. Projects
declare module targets/imports in `diamond.toml`; browser modules add components.
Shared modules can declare exported `route`/`form` record schemas with checked
literal defaults and optional input fields; server modules
can declare exported `action` boundaries. See the [connected syntax guide](language-spine.md).
Not every declaration belongs in every execution profile: see C02.

```diamond
type NoteId = Id<"note">

type Note:
  id: NoteId
  title: Text<length: 1..120>
  published: Bool = false

enum State:
  Draft
  Rejected(reason: Text)

fn title(note: Note) -> Text:
  note.title
```

Function parameters and results are checked. A result annotation is required.
Records use named fields; function calls and enum payloads use positional
arguments. Defaults are checked constant values, not arbitrary effects.
Declarations must resolve to finite supported types; recursive schemas and
generic user-defined type/function parameters are unsupported. Internal inferred
`Never` and `Success` types are not additional user declarations.

Source locations use one-based lines and Unicode-scalar columns. CLI and editor
diagnostics preserve the originating project-module path, stable category, expected
and actual type where applicable, source excerpt/range, and a category-specific
remedy. Browser source maps identify reachable pure-function wrappers, components,
and event/action handlers while excluding unreachable server/private declarations.
See the [tooling contract](tooling.md).

## C02 — Modules and checked execution

Shared modules import only shared modules. Server modules import server/shared
modules; browser modules import browser/shared modules. Imports expose explicit
exports. Missing/private symbols, ambiguous imports, cycles, inaccessible exported
types, and server imports into browser code reject. Qualification must not alias
two different source names. The root browser component has no properties;
non-root component properties are checked at each use.

Checking precedes evaluation, lowering, rendering, and native execution. Native
consumers receive sealed checked-program/project values rather than unchecked
caller-supplied executable IR. A declaration of allowed source effects is an upper
bound on requirements; it is not a grant of native authority.

## C03 — Evaluation and control flow

Statements run in source order. `let` bindings are immutable. Function calls
evaluate arguments left to right before entering the function. Record constructor
argument expressions also run in written order; declared defaults supply omitted
fields. No argument is reordered by field-name sorting.

Operators, low to high precedence, are `or`, `and`, equality, comparison,
addition/subtraction, multiplication/division, and unary operations. `and`/`or`
short-circuit. `match` evaluates its subject once and only evaluates the selected
arm. Matching must cover each possible Boolean, enum, Option, or Result variant;
duplicate, unreachable, incompatible, and incomplete arms reject. A final binding
or `_` arm can cover the remaining alternatives.

`for item in values:` evaluates a bounded list in order and produces a new list
from each body result. Bindings inside a branch or iteration do not escape their
scope. This is not an unrestricted loop facility.

A block's final expression supplies its value. An explicit `return` is supported
only as the last statement at function level. Nested `return` inside a match,
iteration, or transaction block rejects; subsequent statements after a return
reject. Closures, mutable function locals, arbitrary loops, and first-class
functions/authority are not supported. Recursion is checked and runtime-bounded.

## C04 — Integers and explicit numeric wrappers

`Int` is signed 64-bit. Addition, subtraction, multiplication, division, and unary
negation check overflow; division by zero fails. Integer division truncates toward
zero: `-7 / 3` is `-2`. There is no implicit floating-point conversion. Browser
arithmetic uses BigInt; generated integer AST literals use decimal strings to avoid
loss before evaluation.

Source integer tokens are nonnegative magnitudes with optional single underscores
between digits. Unary `-` is separate. **Current lexical limitation:** the direct
spelling `-9223372036854775808` rejects because its magnitude does not fit `Int`.
The valid expression `-9223372036854775807 - 1` produces the minimum value.

`Decimal` is a signed 64-bit count of millionths, with six fractional digits.
`decimal_units` constructs it from units; `decimal_text` always prints six decimal
places. Multiplication/division use widened exact intermediate arithmetic and an
explicit `"half_even"` or `"half_away"` mode; invalid modes, overflow, and zero
division fail. Negative ties round with the same declared rule.

`Timestamp` is signed Unix milliseconds and `Duration` is signed milliseconds.
Use explicit constructors, conversions, `timestamp_add`, and `timestamp_diff`.
They do not read an ambient clock. Numeric wrappers do not silently become `Int`
or acquire generic arithmetic/comparison operators.

## C05 — Text, lists, and comparison

Text is valid Unicode without implicit normalization. `"é"` (U+00E9) and `"é"`
(U+0065 U+0301) have different scalar sequences. `len(Text)`, bounded-text constraints, slicing, and
lexicographic ordering count Unicode scalar values, not bytes, UTF-16 code units,
grapheme clusters, or displayed characters. UTF-8 byte budgets are separate.
For example, `len("A😀é")` is `3`; `text_slice("A😀é", 1, 1)` is `"😀"`.

Text equality compares exact sequences. `text_contains` and `text_replace` use
literal matching. Replacement is nonoverlapping. An empty search inserts the
replacement at every scalar boundary, including both ends: replacing `""` in
`"ab"` with `"·"` yields `"·a·b·"`; for empty input the result is `"·"`.
`text_trim` uses Unicode White_Space semantics, implemented explicitly in the
browser runtime.

`text_slice` and `list_slice` require nonnegative start/count with the entire
range inside the source. They do not clip. An empty slice at the end is valid.
`list_get` returns `None` for negative/out-of-range indexes. List construction,
mapping, appending, and wire decoding remain bounded; list operations return new
values. `len` accepts Text/bounded Text (scalar count) and List (item count);
it does not accept arbitrary records or other collection-like values.

Equality operators are checked for `Bool`, `Int`, text/bounded text, and matching
nominal IDs. Ordering is checked for integers and text/bounded text. There is no
generic record, enum, list, secret, blob, or stream equality/ordering surface.
Use explicit fields and checked matching instead of implicit compound conversion.

## C06 — IDs, validation, and authority

`Id<"note">` and `Id<"user">` are different types. IDs contain 1–128 ASCII letters,
digits, `-`, or `_`. An ID is a name, never proof of ownership, identity, or access.
Single-file and browser alias construction require validated literals. Project
server code can construct the declared alias from text with runtime validation.
Typed routes/forms and checked wire codecs provide other checked input paths.
Shared functions must satisfy the target's restriction when browser-reachable.

Native `Principal`, `HostGrants`, and borrowed `Scope` are outside the managed
value system. They cannot be constructed, serialized, copied through RPC, stored
as application data, or obtained from an ID. An authorized native consumer must
supply the actual verified scope/grants. Source/module capability budgets do not
provide them.

Effects propagate transitively through helper calls, including recursive call
graphs. Missing required source effects or runtime grants fail. The compiler must
not accept a GET/HEAD handler with transitive mutations. Resource policies deny an
action unless explicitly permitted. Business policy remains an application
decision; enforcement is owned by the checker/runtime.

## C07 — Errors and results

`Option<T>` distinguishes `Some`/`None`. `Result<T, DataError>` carries ordinary
data failures. `Result<T, ApplicationError>` uses a declared enum for public typed
application errors. `?` propagates only a compatible error type. It cannot convert
database failures into application enums or catch internal authority, budget,
infrastructure, and uncertain-commit errors as ordinary values.

Local legacy `Err("detail")` retains its message. Over a client boundary it is
redacted to fixed codes/messages. Current codes include `Denied`, `InvalidInput`,
`Aborted`, `RetryableAbort`, and `ApplicationFailure`; arbitrary error text is not
published. A top-level legacy RPC Result is unwrapped: Ok supplies the success
payload, Err becomes a sanitized transport failure. Nested safe legacy Results
can cross as tagged values. A declared application Result preserves its typed
Ok/Err shape and rollback semantics. Native `Error::Application(Value)` carries
the managed error for the authorized host; its Display remains opaque.

Runtime diagnostics and HTTP status alone are not a complete action outcome
protocol. Browser `pending`/`error` indicate transport/local work. Eligible
transactional actions use runtime-issued identities, sealed receipts, and typed
reconciliation; [C13](#c13--cancellation-and-identity-epochs) defines that boundary.

## C08 — Transactions and external effects

Single-file SQLite operations outside `transaction db:` have operation-level
atomicity. Explicit transactions group writes; a returned error or a failed
operation poisons the transaction even when Diamond code matches/catches that
failure. A subsequent apparent success must not commit those writes.

Connected relational requests use serializable PostgreSQL transactions. Explicit
transactions participate in that request boundary; nested source transaction
blocks reject rather than commit an independent inner transaction. A declared
application Err rolls back its application transaction before its safe typed error
is returned. A caught
failed database operation must not revive a poisoned transaction.

Commit acknowledgement loss is not proof of abort. The runtime reports an opaque
uncertain outcome when it cannot establish commit state. The browser must not
automatically replay a mutation or label an aborted fetch a confirmed rollback.
Concurrency aborts require an explicit retry; they are not success.

An eligible transactional action commits its bounded PostgreSQL CRUD and sealed
receipt in the same transaction. Its explicit status operation reconciles the same
invocation after uncertainty; it does not replay the mutation. Business rejection
rolls the action body back to a savepoint before the terminal rejection receipt is
committed. This guarantee does not extend across external systems.

Project functions combining external HTTP with application writes/explicit
transactions reject, as do Git writes/admin operations combined with application
writes/explicit transactions. Host calls cannot be hidden inside an explicit
transaction through a helper. Durable adapters have narrower checked effect and
delivery rules; there is no cross-system exactly-once promise. Workflows permit
their supported transactional database/event effects, not arbitrary HTTP, mail,
Git, delivery, nested jobs, or workflow execution.

## C09 — Resource policy and current authorization

Database adapters apply authorization before pagination/count/order. Updates
check the relevant old and proposed state and protected result reads. A
`created_by` field is derived from verified identity and cannot be supplied as
ordinary client form data. API credential restrictions attenuate every relevant
base relation and state transition; a filter, cursor, parent ID, or cached result
cannot grant additional access.

Protected handles and delayed work are consumed under current account, binding,
resource policy, and relevant parent scope. Jobs/workflows reauthorize at each
attempt. File references and stream descriptors use their dedicated boundaries.
Serializable ordering can allow an already authorized transaction to precede a
revocation; revocation is not asynchronous cancellation of every executing
transaction. Later protected requests/attempts must recheck authority.

## C10 — Restricted-value sinks

Restrictions follow the complete type, recursively through aliases, records,
enum payloads, lists, Options, and Results. A wrapper must not launder a restricted
value. Missing/unknown/excessively deep type shapes are unsafe, not public by
default. See the complete [sink table](language-profiles.md#restricted-value-sinks).

Secrets are adapter-owned handles. The recommended declarative API injects
credentials at the named server adapter; Diamond application code does not read
the bytes. The lower-level server secret handle can reach only its matching
checked consumer. It cannot be formatted, compared, rendered, persisted as
application data, returned publicly, placed in browser state, or sent through
generic RPC. A secret nested anywhere in the project main result rejects.

Blobs and streams are nominal descriptors, not unrestricted byte/network values.
They can occur in supported browser state and dedicated file/watch operations;
ordinary RPC/storage/display/comparison reject them. Possessing their identifier
does not bypass authorization. `Html`, `Url`, and `Response` remain server-only
types; a plain string cannot forge them.

## C11 — Output, internal URLs, and forms

Server typed HTML accepts checked tags/attributes and escapes scalar text.
Only checked Html values (including supported Html lists) are inserted as markup.
`markdown(Text)` produces sanitized server Html, with active source HTML/images
removed and a separately constrained link policy. That policy is not identical
to the internal application-URL policy. There is no general raw-HTML/JavaScript
string injection feature.

Reactive interpolation accepts checked display scalars, not raw Html/Url/Response
or implicit compound strings. The runtime uses text nodes and checked attributes.
Templates do not supply an arbitrary JavaScript escape hatch.

Single-file named typed routes support checked `route_url` construction. Static
internal URL validation rejects protocol-relative/external URLs, `%`, backslashes,
whitespace, and controls. The shared validator also accepts a simple `#fragment`
for static link/redirect values. This is not general URL parsing. Reactive routes
support literal local paths and named shared schemas with finite scalar path/query fields.
`href={IssuePage(id: row.id)}` builds a checked internal path; arbitrary Text is
not accepted as a dynamic URL. Canonical construction/decoding uses one strict
UTF-8 percent-decoding step, canonical integers, sorted queries, default/None
omission, exact keys, bounded scalar kinds, and overlap/reserved-path rejection.
See the [typed input contract](typed-inputs.md) for exact empty/default/Unicode
rules shared by both HTTP servers, native rendering, and the browser.

Single-file `request.form<Record>()` performs strict URL-encoded scalar decoding,
including bounds and nominal IDs, and rejects unknown/duplicate fields, malformed
encoding, reserved CSRF names, and author-supplied server-derived identity fields.
Reactive input bindings hold Text/Bool drafts. A declared shared form schema can
decode named controls into one checked record at submit. Supported fields are
Text/bounded Text, canonical Int, Bool, nominal IDs, unit enums, and one-level
Option of those kinds. One static matching control is required per field.
Generated scalar codecs, constraints, and associated finite-code field errors
preserve bounded ordinary invalid drafts without dispatching an action. The server
independently checks the exact record/tag shape and bounds. Schema password,
hidden/file, restricted and server-derived fields reject; use dedicated account
operations. Schema forms own their POST fallback, preventing native GET leakage
of drafts before hydration. General validation callbacks/nested form objects
are unsupported. The [managed control contract](components.md) adds generated
labels, descriptions, errors, native controls, and bounded live statuses.

## C12 — Reactive state and component lifetime

Component properties are immutable and checked exactly at use sites. Initializers
observe properties and preceding state. Derived declarations recompute in order.
`pending`, `error`, and `route` are runtime-owned locals. Templates, initializers,
derived expressions, properties, and keys are pure. Remote operations belong to
event expressions; ordinary browser function bodies remain pure.

Events accept typed positional parameters, including row-specific IDs. Arguments
must match the handler's exact types and arity. Assignments run in order against proposed
local state. Evaluation/type/pre-render failures preserve the preceding committed
local state; a later valid event recovers. This is not a general transactional DOM
or server rollback guarantee. A prior RPC may have committed before a later RPC
or rendering operation fails.

`<each>` uses unique checked Int/text/nominal-ID keys within the list. Surviving
instances retain state and DOM identity when reordered; duplicate keys reject
before patching. Components cannot form cycles. `mount` runs after
hydration/attachment; removal disposes pending work/subscriptions. `unmount` is
pure and cannot call the server. A fresh mount creates new local state. Component
outputs declare typed data messages. `emit changed(value)` queues a message for
delivery after the child's local commit. Each child output must bind a compatible
declared parent handler; root outputs reject. Delivery rechecks source frame,
binding generation, instance lifetime, and identity epoch. Lifecycle events have
no arguments and cannot emit outputs. Messages require recursively safe data;
Blob, Stream, Secret, server markup, and native authority cannot cross this sink.
Callback closures and slots remain unsupported.

`bind-value` binds mutable Text state on supported text/password inputs, textarea,
and selects; `bind-checked` binds Bool checkboxes. File inputs use the dedicated
file binding path. Native selects support unique static/dynamic options; keyed
fragments flatten to direct options, with dynamic Int/text/ID values formatted to
the selected Text state. Raw controls require authored labels and IDs. Managed
`field`, `submit`, and `status` primitives own their associations and bounds; see
[components](components.md). Images require explicit author-supplied alt text.

## C13 — Cancellation and identity epochs

A new component event or bound-input change supersedes its previous pending
event. Property changes, navigation, and removal invalidate stale replies.
Identity-changing operations advance an epoch, invalidate private pending work,
close relevant watches/selected-file state, reset all component state to initial
values, and serialize identity transitions. Login/logout/verify/reset/password
change/session termination/account disable or enable require a direct root
assignment before other remote work. The initiating handler receives its verified
result against fresh state; a child/nested/late transition is a checked error.
A late reply must not apply to a new identity or a different component instance.

An event has a 30-second deadline and at most 32 remote calls. An action uses two
calls, preparation and execution. Output chains share a 64-message/30-second
budget. Cancellation stops
local consumption/owned work where supported; it does not undo remote commits.
The runtime exposes an uncertainty warning rather than silently retrying writes.
`Action<T,E>` is a local managed outcome type with `ActionIdle`,
`ActionSubmitting`, `ActionSucceeded(T)`, `ActionRejected(E)`, `ActionInvalid`,
`ActionDenied`, `ActionAborted`, `ActionUnknown`, `ActionExpired`,
`ActionConflict`, and `ActionCorrupt`. Only the dedicated action
boundary transports it; generic RPC and application persistence reject it.
Its payloads must be safe. The runtime publishes Submitting automatically, and
fences stale completion; cancellation does not assert that a commit was undone.

An exported action requires an explicit `[actions.NAME]` binding, relational-v1
storage, accounts-v1 identity, database-only effects, and a
`Result<T, declared application enum>`. `replay = "transactional"` additionally
requires a declared resource and matching nominal parent parameter, read authority
for all CRUD dependencies, and no set/aggregate query. `replay = "never"` retains
the bounded LP2 single-attempt profile without a durable result.
Declared rejection rolls back the request transaction. A caught policy failure
still denies/rolls back the action. Source functions cannot invoke an action as
an ordinary helper, and ordinary RPC cannot bind it. Token APIs can explicitly
bind the same action using their current credential scopes and policies.

For `transactional`, the native runtime issues a 24-hour random invocation bound to
the checked action version, canonical input, verified user, cookie/API profile and
API restrictions, declared parent, and expiry. Its encrypted authenticated receipt
captures at most 64 protected CRUD accesses and 256 KiB plaintext. Mutation and
receipt commit atomically. Duplicate execution serializes to one terminal outcome.
Status/result retrieval rechecks the current account, credential restrictions,
declared parent, saved facts, and current rows. Expired, changed, corrupt, missing,
or pruned control state never becomes a fresh execution.

The browser discovers at most 16 unacknowledged server references after reload,
stores no form body/result/invocation in browser persistence, and exposes checked
status/ack controls. Identity changes clear private recovery state. Total retained
invocations are bounded to 128 per user/application and 4,096 per application.
See [reliable actions](actions.md) for the exact protocol and external-effect limit.
Watches have bounded reconnect/coalescing behavior and release ownership on
unmount/identity changes. Dependency cleanup claims beyond these implemented
paths require LP6/LP8 fault and prolonged-run observations.

## C14 — Navigation, SSR, and hydration

Reactive `<route path="/about">` declares an exact local pathname. Declared links,
direct HTTP, and browser history select the corresponding route. Root state
survives client navigation; removed route components dispose/remount. A failed
route initialization preserves the previous successful location/content.
`<route name="IssuePage" as="detail">` supplies checked path fields such as
`detail.id` and typed query fields. Direct GET/HEAD renders the same pure public
shell as client navigation; changing pathname or query remounts named-route
descendants. Simple static fragments remain distinct from typed field data.

Build-time Rust rendering produces public pure initial pages. It does not execute
mount/RPC effects or render authenticated request data. Hydration checks its
contract/DOM, adopts supported native edits, attaches handlers, and runs mounts.
A mismatch leaves an explicit reload error and does not enable handlers. The
normalization contract accounts for HTML parser CRLF/NUL handling. There is no
streaming/island/authenticated-request SSR contract.

## C15 — Wire and publication boundary

Ordinary RPC exports use only recursively safe declared schemas. Int is a
canonical signed decimal JSON string, never a JSON number (`"01"`, `"-0"`, plus
signs, and out-of-range values reject). Decimal/Timestamp/Duration have exact
tagged shapes and canonical unit strings. Records have exactly the declared
fields; enum tags/arity use declared public names; Options/Results use explicit
tags. Strict decoding rejects duplicate keys (including escaped duplicates),
extra fields, invalid Unicode, wrong shapes, and bounds violations. Form/native
consumer input has its own checked boundary; accepting JSON does not grant scope.

Browser builds/source maps include reachable browser/shared code, with private
server source excluded. Compiler-owned component/event markers map failures to a
public `Component.event` label and relevant source location without publishing a
server stack or source. `server.json` and `contract.json` are private artifacts.
Serving uses only the generated public allowlist. A public source canary check
is a bounded observation, not a complete information-flow proof.

## C16 — Versions and prototype compatibility

Current identifiers are package/language **0.20.0**, manifest schema **1**,
artifact format **5**, RPC wire **7**, and HTTP API contract **1**. These name
different boundaries; one must not substitute for another. The artifact loader
requires the exact current language/format versions, recompiles stored trusted
source, and byte-compares all expected generated files. Alteration or incompatible
builds fail explicitly. Hashes/recompilation check consistency, not publisher
authenticity. Rebuild trusted source into a fresh output directory.

Prototype upgrade compatibility is not required. LP2–LP5 changed these versions
for the new syntax, IR, and input/action protocols. Rebuild old trusted source; incompatible
artifacts and mismatched browser/server contracts fail explicitly.

**Forward release policy adopted by LP1:** before the first supported stable
language release, publish its supported source/profile/wire/artifact versions and
stability boundary. After that boundary, an incompatible source or semantic change
requires an explicitly identified breaking release and migration notes; incompatible
wire/artifact shapes require their own version changes. Patch updates preserve
valid documented behavior except necessary security corrections, which must be
documented and replayed. Never accept a previously forbidden authority escape
for compatibility. Only version combinations explicitly qualified are supported;
there is no implied rolling-upgrade or mixed-version protocol today.

This paragraph is a release policy, not an implementation of new negotiation,
deprecated-syntax support, or migrations for old prototype users.

## C17 — Bounded execution

Every profile enforces its limits at its owned boundary. The
[budget matrix](language-profiles.md#resource-budgets) is part of this contract.
Important differences include native managed value depth 64 versus browser/wire
depth 32, and profile-specific request/transaction lifetimes. Fuel and weighted
materialization are conservative work bounds, not exact allocation accounting or
proof of process isolation. Rust/browser implementations need not charge an
identical number of steps for every operation; supported semantic results must
agree when neither exhausts its budget.

Budget failure must be explicit and must not become permission to skip validation,
publish restricted values, or retry uncertain writes. LP6 and LP8 exercised
cleanup, cancellation, prolonged-run, and valid-follow-up invariants within the
recorded envelopes; this remains bounded evidence, not allocator/process isolation.

## C18 — Evidence and changes to the contract

The [LP1 ledger](../verification/lp1/README.md) records the historical baseline;
the [LP2 ledger](../verification/lp2/README.md) records the connected spine, and
the [LP3 ledger](../verification/lp3/README.md) records typed inputs, and
the [LP4 ledger](../verification/lp4/README.md) records the component delta, the
[LP5 ledger](../verification/lp5/README.md) records reliable actions, the
[LP6 ledger](../verification/lp6/README.md) records hardening, and the
[LP8 ledger](../verification/lp8/README.md) records the assembled final candidate.
They separate reviewed source paths, real consumer observations, historical
evidence, documentation drift, and gaps. A green compiler check does not establish
that a database, worker, Git, or browser lifecycle path ran. Contract changes must
start with independent positive/adversarial expectations and retain minimal real
failures.

An apparent profile difference must be classified before unification: intentional
restriction, documentation drift, known limitation, or confirmed defect. A
reproducible disagreement with a promised semantic result blocks the relevant
checkpoint until resolved; changing only the expectation to fit the code is not
a fix. Existing enforced protections are prerequisites of LP2–LP8.
