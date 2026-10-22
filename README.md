# Diamond

Diamond is a small, checked language for writing web applications. Application
logic, policies, server-rendered pages, and reactive browser components are
written in `.dia` files. The Rust runtime provides the controlled boundaries to
PostgreSQL, HTTP, files, accounts, jobs, and WebSockets.

This repository contains Diamond 0.16.0. It is a working language prototype with
two complete reference applications and retained real-interface qualification.
Its documented Linux profile is release-qualified; it is not a hosted service or
a claim that every production environment is secure.

## What you can build

Diamond currently supports:

- command-line programs with checked integers, text, records, enums, lists,
  `Option`, `Result`, matching, and bounded utility functions;
- server-rendered sites with typed routes, forms, HTML, redirects, local
  development accounts, SQLite, and authorization policies;
- multi-file applications with shared, server, and browser modules;
- reactive browser components compiled from Diamond to JavaScript;
- PostgreSQL records, relationships, policies, indexed queries, pagination,
  migrations, and backfills;
- shared accounts and sessions, scoped API credentials, private uploads,
  destination-scoped outbound HTTP, durable jobs, and live events;
- multiple application servers and workers behind an HTTPS proxy, with health
  checks, draining, encrypted snapshots, and isolated restore.

The best places to see the whole system are:

- [`examples/live-workspace/`](examples/live-workspace/) — memberships, projects,
  issues, comments, files, jobs, live updates, and account management;
- [`examples/catalog/`](examples/catalog/) — private drafts, public publishing,
  JSON APIs, live updates, and deduplicated external notification;
- [`examples/functions/`](examples/functions/) — every built-in function and
  callable runtime surface, organized as small examples.

## Install Diamond

You need Rust 1.96 or newer.

```sh
git clone https://github.com/Corvidae-Coding-Projects/Diamond.git
cd Diamond
cargo install --locked --path .
diamond --version
```

For work inside this checkout, you can build once and use
`target/debug/diamond` instead:

```sh
cargo build --locked
target/debug/diamond --version
```

## Your first Diamond program

Create `hello.dia`:

```diamond
fn greeting(name: Text) -> Text:
  "Hello, " + name + "!"

fn main() -> Text:
  greeting("Diamond")
```

Check it before running it:

```sh
diamond check hello.dia
diamond run hello.dia
```

The result is:

```text
Hello, Diamond!
```

Diamond uses indentation to define blocks. Function return types are required.
Bindings created with `let` are immutable, and calls are statically checked.

## Values, records, and functions

The main scalar types are `Int`, `Bool`, `Text`, nominal `Id`, `Decimal`,
`Timestamp`, and `Duration`. Records, enums, `Option`, `Result`, and immutable
lists compose those values.

```diamond
type UserId = Id<"user">

type Person:
  id: UserId
  name: Text<length: 1..80>
  age: Int

enum AccountState:
  Active
  Suspended(reason: Text)

fn describe(state: AccountState) -> Text:
  match state:
    Active => "active"
    Suspended(reason) => "suspended: " + reason

fn label(person: Person) -> Text:
  person.name + " is " + int_text(person.age)
```

Construct records with named fields and enums with positional payloads:

```diamond
let person = Person(id: UserId("ada"), name: "Ada", age: 36)
let state = Suspended("review")
```

Use exhaustive matching for enums, `Bool`, `Option`, and `Result`. Use `?` to
propagate a `Result` failure from a compatible function.

```diamond
fn optional_label(value: Option<Text>) -> Text:
  match value:
    Some(text) => text
    None => "missing"

fn propagate(result: Result<Text, DataError>) -> Result<Text, DataError>:
  let value = result?
  Ok(value)
```

See the runnable [`basics.dia`](examples/functions/basics.dia) and
[`storage_methods.dia`](examples/functions/storage_methods.dia) examples for
complete calls.

## Built-in functions

The standard library is intentionally small and bounded:

- text: `text_slice`, `text_contains`, `text_trim`, `text_replace`, `join_text`;
- lists: `list_append`, `list_reverse`, `list_slice`, `list_get`, `len`;
- decimals: `decimal_units`, `decimal_text`, `decimal_add`,
  `decimal_subtract`, `decimal_multiply`, `decimal_divide`;
- time values: `timestamp`, `duration`, `time_millis`, `duration_millis`,
  `timestamp_add`, `timestamp_diff`;
- conversion: `int_text` and `id_text`.

