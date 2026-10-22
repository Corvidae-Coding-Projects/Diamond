# W1 project foundations

Diamond 0.7.0 added the first general-web expansion checkpoint, retained in 0.9.0. It keeps the
single-file interpreter and its SQLite/browser-account behavior. The project
entry is a separate, deliberately small initial subset: modules, scalar browser
functions, one reactive component, checked host operations, and a concurrent
loopback asset server. The [W2 guide](connected-apps.md) describes the now-implemented
connected application path, its additional syntax, grants and limits. This page
describes the W1 baseline; the [W3 guide](reactive-apps.md) supersedes its browser,
shared-type, asset, route, and artifact restrictions in 0.9.0.

## Check, build, and serve

```sh
cargo build --locked
target/debug/diamond check examples/foundations/diamond.toml
target/debug/diamond build examples/foundations/diamond.toml --out .diamond/first-build
target/debug/diamond check .diamond/first-build
target/debug/diamond serve .diamond/first-build --listen 127.0.0.1:3000
```

Use a new output directory for each build. Open http://127.0.0.1:3000 and click
**Add three**, then **Subtract one**. The example starts at 2, then shows 5 and 4.
The host serves the compiled JavaScript module under a content-hashed asset URL;
the business event and shared arithmetic are authored in Diamond.

`serve` accepts a project manifest directly as well. It serves only the page and
its generated browser asset; server source, contracts, and server functions are
not HTTP endpoints in W1. Ctrl-C drains the server. Production origins, proxy
trust, TLS, RPC, authentication, and application routes are later checkpoints.

## Module contract

The [example manifest](../examples/foundations/diamond.toml) names the project,
server and browser entries, root component, modules, relative source paths,
targets, imports, and maximum capability budgets. Unknown manifest fields and
unknown capabilities fail explicitly. All declared modules must be reachable
from an entry. An import makes only the dependency's explicit exports visible.
Imported function names are unqualified in this first subset; ambiguous names
fail instead of selecting a dependency by order.

```diamond
export fn bump(value: Int) -> Int:
  value + 3

fn private_helper() -> Text:
  "visible only inside this module"
```

Module paths stay inside the project root. Shared modules import only shared
modules and have no host authority. Browser modules import browser/shared
modules; server modules import server/shared modules. Cycles, private/missing
symbols, duplicate imports, and server imports into browser code fail checking.
Source-level names beginning `__diamond_` are reserved, including references.
The compiler uses injective internal qualification; names containing underscores
do not create accidental aliases.

W1 project declarations are functions and browser components. Server functions
use the existing function checker; browser-reachable functions currently support
Int, Bool, Text, immutable local bindings, returns, arithmetic/comparisons,
Boolean logic, pure calls, and text `len`. Other constructs fail explicitly when
browser lowering needs them in the original W1 profile. W2 adds project CRUD,
and W3 adds shared types and multiple/nested components. Use a `.dia`
single-file entry for the existing interpreter's full supported surface.

## Component contract

```diamond
export component Counter:
  state count: Int = 2

  on increment:
    count = bump(count)

  html:
    <main><p>{count}</p><button type="button" on-click={increment}>Add three</button></main>
```

Initializers use pure scalar expressions and imported functions. Event handlers
assign only declared state of the same type. Statements observe preceding
assignments in that event, and changes become visible only when the entire event
and rendering succeed. Overflow or another failed update preserves prior state
and displays a bounded generic error; a later valid event recovers.

The compiler lowers scalar expressions to a typed browser IR, then emits direct
JavaScript functions and DOM construction. Int uses checked BigInt operations,
text lengths count Unicode scalar values, and text ordering compares code points.
Literal/interpolated text uses text nodes; it never becomes `innerHTML`.
Allowlisted attributes use `setAttribute`; only a declared event may bind
`on-click`, and only on a button. Dynamic URL attributes and document/form/input
elements are outside this first component subset. This renderer replaces the
component tree on update; keyed updates, focus preservation, hydration, CSS and
the broader lifecycle belong to W3.

Pure functions have 64-call-depth and 100,000-step budgets per initialization,
event, or render. Individual text values remain limited to 1 MiB. This first
browser subset does not establish the server interpreter's weighted aggregate
memory/copy accounting or browser process isolation; those resource semantics
must be completed before the full frontend release.

## Explicit PostgreSQL host operations

The registry currently exposes two server-only operations:

