# W2 connected applications

**Historical W2 snapshot.** The scalar-only RPC subset and future-checkpoint
statements below describe the original connected-app milestone. Current compound
values, relational storage, adapter boundaries, versions, and limits are defined
by the [canonical contract](language-specification.md) and
[profile matrix](language-profiles.md). Retained W2 receipts remain historical.

Diamond 0.8.0 adds explicit generated RPC, shared PostgreSQL accounts/sessions,
protected CRUD and a minimal workspace tracker authored in Diamond. This is the
local two-server slice of the expansion plan. Full frontend semantics, relational
query planning, enrollment/recovery and production deployment remain later work.

The W2 sections below preserve the original connected profile. In 0.9.0, the
[W3 guide](reactive-apps.md) supersedes the scalar-only RPC, direct-only remote
expressions, replace-on-update renderer, and wire-version-1 restrictions. The
PostgreSQL, account, transport, and authority limits here still apply.

## Try the complete local demonstration

With Rust 1.96, Python 3 and PostgreSQL's `initdb`, `pg_ctl` and `psql` installed:

```sh
cargo build --locked
python3 verification/web_two.py --serve-fixture .diamond/w2-demo --public-host 127.0.0.1 --public-port 3000
```

Use a new fixture directory. Open http://127.0.0.1:3000 in a browser. This
verification fixture starts its own Unix-socket-only PostgreSQL database, two real
Diamond processes and a forwarding proxy. It provisions synthetic `alice` and
`bob` accounts with the demonstration password `Synthetic W2 test password 42!`.
Those credentials belong only to disposable test data. Ctrl-C stops the owned
servers and database. The fixture's private files remain in its ignored directory.

Sign in as Alice, enter a workspace/title/details, create an issue, and edit it.
The proxy sends login to A, creation to B, and reads/edits to A. Reload, click
**Current user**, then enter the saved issue ID to read it again. Switching to
Bob clears prior issue data; submitting Alice's ID is denied. The automated
campaign also restarts B and reads committed data and the shared session through B.

The proxy provides routing only. The Rust runtime provides protocol/storage/auth
adapters; [server.dia](../examples/workspace/server.dia) contains the business
handlers and ownership policies, and [tracker.dia](../examples/workspace/tracker.dia)
contains the inputs, state and events. No handwritten application JavaScript or
mock persistence supplies this journey.

## Connect an explicitly granted local database

Configure a connection string outside source/build files in an environment
variable such as `DIAMOND_POSTGRES_URL`. Use one explicit Unix socket, account and
database. The current no-TLS development profile rejects TCP/remote connections.
Given that configuration:

```sh
target/debug/diamond check examples/workspace/diamond.toml
target/debug/diamond project-init examples/workspace/diamond.toml --postgres-env DIAMOND_POSTGRES_URL
target/debug/diamond project-user-add examples/workspace/diamond.toml --postgres-env DIAMOND_POSTGRES_URL --user alice
target/debug/diamond build examples/workspace/diamond.toml --out .diamond/workspace-build
target/debug/diamond serve .diamond/workspace-build --listen 127.0.0.1:3001 --postgres-env DIAMOND_POSTGRES_URL --origin http://localhost:3000
```

Account provisioning prompts without echo; `--password-stdin` accepts one bounded
line. It never replaces an existing password. Start a second instance on port
3002 with the same build/database/origin. A local proxy on port 3000 must preserve
the original Host and Origin and omit Forwarded/X-Forwarded headers. Merely
supplying those headers does not configure trust. Direct backend requests must
use the exact configured public Host. Production proxy/TLS configuration is not
implemented in this profile.

Initialization explicitly creates the `diamond_w2` profile. Serving never creates
or migrates it. A different stored resource shape or unknown profile fails
startup. Application names identify separate stores and cookie namespaces;
instances of one application use the same name, source and contract. Use a
dedicated development database with explicit table access. Runtime/migration
credential separation and deployment validation remain subsequent work.

## Checked server and browser contract

The manifest declares maximum application and module budgets, for example
`DbRead<Issues>` and `DbWrite<Issues>`. Calls propagate effects across server
imports, and each caller/module must cover its transitive requirements. A runtime
PostgreSQL connection grant is separate from those source budgets. Browser/shared
modules receive no database authority and cannot import server implementations.

W2 permits aliases, records, resources and policies in the server entry module.
Their type/resource schema is shared by the checked server graph, including
imported server helpers. Cross-module browser record interfaces remain W3 work.
Only exported functions explicitly named in `[rpc]` become endpoints:

```toml
[rpc]
create_issue = "server.create_issue"
read_issue = "server.read_issue"
```

Exporting a helper alone exposes no HTTP operation. RPC signatures currently
accept Int, Bool and Text (including bounded Text), returning a scalar or
`Result<Scalar, DataError>`. Result failures become sanitized transport errors;
record/Id/Scope/host-handle serialization is not provided. On the server,
`IssueId(text)` validates dynamic project input, `id_text(id)` explicitly formats
a nominal ID, and `join_text(List<Text>, separator)` supplies a bounded listing.
These project-only helpers do not change the single-file language profile.