Every function has a runnable example, signature, and notes in the
[`examples/functions` catalog](examples/functions/README.md). Try all pure
examples with:

```sh
for file in examples/functions/{basics,text,lists,decimals,time}.dia; do
  diamond run "$file"
done
```

## Records, storage, and policies

A resource binds a record to checked storage operations. Policies are mandatory
for protected operations and default to denial when an action is absent.

```diamond
app NotesApp:
  database SQLite
  provides DbRead<Notes>, DbWrite<Notes>

type NoteId = Id<"note">

type Note:
  id: NoteId
  title: Text
  visible: Bool = true

type NewNote:
  title: Text<length: 1..120>

resource Notes: Note

policy Notes:
  read(note, by principal):
    note.visible
  create(proposed, by principal):
    proposed.visible

fn main() -> Result<NoteId, DataError>:
  let saved = notes.create(NewNote(title: "My first note"))?
  Ok(saved.id)
```

The source declaration sets the maximum database authority. The host grant is a
separate command-line argument:

```sh
mkdir -p .diamond
diamond db-init examples/storage.dia --database .diamond/notes.sqlite
diamond run examples/storage.dia --database .diamond/notes.sqlite
```

Repository methods are `create`, `get`, `update`, `delete`, and `select`. The
PostgreSQL profile also provides indexed `page`, `cursor`, `count`, `sum`, and
configured `join`. See [`examples/functions/storage_methods.dia`](examples/functions/storage_methods.dia)
and the checked [`relational` function project](examples/functions/relational/).

## Routes, forms, and HTML

Routes declare their visibility, method, typed path, and optional handle:

```diamond
type NoteId = Id<"note">

type EchoForm:
  message: Text<length: 1..120>

public route GET "/notes/{id:NoteId}" as NotePage:
  html:
    <main><h1>Note {id}</h1></main>

public route POST "/echo":
  let form = request.form<EchoForm>()?
  html:
    <main><p>{form.message}</p></main>
```

HTML is parsed and checked as part of the program. Interpolated text is escaped,
tags and attributes are allowlisted, dynamic internal links use `route_url`, and
forms decode into declared records. The complete helper example is
[`web_helpers.dia`](examples/functions/web_helpers.dia):

```sh
diamond run examples/functions/web_helpers.dia --listen 127.0.0.1:3000
```

Open http://127.0.0.1:3000 and stop the development server with Ctrl-C.
In another terminal, exercise its typed form route with:

```sh
curl --data 'message=Hello from Diamond' http://127.0.0.1:3000/echo
```

## Multi-file and reactive applications

A project manifest assigns each module to `shared`, `server`, or `browser`.
Imports share types and pure functions; they do not grant database, network, file,
job, or account authority.

```toml
schema_version = 1
name = "Counter"
server = "server"
browser = "ui"
component = "Counter"

[modules.shared]
path = "shared.dia"
target = "shared"

[modules.server]
path = "server.dia"
target = "server"
imports = ["shared"]

[modules.ui]
path = "ui.dia"
target = "browser"
imports = ["shared"]
```

Browser components use typed state and event handlers:

```diamond
export component Counter:
  state count: Int = 0
  derived label: Text = "Count: " + int_text(count)

  on increment:
    count = count + 1

  html:
    <main>
      <p>{label}</p>
      <button on-click={increment}>Add one</button>
    </main>
```

Build and serve the included counter:

```sh
diamond check examples/foundations/diamond.toml
diamond build examples/foundations/diamond.toml --out .diamond/counter
diamond serve .diamond/counter --listen 127.0.0.1:3000
```

Open http://127.0.0.1:3000. Build destinations must be new directories; Diamond
never silently replaces an existing artifact.

Components also support `derived`, `<when>`, keyed `<each>`, `<route>`, `<use>`,
forms, `bind-value`, `bind-checked`, generated `rpc` calls, file controls, and
typed `<watch>` subscriptions. See the
[`reactive application guide`](docs/reactive-apps.md).

## Scaffold a complete application

Diamond embeds two checked starting points:

```sh
diamond new my-workspace --template workspace
diamond new my-catalog --template catalog

diamond check my-workspace/diamond.toml
diamond build my-workspace/diamond.toml --out .diamond/my-workspace
```

The workspace template includes accounts, permissions, records, comments,
uploads, outbound HTTP declarations, jobs, and live events. The catalog template
shows private authoring and public publishing. Their local READMEs explain every
source file and the full browser journey.

