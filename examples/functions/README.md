# Diamond function examples

This directory is a runnable catalog of every fixed callable surface currently
available to Diamond source. Start with the pure files: they need no database,
network, account, or browser.

```sh
cargo build --locked

target/debug/diamond run examples/functions/basics.dia
target/debug/diamond run examples/functions/text.dia
target/debug/diamond run examples/functions/lists.dia
target/debug/diamond run examples/functions/decimals.dia
target/debug/diamond run examples/functions/time.dia
```

Every file is deliberately small enough to copy and alter. Run `diamond check`
before `diamond run` while experimenting; checking never opens a database or
starts a server.

## Pure functions

| Function | Signature | Example |
| --- | --- | --- |
| `int_text` | `Int -> Text` | [`basics.dia`](basics.dia) |
| `id_text` | `Id<T> -> Text` | [`basics.dia`](basics.dia) |
| `join_text` | `List<Text>, Text -> Text` | [`basics.dia`](basics.dia) |
| `text_slice` | `Text, Int, Int -> Text` | [`text.dia`](text.dia) |
| `text_contains` | `Text, Text -> Bool` | [`text.dia`](text.dia) |
| `text_trim` | `Text -> Text` | [`text.dia`](text.dia) |
| `text_replace` | `Text, Text, Text -> Text` | [`text.dia`](text.dia) |
| `list_append` | `List<T>, T -> List<T>` | [`lists.dia`](lists.dia) |
| `list_reverse` | `List<T> -> List<T>` | [`lists.dia`](lists.dia) |
| `list_slice` | `List<T>, Int, Int -> List<T>` | [`lists.dia`](lists.dia) |
| `list_get` | `List<T>, Int -> Option<T>` | [`lists.dia`](lists.dia) |
| `decimal_units` | `Int -> Decimal` | [`decimals.dia`](decimals.dia) |
| `decimal_text` | `Decimal -> Text` | [`decimals.dia`](decimals.dia) |
| `decimal_add` | `Decimal, Decimal -> Decimal` | [`decimals.dia`](decimals.dia) |
| `decimal_subtract` | `Decimal, Decimal -> Decimal` | [`decimals.dia`](decimals.dia) |
| `decimal_multiply` | `Decimal, Decimal, Text -> Decimal` | [`decimals.dia`](decimals.dia) |
| `decimal_divide` | `Decimal, Decimal, Text -> Decimal` | [`decimals.dia`](decimals.dia) |
| `timestamp` | `Int -> Timestamp` | [`time.dia`](time.dia) |
| `duration` | `Int -> Duration` | [`time.dia`](time.dia) |
| `time_millis` | `Timestamp -> Int` | [`time.dia`](time.dia) |
| `duration_millis` | `Duration -> Int` | [`time.dia`](time.dia) |
| `timestamp_add` | `Timestamp, Duration -> Timestamp` | [`time.dia`](time.dia) |
| `timestamp_diff` | `Timestamp, Timestamp -> Duration` | [`time.dia`](time.dia) |

Decimal values have six fractional digits. Multiplication and division accept
`"half_even"` or `"half_away"`. Text/list slices use zero-based start and count,
reject invalid bounds, and never silently clamp. Text indexes and `len(Text)`
count Unicode scalar values.

## Constructors and general helpers

[`basics.dia`](basics.dia) shows all ordinary call shapes:

- user functions such as `greeting(person)`;
- record constructors with named fields, such as `Person(name: "Ada", age: 36)`;
- nominal ID constructors such as `UserId("ada")` and conversion with `id_text`;
- enum constructors with and without payloads;
- `Some(value)` and `None` for `Option<T>`;
- `Ok(value)` and `Err(error)` for `Result<T, E>`;
- exhaustive `match`, record field access, immutable `let`, and `error_message`.

[`text.dia`](text.dia) and [`lists.dia`](lists.dia) show both accepted forms of
`len(value)`. The list example also shows bounded `for` mapping. Arithmetic,
comparison, `and`, `or`, and `not` are operators rather than functions.

## Web helpers

[`web_helpers.dia`](web_helpers.dia) is a checked server-rendered application and
shows:

| Call | Purpose |
| --- | --- |
| `route_url(RouteHandle, values...)` | Construct a typed internal URL. |
| `request.form<Record>()` | Strictly decode a form into a record. |
| `request.is_user(user_id)` | Compare a verified request identity with an ID. |
| `bad_request(html)` | Return typed HTML with HTTP status 400. |
| `error_message(data_error)` | Render the safe public text of a `DataError`. |

