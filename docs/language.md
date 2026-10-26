# Diamond language guide

This guide describes the Diamond 0.16.0 single-file compatibility profile. The
[project guide](projects.md) describes modules and the project/browser profile.
The repository's [architectural design](../design.md) has a broader long-term
surface, while the [README boundaries](../README.md#boundaries) define the current
implemented release.

## Source structure

Diamond is indentation-sensitive. Use spaces, keep each block aligned with its
enclosing indentation level, and do not use tabs. Blank lines and `#` comments
do not change block structure. Names use ASCII letters, digits, and underscores;
the first character is a letter or underscore.

Top-level declarations are:

```diamond
type UserId = Id<"user">

type Note:
  id: Id<"note">
  title: Text<length: 1..120>
  visible: Bool = true

enum State:
  Ready
  Waiting(reason: Text)

resource Notes: Note

fn main() -> Text:
  "ready"
```

`app`, `policy`, `route`, and `public route` connect host authority and HTTP.
Function result annotations are required. Local `let` bindings are immutable.
Decimal integers are signed 64-bit values; one underscore may separate adjacent
digits, as in `1_000`. Arithmetic is checked.

## Values and control flow

The implemented scalar types are `Int`, `Bool`, `Text`, nominal `Id`, `Url`, and
typed `Html`. Records, nominal enums, `Option`, `Result`, and immutable lists
compose them within the documented depth and value-work budgets.

`match` must cover each enum, Boolean, Option, or Result case once. An arm can be
one expression or an indented block. `for item in values:` maps a bounded list to
a new list. `?` propagates supported `Result` failures; it does not catch internal
database, authority, or resource-limit failures.

Operators, from lower to higher precedence, are `or`, `and`, equality,
comparisons, addition/subtraction, and multiplication/division. Parentheses can
group expressions. Function and record calls use checked positional or named
arguments according to their declarations.

See [language.dia](../examples/language.dia) for exhaustive matching, payload
bindings, Option/Result recovery, lists, and iteration. The
[function catalog](../examples/functions/README.md) gives a runnable example for
every built-in utility and checked callable family.

## Authority, storage, and policy

An application declaration states the maximum database effects source may use:

```diamond
app Storage:
  database SQLite
  provides DbRead<Notes>, DbWrite<Notes>
```

This declaration does not grant host access. The CLI must also receive
`--database PATH`. Each resource binds a repository name to a record. Read,
create, update, and delete policies are restricted checked predicates and default
to denial when the required action is absent. Route and function effects are
computed transitively.

Repository operations return `Result` for ordinary data failures. Explicit
`transaction db:` blocks group related writes; a failed operation poisons the
transaction even if source matches the returned error. The runtime reports an
opaque uncertain outcome if it cannot establish whether a commit happened.

Selections accept named scalar equality filters plus required `limit` from 1 to
100 and optional `offset` from 0 to 10,000. Authorization is applied before
ordering and pagination.

## Routes, forms, and typed HTML

Routes use literal absolute paths and typed parameters:

```diamond
public route GET "/notes/{id:NoteId}" as Details:
  html:
    <main><h1>Note {id}</h1></main>
```

`route_url(Details, id)` constructs a checked internal URL. Typed HTML accepts
the allowlisted tags and attributes implemented by the template parser. Text
interpolation is escaped; values are never reinterpreted as markup. Static links
and redirects must be absolute internal paths without percent encoding. Static
links may also use a simple same-page fragment such as `#main-content`;
fragments stay in the current application route.

`request.form<Record>()` decodes strict URL-encoded scalar form fields. Server
derived IDs, CSRF names, unknown fields, duplicates, malformed encodings, and
out-of-bound text fail before a mutation. With `session SecureCookie` and an
explicit `--auth` account database, the runtime supplies browser login, logout,
CSRF binding, and a verified user identity. These are loopback development
features, not a production authentication system.
