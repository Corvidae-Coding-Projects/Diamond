# W3 reactive applications

Diamond 0.9.0 adds the language/frontend slice to the existing local project and
connected PostgreSQL profiles. The workspace example now has typed issue cards,
keyed local edits, a validated creation form, and an About route. All application
behavior is Diamond; JavaScript is the compiler-owned managed runtime.

Build with `cargo build --locked`. For a complete disposable two-node example,
run `python3 verification/web_three.py --serve-fixture /tmp/diamond-w3-demo` and
open http://127.0.0.1:39171/ . This starts real PostgreSQL and provisions synthetic
`alice` and `bob` accounts with password `Synthetic W2 test password 42!`.
Type `stop` in the fixture terminal to stop its servers and database. The
`/qualification` route contains verification controls, not part of the example's
normal application. Existing explicit provisioning/connection commands are in
the [connected guide](connected-apps.md).

## Components and templates

```diamond
export component Card(title: Text):
  state draft: Text = title
  state mounts: Int = 0
  derived caption: Text = "Draft: " + draft
  on mount:
    mounts = mounts + 1
  on unmount:
    draft = ""
  html:
    <section><label for="draft">Draft</label><input id="draft" type="text" bind-value={draft}><p>{caption}</p></section>
```

Properties are immutable, typed inputs. Initial state and derived declarations
observe preceding declarations; derived values are recalculated during updates.
Only declared state can be assigned in handlers. Component locals `pending`,
`error`, and `route` are supplied by the runtime. Initializers, derived values,
properties, keys, and templates are pure and cannot call RPC/session operations.
`mount` runs once per mounted instance after hydration/attachment. Removal
disposes the instance and cancels its pending request; remount initializes a new
instance. `unmount` is pure cleanup and cannot dispatch server calls.

`<use component="Card" title={value}></use>` checks all properties exactly.
`<when test={condition}>…</when>` conditionally mounts content.
`<each items={values} as="item" key={item.id}>…</each>` accepts a List with
Int, Text, or nominal Id keys. Keys must be unique within that list. Reordering
and inserting preserve surviving instances, local state, and DOM nodes. A
duplicate key rejects the proposed state before patching the DOM. Component
cycles are rejected. Render compound values through fields, `match`, formatting,
and lists rather than implicit string conversion. Text always uses escaped HTML
or text nodes. Runtime element IDs `diamond-app` and `diamond-error` are reserved.

Use `bind-value` with mutable Text state on text/password inputs, textarea, or
select; `bind-checked` with Bool state on checkboxes. Select supports static,
uniquely valued option children. Forms require `on-submit={handler}` and native
required/minlength/maxlength validation. Buttons bind `on-click={handler}`.
Provide labels and distinct IDs as in the workspace example. Native validation
precedes dispatch. Pending/error state is available for accessible status text
and disabling controls. Unrelated updates preserve active controls and edits.

## Events, navigation, and hydration

An event evaluates assignments in order against proposed local state. Pure
functions, nested RPC arguments, lazy Boolean/match branches, List expressions,
and sequential `for` calls may compose remote calls. Pure function bodies remain
pure; remote operations belong to event expressions. The whole proposed local
update is committed only after evaluation, type checks, and rendering succeed.

Each new event or bound input supersedes the previous event for that component.
Property changes, navigation, and removal invalidate old replies. Event fetches
have a 30-second deadline and a maximum of 32 remote calls. Identity changes
invalidate outstanding work and serialize login/logout. Cancellation does not
prove a server operation rolled back. Earlier server calls may commit even if a
later call or local render fails. There is no automatic mutation replay; the UI
warns about uncertain outcomes and asks users to refresh before retrying.

`<route path="/about">…</route>` declares content for a canonical local path.
Links to declared paths use client navigation; back/forward and direct HTTP
requests select the same route. Query/hash, endpoint/asset paths, and dynamic URL
attributes are outside this profile. Root state survives client navigation;
components removed by a route switch are disposed and remount on return. Failed
route initialization keeps the last successful URL/content and displays an error;
a failed click does not add a history entry.

Rust renders deterministic public initial content for every declared route.
There is no authenticated build-time rendering. Hydration checks the contract
and DOM structure, adopts existing nodes and native control edits, then mounts
components and attaches handlers. A mismatch leaves an explicit reload error.
This is full-page hydration, without streaming, partial islands, or independent
server-rendered authenticated requests. Browser back-forward cache suspension
cancels work without claiming server rollback; resumption reattaches navigation.

## Shared values and errors

Shared modules can export aliases, records, enums, and pure functions. Public
interfaces cannot expose private type names. Type names/constructors are
qualified internally; wire enum tags use their public source names. Browser
functions support immutable locals, returns, field access, match, for, List,
Option, Result, and declared record defaults in the checked managed subset.
Generic enum parameters, general closures, mutable function locals, and host
authority as values remain unsupported.