Components add `bind-value={text_state}` on static text/password inputs and direct
asynchronous event assignments such as `selected = rpc.create_issue(...)`.
`session.login(username, password)`, `session.user()` and `session.logout()` are
checked event operations. Remote calls are allowed only as direct event
assignments, not initializers, ordinary pure functions or nested expressions.
Full async expressions, forms/lifecycle/hydration and typed browser records are W3.

Events run serially with controls disabled while work is pending. Local state is
committed after the whole event/render succeeds. Earlier server calls may already
have committed when a later event step fails. Transport retries apply only to
explicit pre-execution overload responses. Lost mutation replies still require
reconciliation before retrying; see the current [runtime retry contract](deployment.md#bounded-runtime-capacity).
Application code must clear account-specific state on identity changes, as the
example does. Rendering still replaces the component tree, with W1's explicit
browser resource-accounting limitations.

## Wire and identity boundary

RPC uses POST `/_diamond/rpc/NAME` with exactly:

```json
{"contract":"CONTENT_HASH","arguments":["argument",true,"9223372036854775807"]}
```

The declared positional type determines each value. Int uses a canonical signed
decimal string (no coercion, leading zero, plus sign, whitespace or `-0`); Bool is
a JSON boolean; Text is a JSON string. Unknown/duplicate envelope fields, wrong
arity/types, invalid Unicode, bounds failures and incompatible contracts are
rejected. Success is `{"value":VALUE}`; failure is a bounded `{"error":CODE}`.
Build contracts record wire version 1, signatures/effects and `rpc_id`. A stale
client receives `contract_mismatch` and must reload.

Session bootstrap is GET `/_diamond/session` with `X-Diamond-Session: 1`.
Generated code obtains a session-bound CSRF value there. Login/logout and every
RPC require the exact configured Origin and `X-Diamond-CSRF`; credentialed
cross-origin access is not enabled. Ambiguous cookies and forwarded authority are
rejected. The connected page's CSP allows scripts and fetch only from itself.
Server artifacts/contracts/source remain unavailable over HTTP.

Passwords use the existing bounded Argon2id parameters. PostgreSQL holds password
hashes, hashed opaque session tokens, database-clock expiry and atomic shared
login counters. Raw session tokens are returned only as host-only Secure,
HttpOnly, SameSite=Strict cookies. Login rotates the token; logout/expiry are
observed by the next operation on either node. RPC identity is reconstructed
from current shared state; client arguments cannot provide a verified principal.
W2 accounts are explicitly provisioned, without registration, recovery, API
credentials or a full account-disable/password-change lifecycle.

## Storage, transactions and limits

The versioned W2 profile stores typed records as bounded JSON documents in
PostgreSQL. It uses `json` to preserve escaped U+0000 and reads `payload::text`
before decoding in Rust. PostgreSQL documents the Unicode distinction between
`json` and `jsonb` in its [JSON type reference](https://www.postgresql.org/docs/current/datatype-json.html).
This profile does not use PostgreSQL JSON text extraction to decode field values.
Queries bind values structurally; Diamond exposes no arbitrary SQL endpoint.

IDs/creator fields are server-derived. The existing checked policy reference
evaluator authorizes reads, proposed creates, before/after updates and deletes.
Selection checks policies before filters, offset and result limits. A coarse
application advisory lock serializes RPC transactions across nodes. All CRUD in
one RPC is atomic; caught data-operation failures poison that transaction.
Explicit Diamond transaction blocks remain checked and cannot nest. Failed
commit acknowledgement produces `outcome_unknown`, not a rollback claim.

W2 limits are 1,000 records per application, 16 KiB encoded record payloads
(32 KiB maximum stored representation), 100 selected results and offset at most
1,000. Wire request bodies are at most 16 KiB and must arrive within three seconds;
individual bounds do not override that aggregate transport limit. There are at
most 64 RPC exports, defaults of 16 admitted requests and four blocking workers,
4,096 shared
sessions, 10,000 provisioned accounts, 30-minute session expiry, and shared login
limits of ten attempts per username and 100 per application per minute.
The managed server also defaults to 128 bounded waiting requests and a one-second
queue deadline. See [runtime capacity](deployment.md#bounded-runtime-capacity)
for checked configuration, safe overload backoff and database conflict handling.

Each operation has a five-second database deadline and each owned connected work
item a ten-second execution budget after connection and setup. An in-flight worker retains
its capacity permit and owned driver when its client disconnects; cleanup aborts
and joins the driver. Disconnect does not imply rollback. These are development
bounds, not production connection admission, request streaming or deployment SLOs.

W4 supplies relational schemas/constraints, SQL query plans, relationships, finer
concurrency, pools and migrations. The W2 profile assumes managed application
tables; arbitrary native database/host tampering is outside that contract.

## Native API and evidence

`CheckedProject` adds `initialize_postgres`, `add_postgres_user` and
`serve_connected`. Synchronous setup/serving rejects nested async runtime use.
The [native consumer](../examples/connected_embedding.rs) exercises initialization,
provisioning and the existing project API with explicit disposable credentials.

See [W2 evidence](../verification/web_two_results.md) for actual browser, CLI,
two-node HTTP/PostgreSQL, independent verification, generated inputs, fault
injection, repaired failures and compatibility observations. Replay with:

```sh
python3 verification/web_two.py
python3 verification/web_two_independent.py
python3 verification/regressions/w2_unicode_storage/replay.py
```
