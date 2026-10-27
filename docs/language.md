# Diamond language guide

This tutorial describes the Diamond 0.20.0 single-file profile. The
[canonical language contract](language-specification.md) and
[execution-profile matrix](language-profiles.md) define current semantics and
limits. The [project guide](projects.md) introduces modules through its historical
W1 snapshot; later checkpoint guides extend that foundation. The repository's
[architectural design](../design.md) includes proposed long-term behavior.

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
Unary minus is separate from the integer token: the direct minimum-Int literal
`-9223372036854775808` currently rejects. Use `-9223372036854775807 - 1` to
construct that value.

## Values and control flow

The implemented scalar types include `Int`, `Bool`, `Text`, nominal `Id`,
`Decimal`, `Timestamp`, and `Duration`. `Url`, typed `Html`, and `Response` belong
to server output boundaries. Records, nominal enums, `Option`, `Result`, and
immutable lists compose supported types within their profile and value budgets.

`match` must cover each enum, Boolean, Option, or Result case once. An arm can be
one expression or an indented block. `for item in values:` maps a bounded list to
a new list. `?` propagates supported `Result` failures; it does not catch internal
database, authority, or resource-limit failures.

Operators, from lower to higher precedence, are `or`, `and`, equality,
comparisons, addition/subtraction, and multiplication/division. Parentheses can
group expressions. Function calls and enum payloads use positional arguments;
record constructors use named fields. Arguments evaluate left to right.
An explicit `return` must be the final function-level statement; nested returns
inside match/iteration/transaction blocks are unsupported.

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
links and redirects may also use a simple same-page fragment such as
`#main-content`, as accepted by the shared internal-URL validator. This is separate
from reactive route declarations, which accept literal pathnames only.

`request.form<Record>()` decodes strict URL-encoded scalar form fields. Server
derived IDs, CSRF names, unknown fields, duplicates, malformed encodings, and
out-of-bound text fail before a mutation. With `session SecureCookie` and an
explicit `--auth` account database, the runtime supplies browser login, logout,
CSRF binding, and a verified user identity. These are loopback development
features, not a production authentication system.
