# Diamond

Diamond 0.14.0 is a language prototype implemented in Rust. Its existing
single-file interpreter connects checked evaluation, policies, SQLite, forms,
typed HTML, local accounts, and sessions. The W1 expansion adds multi-file
projects, checked server/browser boundaries, a first reactive component compiled
to JavaScript, explicit PostgreSQL host operations, versioned artifacts, and a
concurrent loopback project server. W2 connects explicit generated RPC, shared
PostgreSQL accounts/sessions, policy-enforced CRUD, and a Diamond workspace
tracker across two application processes. W3 adds typed components and shared values,
keyed updates, forms, routes, server rendering/hydration, CSS/assets, source maps,
and checked decimal/time/text/list utilities. W4 adds typed relational PostgreSQL
storage, scoped roles and relationships, indexed cursor queries, authorized
joins/counts/aggregates, serializable mutations, a bounded connection pool and
explicit migrations/backfills. W5 adds opt-in enrollment, invitations, verification,
recovery, shared revocation/rate limits, scoped API credentials and explicit
versioned JSON operations. W6 adds private shared-file streams, policy-controlled
attachments, typed outbound HTTP, opaque secrets and explicit runtime grants. See
the [files/integrations guide](docs/files-and-integrations.md),
[accounts/API guide](docs/accounts-and-apis.md), [relational app guide](docs/relational-apps.md),
[reactive app guide](docs/reactive-apps.md) and [connected application guide](docs/connected-apps.md). W7 adds a transactional job outbox, restricted fenced workers,
typed durable events and reactive WebSocket subscriptions. W8 adds scaffolding,
canonical HTTPS proxy support, deployment validation, operational endpoints,
graceful draining and encrypted quiesced backup/isolated restore. Both reference
applications have Diamond business logic; see the [jobs/live guide](docs/jobs-and-live-apps.md)
and [deployment/recovery guide](docs/deployment.md).

Checkpoint six completes the accepted MVP passes with source-span diagnostics,
parser edge handling, a checked installation and startup path, focused guides,
and measured performance. The [architectural design](design.md) describes a much
larger intended system. This prototype does not establish that design's security
guarantees or production readiness.

The next phase is described in the [general web application expansion plan](.design/general-web-apps.md).
It targets a reactive frontend written in Diamond, PostgreSQL and multiple
application servers, shared identity and permissions, typed APIs, uploads,
external integrations, and durable jobs. W1–W8 are implemented within their
documented profiles; W9–W10 remain planned. Public infrastructure installation,
SMTP and object-store replication remain operator/later work. W4 uses one
current schema with stopped-server migrations; old/new upgrade compatibility is
outside the requested scope. See the [W7/W8 evidence](verification/web_seven_eight_results.md).

Start the new project example with:

```sh
cargo build --locked
target/debug/diamond check examples/foundations/diamond.toml
target/debug/diamond build examples/foundations/diamond.toml --out .diamond/first-build
target/debug/diamond serve .diamond/first-build --listen 127.0.0.1:3000
```

Open http://127.0.0.1:3000 and click **Add three**. The component starts at 2
and displays 5. See the [project guide](docs/projects.md) for the exact W1 subset,
build contracts, PostgreSQL grants, and compatibility boundaries. Build output
must be a new directory; an existing destination is never silently replaced.

The project is published at https://github.com/Corvidae-Coding-Projects/Diamond
under the [MIT license](LICENSE).

## Install and run the first example

Rust 1.96 or newer is required. From this checkout:

```sh
cargo install --locked --path .
diamond --version
diamond check examples/hello.dia
diamond run examples/hello.dia
```

The last command prints `Hello, Diamond!`. The installed executable has no
runtime dependency on this source tree; a source path is the only input it needs.
For development without installing, use Cargo directly:

```sh
cargo build --locked
cargo run --locked -- check examples/message_board.dia
cargo run --locked -- run examples/hello.dia
cargo run --locked -- run examples/web.dia --listen 127.0.0.1:3000
```

Open http://127.0.0.1:3000 while the last command runs. The server is a bounded,
single-connection development adapter, restricted to loopback. Stop it with Ctrl-C.
`--max-requests N` makes it exit after N connections for reproducible checks.

## Command reference