`request.authenticated` and `request.user_id` are verified fields rather than
functions. `redirect value` is a route statement. Try the app with:

```sh
target/debug/diamond run examples/functions/web_helpers.dia \
  --listen 127.0.0.1:3000
```

Then open http://127.0.0.1:3000. Private `/account` requires a configured account
provider. Exercise typed form decoding directly with:

```sh
curl --data 'message=Hello from Diamond' http://127.0.0.1:3000/echo
```

## Storage functions

[`storage_methods.dia`](storage_methods.dia) demonstrates the complete SQLite
repository surface: `create`, `get`, `update`, `select`, and `delete`. It also
shows `transaction db:` and `?` propagation.

```sh
mkdir -p .diamond
target/debug/diamond db-init examples/functions/storage_methods.dia \
  --database .diamond/function-examples.sqlite
target/debug/diamond run examples/functions/storage_methods.dia \
  --database .diamond/function-examples.sqlite
```

The first run prints `Updated title | selected 1`. The example deletes its row
before committing, so it can be run repeatedly. Source effects declare the
maximum authority; `--database` supplies the separate host grant.

[`relational/`](relational/) is a complete checked PostgreSQL project. Its server
module shows `create`, `get`, `update`, `delete`, indexed `select`, ordered `page`,
`cursor`, `count`, integer `sum`, and configured `join`. Its browser module shows
generated `rpc.operation(...)` calls and `session.login`, `session.user`, and
`session.logout`.

```sh
target/debug/diamond check examples/functions/relational/diamond.toml
target/debug/diamond build examples/functions/relational/diamond.toml \
  --out .diamond/relational-functions
```

PostgreSQL execution needs an initialized disposable database and account, as
described in the [relational guide](../../docs/relational-apps.md).

## Authority-bound project functions

[`authority/`](authority/) is the checked syntax catalog for calls that require
explicit native grants. It contains:

- `host.postgres_version()` and `host.postgres_echo(text)`;
- `host.secret(destination)`, `host.http_get<T>(...)`, and
  `host.http_post<Body, Reply>(...)`;
- `host.job_submit<T>(...)`, `host.job_status(...)`, `host.job_cancel(...)`, and
  worker-only `host.job_key()`;
- `host.event_emit<T>(...)`;
- `files.upload(...)`, `files.status(...)`, `files.download(...)`, and
  `files.delete(...)`;
- the complete account browser surface: `session.register`, `verify`, `resend`,
  `recover`, `reset`, `change_password`, `logout_all`, `invite`, `disable`,
  `create_token`, `list_tokens`, and `revoke_token`, plus login/user/logout;
- generated `rpc.name(...)` calls and a typed `<watch>` subscription.

Check or build it without granting runtime authority:

```sh
target/debug/diamond check examples/functions/authority/diamond.toml
target/debug/diamond build examples/functions/authority/diamond.toml \
  --out .diamond/authority-functions
```

Running those operations requires PostgreSQL, an accounts profile, shared object
storage, and an explicit destination grant. Use the complete
[`live-workspace`](../live-workspace/) and [`catalog`](../catalog/) journeys for a
working environment rather than putting credentials into these source examples.

## Policy and component callables

Policy expressions use `principal.is_user(id)`, `principal.has_role(.Role)`,
`principal.is_related("Lookup", scope)`, and
`principal.has_role_in("Lookup", scope, .Role)`. See
[`message_board.dia`](../message_board.dia) for local roles and
[`live-workspace/server.dia`](../live-workspace/server.dia) for relationship
lookups. `principal.authenticated` is a field.

Component `state`, `derived`, `on`, `<when>`, `<each>`, `<route>`, `<use>`,
`<watch>`, `bind-value`, and `bind-checked` are language constructs, not callable
functions. The smallest component is [`foundations/counter.dia`](../foundations/counter.dia);
the complete set is exercised by [`workspace/tracker.dia`](../workspace/tracker.dia)
and [`live-workspace/tracker.dia`](../live-workspace/tracker.dia).

There is no dynamic function lookup, reflection, arbitrary SQL, shell execution,
filesystem API, or unrestricted network function. If a call is absent from this
catalog and the checked application manifest, the compiler rejects it.