| Operation | Arguments/result | Behavior |
| --- | --- | --- |
| `host.postgres_version()` | no arguments → Text | Read the actual server version. |
| `host.postgres_echo(value)` | Text → Text | Round-trip one structurally bound text parameter. |

Both require `PostgresRead` in the calling module's budget and the application
budget. Effects propagate through calls, including across imports; granting an
import never supplies runtime authority. Normal single-file programs cannot call
these project host operations. Unknown operations and incompatible signatures
fail checking, and host calls cannot run inside application transactions.

The native embedding host supplies a connection string explicitly. The CLI reads
it only from a deliberately named environment variable:

```sh
target/debug/diamond run examples/foundations/diamond.toml --postgres-env DIAMOND_POSTGRES_URL
```

Configure that variable outside source/build files using one explicit local Unix
socket, database, and database account. W1's no-TLS profile rejects TCP/remote
hosts; production PostgreSQL/TLS support is later work. Without the host grant,
the same checked program fails before connecting. Configuration/debug output
never prints the connection string. The operations use an owned asynchronous
PostgreSQL connection driver under the synchronous API, five-second connection
and operation timeouts, server read-only/statement-timeout settings, and explicit
driver cancellation/join on timeout or cleanup. There is no automatic replay or
claim that dropping a connection rolls back an external effect.

This is a real PostgreSQL connection/typed-operation foundation, not application
CRUD, migrations, a pool, or shared authentication. Those are W2/W4/W5 work.

## Artifacts and native API

A W1 build contains `server.json`, `index.html`, `assets/HASH.js`, and `contract.json`.
W3 also emits hashed CSS, explicit static assets, maps, and declared route pages.
The server artifact retains the checked program's source inputs for rechecking.
It is a private deployment artifact and must not be put in a public static asset
directory. The supplied project server exposes only the public browser files.

The contract records format/language versions, input hashes, module targets and
exports, capability budgets, the typed browser IR, host-operation signatures and
limits, and generated artifact hashes. Builds contain no timestamps or host
grants. Repeated builds from identical inputs have identical bytes. Loading a
build checks format/version, recompiles its source, and compares every expected
artifact. It does not deserialize arbitrary executable IR or trust a hash as
proof that a publisher is benevolent. Use trusted source/build provenance.

The public API adds `compile_project`, `load_project_build`, and sealed
`CheckedProject` methods `summary`, `evaluate`, `build`, and `serve`. The existing
APIs remain available. Run the actual native consumer with:

```sh
cargo run --locked --example project_embedding
```

Set `DIAMOND_EXAMPLE_POSTGRES` only when deliberately granting that consumer a
local database connection. Host-backed project evaluation and all project serving
reject use inside an existing Tokio runtime. W1 supports synchronous embedding; an async
embedding profile and its execution/lifetime contract remain later work.

## Limits and verification

Project limits are 64 modules, 4,096 aggregate declarations/components, 1 MiB per
source/manifest, 8 MiB aggregate source, 64 state fields/events per component,
and 256 assignments per event. The current parser/template/value budgets also
apply. Inputs/artifacts must be bounded regular UTF-8 files. Browser artifact
size is at most 8 MiB and individual serialized artifacts at most 16 MiB.

The project HTTP skeleton uses Axum/Hyper and Tokio, two runtime worker threads,
and 16 admitted handlers. Its static handlers do not execute the server program.
The legacy HTTP parser and development-account store are preserved for existing
single-file apps. Neither transport profile claims production readiness.

Replay the new real-interface campaign with `python3 verification/web_one.py`.
It starts/stops its own Unix-socket-only PostgreSQL instance and loopback server,
checks parameter binding and missing grants, compares actual `psql` state/results,
tests concurrent clients/slow connections, and verifies deterministic builds.
Socket creation must be allowed by the execution environment. Replay the retained
module/artifact exploration with `python3 verification/web_one_modules.py`.
Browser interaction and retained resolver failures are documented in the
[W1 checkpoint record](../verification/web_one_results.md).

The integration dependencies are pinned in `Cargo.lock`: Axum 0.8.9 (MIT),
Tokio 1.54.1 (MIT), tokio-postgres 0.7.18 (MIT OR Apache-2.0), Serde 1.0.229,
serde_json 1.0.154, and TOML 1.1.8 (the last three MIT OR Apache-2.0). Their
declared minimum toolchains fit the existing Rust 1.96 baseline. Dropping a
request future does not imply canceling a running blocking task; this initial
asset server performs no blocking host calls, and PostgreSQL read drivers are
explicitly owned and joined. Later server-operation dispatch must keep those
same lifetime constraints.