Running a connected application requires a local PostgreSQL Unix-socket grant.
The workspace also needs a private shared object directory:

```sh
mkdir -p .diamond/runtime/objects .diamond/runtime/mail
chmod 700 .diamond/runtime .diamond/runtime/objects .diamond/runtime/mail

export DIAMOND_POSTGRES='host=/run/postgresql dbname=diamond_example user=diamond_example'
export DIAMOND_RUNTIME="{\"objects\":\"$PWD/.diamond/runtime/objects\",\"http\":{}}"

diamond project-init .diamond/my-workspace --postgres-env DIAMOND_POSTGRES
diamond project-files-init .diamond/my-workspace \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-admin-add .diamond/my-workspace \
  --postgres-env DIAMOND_POSTGRES --user alice --email alice@example.invalid
diamond serve .diamond/my-workspace --listen 127.0.0.1:3000 \
  --origin http://localhost:3000 --postgres-env DIAMOND_POSTGRES \
  --runtime-env DIAMOND_RUNTIME --identity-mailbox "$PWD/.diamond/runtime/mail"
```

Create the empty database and least-privilege role before these commands. The
administrator command prompts for a password without echo. Open
http://localhost:3000 for the development secure-cookie profile. The
[`deployment guide`](docs/deployment.md) covers two servers, two workers, Caddy,
validation, backup, restore, and process supervision.

## Command guide

| Command | Use |
| --- | --- |
| `diamond check PATH` | Parse and type-check a `.dia` file, project manifest, or build. |
| `diamond run FILE` | Run a single-file `main`, or serve it with `--listen`. |
| `diamond build PROJECT --out NEW_DIR` | Build checked server/browser artifacts. |
| `diamond serve PROJECT_OR_BUILD ...` | Serve a project on a loopback address. |
| `diamond new DIR --template workspace\|catalog` | Create a complete application. |
| `diamond db-init FILE --database PATH` | Initialize a checked SQLite schema. |
| `diamond user-add --auth PATH --user NAME` | Add a local development account. |
| `diamond project-init PROJECT --postgres-env NAME` | Initialize the current PostgreSQL profile. |
| `diamond project-admin-add ...` | Provision the first connected-app administrator. |
| `diamond project-work ...` | Run one restricted durable worker. |
| `diamond project-deploy-check ...` | Validate capabilities and deployment configuration. |
| `diamond project-status/migrate/backfill/audit ...` | Operate explicit PostgreSQL schema changes. |
| `diamond project-backup/restore/recover ...` | Operate encrypted quiesced recovery. |
| `diamond explain CODE` | Explain a diagnostic such as `D204`. |
| `diamond --help` / `--version` | Show the installed command surface and version. |

Run `diamond --help` for exact arguments. The focused guides explain the larger
commands:

- [language](docs/language.md)
- [projects and modules](docs/projects.md)
- [reactive browser applications](docs/reactive-apps.md)
- [PostgreSQL and policies](docs/relational-apps.md)
- [accounts and JSON APIs](docs/accounts-and-apis.md)
- [files and outbound integrations](docs/files-and-integrations.md)
- [jobs and live events](docs/jobs-and-live-apps.md)
- [deployment and recovery](docs/deployment.md)
- [diagnostics](docs/diagnostics.md)

## Boundaries

Diamond rejects authority that the checked source and host configuration do not
both grant. Browser code cannot import server authority. Storage policies are
applied before protected data reaches application expressions. External HTTP,
files, jobs, account actions, and database operations use closed checked
interfaces rather than arbitrary shell, SQL, filesystem, or socket access.

The current qualified profile is Linux x86-64 with Rust 1.96, local Unix-socket
PostgreSQL, loopback Diamond servers behind the tested proxy, and
operator-provided shared storage. Mixed-version schema operation, public hosting,
SMTP, replicated object storage, arbitrary native plugins, shell execution, and
general-purpose network access are outside this release. Read the
[`release qualification`](docs/release-qualification.md) before deploying it.

## Verify the repository

Run the fast build gates and the executable example catalog with:

```sh
cargo fmt --all --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked
python3 verification/examples.py
```

The broader retained qualification is documented in
[`verification/web_ten_results.md`](verification/web_ten_results.md). Passing a
suite is bounded evidence for the tested profile, not proof of total security.

Diamond is published under the [MIT license](LICENSE).
