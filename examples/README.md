# Diamond examples

Use this directory as a learning path and as copyable application code. Every
`.dia` file is checked by the real Diamond compiler; runnable examples are also
executed by `verification/examples.py`.

Build Diamond once from the repository root:

```sh
cargo build --locked
```

Then choose the shortest path for what you want to learn.

## Learn the language

1. [`hello.dia`](hello.dia) — a user function and `main`.
2. [`functions/basics.dia`](functions/basics.dia) — records, IDs, enums,
   `Option`, `Result`, and exhaustive matching.
3. [`functions/text.dia`](functions/text.dia) — every text utility.
4. [`functions/lists.dia`](functions/lists.dia) — every list utility and `for`.
5. [`functions/decimals.dia`](functions/decimals.dia) — fixed-scale decimal
   construction, arithmetic, rounding, and formatting.
6. [`functions/time.dia`](functions/time.dia) — timestamps and durations.
7. [`language.dia`](language.dia) — a compact combined enum/list/result example.

Run any pure file directly:

```sh
target/debug/diamond check examples/functions/text.dia
target/debug/diamond run examples/functions/text.dia
```

The complete callable reference is
[`functions/README.md`](functions/README.md). It maps every built-in, constructor,
request helper, repository method, policy helper, browser namespace, and host
operation to checked source.

## Learn server-rendered applications

- [`functions/web_helpers.dia`](functions/web_helpers.dia) — typed routes,
  `route_url`, HTML, forms, request identity, `bad_request`, and safe errors.
- [`web.dia`](web.dia) — a smaller route and form example.
- [`functions/storage_methods.dia`](functions/storage_methods.dia) — all SQLite
  repository methods in one transaction.
- [`storage.dia`](storage.dia) — the smallest policy-controlled SQLite program.
- [`message_board.dia`](message_board.dia) — accounts, sessions, CSRF, CRUD,
  selection, transactions, editing, publishing, deletion, and redirects.

Run the web helper:

```sh
target/debug/diamond run examples/functions/web_helpers.dia \
  --listen 127.0.0.1:3000
```

Open http://127.0.0.1:3000. Stop it with Ctrl-C.

The SQLite gallery includes exact setup commands in
[`functions/README.md`](functions/README.md#storage-functions).

## Learn multi-file projects

- [`foundations/`](foundations/) — the smallest shared/server/browser module
  graph and reactive component.
- [`workspace/`](workspace/) — generated RPC, shared PostgreSQL sessions,
  ownership policies, reactive forms, keyed components, and browser routing.
- [`functions/relational/`](functions/relational/) — every relational query
  method: `select`, `page`, `cursor`, `count`, `sum`, and `join`, plus CRUD.
- [`functions/authority/`](functions/authority/) — the full checked syntax for
  PostgreSQL host calls, secrets/HTTP, jobs, events, RPC, sessions, API tokens,
  files, and live subscriptions.

Check and build a project without opening a database or network connection:

```sh
target/debug/diamond check examples/functions/relational/diamond.toml
target/debug/diamond build examples/functions/relational/diamond.toml \
  --out .diamond/relational-example
```

Build output must be a new directory.

## Run complete applications

[`live-workspace/`](live-workspace/) is the primary full application. It has
workspace roles, projects, issues, comments, files, account lifecycle, API
credentials, outbound HTTP, background jobs, and live events.

[`catalog/`](catalog/) is the second full application. It demonstrates private
drafts, public content, JSON APIs, publishing, live updates, and deduplicated
notifications.

Both directories contain their own walkthrough. You can also scaffold an editable
copy:

```sh
target/debug/diamond new .diamond/my-workspace --template workspace
target/debug/diamond new .diamond/my-catalog --template catalog
```

The [deployment guide](../docs/deployment.md) covers PostgreSQL initialization,
two servers, two workers, runtime grants, Caddy, backup, and restore.

## Earlier checkpoint applications

These directories remain useful when you want to isolate one layer:

| Directory | Focus |
| --- | --- |
| [`relational-workspace/`](relational-workspace/) | PostgreSQL schema, policies, relationships, and pagination. |
| [`accounts-workspace/`](accounts-workspace/) | Enrollment, verification, recovery, revocation, and API credentials. |
| [`files-workspace/`](files-workspace/) | Private file streaming and destination-scoped outbound HTTP. |

The newer `live-workspace` combines all three layers and adds jobs/events.

## Rust embedding examples

- [`embedding.rs`](embedding.rs) — single-file compilation, evaluation, sessions,
  CSRF binding, revocation, and expiry.
- [`project_embedding.rs`](project_embedding.rs) — project checking, build/load,
  and explicit host grants.
- [`connected_embedding.rs`](connected_embedding.rs) — PostgreSQL initialization
  and account provisioning through the public Rust API.

Run the dependency-free embedding example with:

```sh
cargo run --locked --example embedding
```

The PostgreSQL examples require their documented disposable database environment
variables. Never put database strings, passwords, API tokens, or private runtime
paths in Diamond source or a committed manifest.