`Result<T, ApplicationError>` accepts a declared application enum. `Ok` infers
the surrounding error type; `Err` carries that enum, and `?` requires the same
error type. A database DataError cannot silently become an application error.
Native `Error::Application(Value)` exposes the managed error to an authorized
host while its Display is opaque. Declared application Err results roll back an
explicit transaction; infrastructure failures and unknown commit outcomes stay
failures rather than invented application values.

Legacy `Result<T, DataError>` remains supported. Pure `Err("detail")` and
`error_message` preserve detail locally. Across RPC, DataError is deliberately
redacted to `Denied`, `InvalidInput`, `Aborted`, or `ApplicationFailure`; the last
decodes as `Application failed.`. Use an explicit application enum for public
structured details. A top-level legacy Result failure retains W2's sanitized
HTTP error behavior. A nested legacy Result can cross as a tagged value.

RPC accepts recursively bounded records, enums, nominal IDs, lists, Option,
Result, Unit, Bool, Text, Int, Decimal, Timestamp, and Duration. Authority,
principals, scopes, HTML, host handles, and recursive/unbounded schemas are not
wire values. Int is a canonical decimal JSON string; numeric wrappers use
`{"tag":"Decimal","value":"1000000"}`, and other compound values use exact
declared shapes. Duplicate JSON keys, including escaped duplicates, extra
fields, wrong tags/arity, invalid Unicode, and bounds violations are rejected.
Client identity still comes only from verified shared sessions.

## Pure utility reference

| Type/operation | Semantics |
| --- | --- |
| Int | Checked signed 64-bit, including arithmetic in the browser through BigInt |
| `decimal_units(Int)`, `decimal_text(Decimal)` | Signed 64-bit millionths; formatting always has six fractional digits |
| `decimal_add`, `decimal_subtract` | Checked same-scale arithmetic |
| `decimal_multiply(a,b,mode)`, `decimal_divide(a,b,mode)` | Explicit `half_even` or `half_away` rounding; zero division/overflow reject |
| `timestamp(Int)`, `duration(Int)` | Signed 64-bit Unix milliseconds / milliseconds; no implicit clock access |
| `time_millis`, `duration_millis`, `timestamp_add`, `timestamp_diff` | Explicit conversion and checked time arithmetic |
| `len`, `text_slice(text,start,count)` | Unicode scalar count and bounded scalar slice |
| `text_contains`, `text_trim`, `text_replace` | Literal matching, Unicode White_Space trimming, scalar boundaries for empty-needle replacement |
| `int_text`, `id_text`, `join_text` | Explicit bounded formatting; join accepts List<Text> and separator |
| `list_append`, `list_reverse`, `list_slice`, `list_get` | Immutable bounded lists; get returns Option, negative/out-of-range is None |

## Assets, diagnostics, and limits

Declare `styles = ["app.css"]` and `assets = { logo = "diamond.svg" }` in the
manifest. Inputs stay within the project root and are bounded regular UTF-8
files. This release supports CSS stylesheets and explicit CSS/SVG/txt assets;
binary assets, package-manager dependencies, and arbitrary application scripts
are deferred. Use `src="asset:logo"` or `url("asset:logo")` in CSS. Outputs are
content hashed and served under same-origin CSP; direct SVG documents are
sandboxed. At most 64 style/asset declarations share the existing 8 MiB aggregate
project budget, with a 1 MiB ceiling per input.

Generated source maps contain only reachable browser/shared declarations, with
unreachable declaration lines redacted. Server sources stay private. Mappings
identify pure-function entry wrappers; they are not expression-level stepping
or component-event source maps. Console errors retain runtime stack context;
user-visible errors are bounded and do not disclose managed application errors.

Browser evaluation allows 100,000 steps and 64 call frames per event/render,
depth-32 managed/wire values, at most 1,000 list items, 4 MiB weighted single
values, and 64 MiB weighted materialization work. Rendering allows 1,024 component
instances and 8,192 template nodes. These limits do not isolate the browser
process or measure exact allocator usage. The existing 64-module/project budgets,
bounded HTTP bodies, and W2 PostgreSQL locking/1,000-row limits still apply.
Production serving, relational query planning, pools/migrations, full account
lifecycle, uploads, external HTTP, jobs, and deployment remain W4–W10 work.

Build artifacts now declare language 0.9.0 and wire version 2. Rebuild trusted
source into a fresh directory; earlier language-version builds must not be reused.
The loader still recompiles and compares all artifacts. Never publish private
`server.json` or `contract.json` as public static content.

See the [W3 evidence](../verification/web_three_results.md) for the actual
browser/CLI/HTTP/PostgreSQL/native observations, retained regressions, and replay
commands. Earlier W1/W2 evidence remains historical and is not overwritten.