| Command | Behavior |
| --- | --- |
| `diamond check FILE` | Parse and check without executing, opening a database, or serving HTTP. |
| `diamond run FILE [OPTIONS]` | Evaluate `main` or serve declared routes on loopback. |
| `diamond check PROJECT.toml` / `diamond check BUILD_DIRECTORY` | Check a module graph or recheck and verify an artifact build. |
| `diamond build PROJECT.toml --out NEW_DIRECTORY` | Emit versioned server, browser, page, and contract artifacts. |
| `diamond serve PROJECT_OR_BUILD --listen ADDRESS` | Serve the checked browser asset on loopback with concurrent HTTP handling. |
| `diamond run PROJECT_OR_BUILD [--postgres-env NAME]` | Evaluate the server entry with an optional explicit local PostgreSQL grant. |
| `diamond project-init PROJECT --postgres-env NAME` | Explicitly initialize the versioned W2 PostgreSQL profile. |
| `diamond project-user-add PROJECT --postgres-env NAME --user USER` | Provision a shared development account with a password prompt. |
| `diamond serve PROJECT_OR_BUILD --listen ADDRESS --postgres-env NAME --origin URL` | Serve connected RPC/components with shared identity and the exact loopback public origin. |
| `diamond db-init FILE --database PATH` | Initialize the exact checked SQLite schema. |
| `diamond user-add --auth PATH --user NAME` | Provision a local development account; prompts without echo. |
| `diamond explain CODE` | Explain a diagnostic category, for example `D204`. |
| `diamond new DIRECTORY --template workspace\|catalog` | Scaffold a complete checked reference app in a new directory. |
| `diamond project-work PROJECT --postgres-env NAME --runtime-env NAME` | Run one restricted durable worker; supervise two processes. |
| `diamond project-deploy-check PROJECT ...` | Validate origin, stores and capability grants without deploying. |
| `diamond project-backup PROJECT ...` / `project-restore PROJECT ...` | Create an encrypted quiesced snapshot or restore into an isolated empty target. |
| `diamond project-recover PROJECT --postgres-env NAME --policy resume\|cancel` | Explicitly choose paused-job recovery with actors stopped. |
| `diamond --help` / `--version` | Show the installed command surface or build version. |

`run` options are `--database PATH`, `--auth PATH`,
`--listen 127.0.0.1:PORT`, and `--max-requests N`. Repeated or unknown options
fail explicitly. Database-backed routes require the database host grant at
startup. The account and application databases must be separate files.

Continue with the [language guide](docs/language.md), the
[diagnostic reference](docs/diagnostics.md), and the ordered
[example index](examples/README.md).

Explicit database access:

```sh
mkdir -p .diamond
cargo run --locked -- db-init examples/message_board.dia --database .diamond/board.sqlite
cargo run --locked -- run examples/storage.dia --database .diamond/notes.sqlite
cargo run --locked --example embedding
```

`check` performs no database or runtime operations. Declared effects alone do not
grant database access: `run` also requires the explicit `--database` host grant.
The storage example deliberately permits anonymous creation of visible notes.

## Try the authenticated message board

```sh
mkdir -p .diamond
cargo run --locked -- user-add --auth .diamond/accounts.sqlite --user alice
cargo run --locked -- run examples/message_board.dia \
  --database .diamond/board.sqlite --auth .diamond/accounts.sqlite \
  --listen 127.0.0.1:3000
```

The first command prompts for a password without echoing it. Choose 12–256 UTF-8
bytes. The account database stores salted Argon2id hashes in a private file,
separate from application records. `user-add` refuses to replace an existing
account. Account files must be mode-0600, single-link regular files on Unix;
symbolic links, replacement during open, special files, incompatible schemas,
and unsupported hash parameters fail at startup. The local provider validates
at most 10,000 accounts before accepting requests. For automation,
`--password-stdin` reads one line; passwords are never command-line arguments.

Open http://localhost:3000, follow **Sign in**, and use the account you created.
Enter a title and body, then select **Create post**. The runtime supplies the
CSRF field, the handler derives authorship from the session, and the browser
follows a typed redirect to the saved post. The example creates unpublished
posts, so only their author can read them. **Your posts** lists your saved records.
Open a post to **Edit post**, **Publish**, **Unpublish**, or **Delete post**.
The public **Published posts** page shows only published records. Validation
feedback includes a **Try again** link; rejected edits leave saved data unchanged.
Each list currently shows up to 100 posts, ordered by opaque ID rather than time.

Restart with the same two database paths and sign in again. Accounts and posts
persist; sessions are held in memory and expire on restart. Signed-in sessions
last eight hours, and sign-in forms have five-minute anonymous sessions. Sign-out
revokes the current session. Cookies are host-only, `Secure`, `HttpOnly`, and
`SameSite=Strict`; use the `localhost` URL for this local HTTP development flow.
The in-memory registry holds at most 4,096 sessions. At capacity, a new anonymous
session replaces the oldest anonymous session and never evicts an authenticated
user; successful sign-in rotates its exact prior session in place.
The runtime owns `/_diamond/login` and `/_diamond/logout`; source routes cannot
occupy that namespace. The local provider issues user identities with no roles.
Server session lifetimes use elapsed monotonic time; a wall-clock adjustment
cannot revive an expired session or invalidate a live one prematurely.

