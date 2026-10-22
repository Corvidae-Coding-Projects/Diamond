# Relational function example

This checked project keeps every PostgreSQL repository call in one small server
module. [`server.dia`](server.dia) demonstrates:

- `create`, `get`, `update`, and `delete`;
- indexed filtered `select`;
- ordered `page` and matching `cursor`;
- authorized `count` and integer `sum`;
- a configured relationship `join`.

[`ui.dia`](ui.dia) shows generated RPC plus `session.login`, `session.user`, and
`session.logout`. [`diamond.toml`](diamond.toml) shows the storage scopes, index,
reference, join, identity profile, module budgets, and RPC declaration that make
those calls available.

Check and build without opening PostgreSQL:

```sh
target/debug/diamond check examples/functions/relational/diamond.toml
target/debug/diamond build examples/functions/relational/diamond.toml \
  --out .diamond/relational-functions
```

To run it, create a disposable local PostgreSQL database and follow the
[relational application setup](../../../docs/relational-apps.md). This source is a
method-focused gallery; the complete browser journey is
[`live-workspace`](../../live-workspace/).