## Initial language surface

- Indentation-sensitive declarations, immutable `let`, annotated functions,
  records with named constructors, scalar defaults, aliases, and nominal IDs.
- `Int` (checked signed 64-bit arithmetic), `Bool`, `Text`, scalar records,
  bounded text, nominal enums with positional payloads, `Result`/`Option`,
  exhaustive matching, immutable lists, bounded iteration, and `?` propagation.
- Explicit `resource Posts: Post` binds a repository to its record type.
  Its source name is `posts`, the lowercase resource name. This preliminary
  syntax avoids guessing a model from pluralization.
- `app`, SQLite and session declarations, `DbRead`/`DbWrite` budgets,
  transitive function effects, and read-only GET/HEAD handlers.
- Restricted scalar policies with principal predicates and default-deny
  missing actions. Updates receive existing and proposed state.
- Structural SQLite get/create/update/delete operations, automatic scoped
  transactions, explicit transaction blocks, and conservative unknown outcomes.
  Bounded selections compose read policies with bound equality filters before
  ordering and pagination. Returned or caught repository failures abort a
  transaction and roll back earlier writes.
  The schema adapter refuses incompatible existing schemas and protected triggers.
- Typed route parameters and handles, restricted HTML trees, contextual URL
  checks, text escaping, and strict URL-encoded form parsing from record types.
- Opaque expiring sessions, CSRF-token binding, redacted token debugging,
  and revocation available to a trusted native embedding host; an optional
  local account provider connects them to browser forms and secure cookies.

Function return annotations are required in this pass. Record defaults are
literal values. Comparisons use matching scalar types; text ordering is ordinal.
Text limits count Unicode scalar values. Supported templates use an allowlisted
set of HTML tags and attributes; text entities are treated as literal text.
Dynamic links and redirects require `route_url`; static URL values must be
absolute internal paths without percent encoding.

## Matching, selections, and fallible operations

See [examples/language.dia](examples/language.dia) for enums, positional payloads,
exhaustive matches, Option/Result recovery, and iteration. Match arms may contain
an expression or an indented block ending in a value. Branch-local bindings do
not escape. Lists hold one checked element type; `for item in values:` produces
a list of the block's results. A `List<Html>` can be interpolated into typed HTML
without reinterpreting text as markup. `len` counts list entries or Unicode
scalar values in text. Iteration is limited to 1,000 entries and the evaluator's
existing step/output budgets.

```diamond
let visible = posts.select(published: true, limit: 20, offset: 0)?
let entries = for post in visible:
  html:
    <li><a href={route_url(PostDetails, post.id)}>{post.title}</a></li>
```

Selection arguments are named scalar equality filters. `limit` is required and
must be 1–100; `offset` defaults to zero and must be 0–10,000. These two names are
reserved query options. Results use ascending binary ID order. Optional filters
accept `None` and `Some(value)` with null-safe equality. Read authorization is
part of the SQL relation before the filters, ordering, limit, and offset; the
adapter also checks each returned row with the reference policy evaluator.
Before selection, the adapter validates the whole base relation in the same
SQLite read snapshot. Malformed hidden or paginated-away records fail the entire
operation. This prototype limits that validation to 10,000 records; larger
relations fail with a resource-limit error instead of returning a partial list.
Stored text values are limited to 1 MiB before decoding into managed values.
Relation validation also uses the evaluation's managed-value work budget, so
large records or relations can exhaust that budget before the row-count cap.

Expected form/repository failures are `Result<T, DataError>` values that can be
matched with `Ok(value)` and `Err(error)`. `error_message(error)` exposes a safe
message; `?` preserves the safe failure class for HTTP mapping. `Err("message")`
constructs an application failure. Internal database failures, budget exhaustion,
and uncertain transaction outcomes remain runtime failures. A transaction cannot
commit after a failed repository operation, even when the program matches its
Err; returning Err from a transaction also rolls back. Nested transactions and
early returns from nested blocks are rejected.
The checker follows transaction-opening calls transitively, including recursion,
and rejects possible nesting. Transaction blocks require a source SQLite
declaration; actual execution also requires the host database path. Unknown
outcomes expose an opaque operation ID, discard the affected connection, and
instruct the caller not to retry automatically.

Routes can render a validation page with `bad_request(typed_html)`, returning an
HTML response with status 400. Ordinary successful typed HTML and redirects keep
their existing behavior. `request.user_id`, `request.authenticated`, and
`request.is_user(id)` read the verified context inside a route; they grant no
authority. The example uses the last predicate to present owner controls, while
repository policies independently enforce all mutations.

## Explicit limits

The following storage, account and transport limits describe the single-file
SQLite compatibility profile. The [W1 project guide](docs/projects.md) and
[W2 connected guide](docs/connected-apps.md) and [W3 frontend guide](docs/reactive-apps.md) state the separate project profiles.

Generic enums, nested/or/literal patterns, unrestricted error-type parameters,
general closures/callbacks, general mutable variables, language-level
concurrent tasks, generational GC, streams/WebSockets/uploads, general
query syntax beyond the selection subset, joins/aggregates/bulk APIs, schema migrations, external networking,
filesystem APIs, raw interop, secrets, native machine-code compilation, and production
deployment are not implemented. Unrecognized syntax and operations fail with
diagnostics. Resource and call budgets bound the implemented evaluator; values
are owned immutable Rust data without a cyclic object model.
Source files must be regular files and are read through a 1 MiB byte ceiling;
special files and larger input fail before parsing.
There is no hard aggregate heap quota or process isolation. Persistent fields
and typed form fields support scalars or one optional scalar; other ordinary
record shapes remain available to the interpreter. Forms cannot expose reserved
CSRF or server-derived fields. These restrictions are checked before execution.
Managed values are limited to depth 64 and 4 MiB of weighted size; an evaluation
has 64 MiB of cumulative weighted value materialization/copy work. Weights charge
128 units per managed node plus owned text/tag/field-name bytes, with map-copy
overhead for scopes. These are conservative interpreter limits, not exact native
allocator accounting. Limit failures bypass ordinary Result recovery and roll
back an active explicit transaction. Earlier implicit commits can remain when
later evaluation fails; enclose related writes in a transaction for atomicity.

Browser authentication requires an explicit `--auth` account database and an
application `session SecureCookie` declaration. Without a provider, private
routes deny requests and POST-form rendering lacks CSRF context. Registration,
password reset, role management, durable sessions, and production authentication
integration remain unsupported. Local account-file permission handling currently
requires Unix. Login verification is bounded to 64 attempts per minute across
the process; this is an initial development boundary.

The single-file development server rejects proxy headers, upgrades, chunking, duplicate
headers, query strings, encoded paths, non-loopback listeners, and nonmatching
Host/Origin values. It supplies restrictive response headers. It does not
terminate TLS, provide a production HTTP parser, or claim production readiness.
Complete headers are limited to 16 KiB and request bodies to 65,536 bytes. To
deliver a 413 at that boundary, the server drains at most 1 KiB of a declared
oversize body within the existing five-second request deadline. Larger or
incomplete inputs are released within that bound; they are not promised a
complete error response.

## Verification

Completion evidence and remaining boundaries are recorded in
[CHECKPOINTS.md](CHECKPOINTS.md). Replay the real CLI/HTTP/database checks with:

```sh
python3 verification/checkpoint_one.py
python3 verification/checkpoint_two.py
python3 verification/checkpoint_three.py
python3 verification/checkpoint_four.py
python3 verification/checkpoint_five.py --full
python3 verification/checkpoint_six.py --full
python3 verification/red_team.py --seed 0xD1A06011
python3 verification/web_two.py
python3 verification/web_two_independent.py
```

The verifiers build expectations from the declared subset, drive the actual
binary and SQLite databases, and use isolated temporary data. Checkpoints two
through five also have separately observed Chrome journeys; the HTTP driver alone
does not establish browser cookie behavior. Checkpoint six retains the final
checkpoint-five Chrome evidence because it changes no browser flow. Its measured
fresh-process p95s on the recorded Linux qualification host were 1.1 ms for the
hello check, 2.2 ms for the message-board check, and 1.0 ms for the hello run. A
4,000-declaration source checked in 17 ms with 7.3 MiB peak RSS. These are local
regression observations, not production performance claims. The accepted pass
order is preserved in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
The red-team campaign additionally attacks terminal output, maximum call graphs,
source and account path types, account replacement races, hash corruption,
session exhaustion, login response uniformity, HTML/redirect injection, HTTP
framing, policy state transitions, and the current RustSec advisory set. Its
bounded result is recorded in
[verification/red_team_results.md](verification/red_team_results.md).
