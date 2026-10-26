# Checked graph queries

Diamond query documents expose a bounded, inspectable read plan over policy-aware
resources. They are deliberately narrower than arbitrary resolver frameworks: a
resolver is exactly one repository `page` or configured `join` expression.

Write an exported, same-named RPC function:

```diamond
export fn document_graph(after: Text, limit: Int) -> Result<List<Document>, DataError>:
  documents.page(order: "id", descending: false, after: after, limit: limit)
```

Bind it as an RPC and query document:

```toml
[rpc]
document_graph = "server.document_graph"

[queries.document_graph]
resources = ["Documents"]
selection = ["id", "owner", "title"]
cursor_argument = "after"
limit_argument = "limit"
max_depth = 1
max_nodes = 400
max_rows = 100
max_cost = 400
max_queries = 1
timeout_ms = 5000
max_response_bytes = 65536
```

The compiler rejects a missing/differently named RPC, arbitrary helper calls,
multiple statements, `select` with offset pagination, writes, host effects,
undeclared reads, and cursor/limit parameters not wired directly to `after` and
`limit`. The result must be a list of records composed only of records, lists,
options, and wire-safe scalar leaves. Recursive graphs and enums are rejected in
this profile.

`selection` must exactly equal the scalar leaf paths derived from the result type.
For nested records, paths use dots such as `author.name`. This makes field
visibility a checked type property: a resolver cannot fetch hidden fields and rely
on application code to strip them later. A configured relational `join` may form a
nested graph, but its exact read effects must equal `resources`.

The static node floor is `max_rows * (selected leaves + 1)`. Static cost multiplies
that by graph depth. The document may narrow but cannot exceed depth 8, 10,000
nodes, 100 rows, cost 100,000, one database query, the runtime's fixed five-second
PostgreSQL statement timeout, or 1 MiB encoded output. Runtime also bounds cursors
to 512 bytes, requires canonical `Int` limits in `1..max_rows`, counts repository
operations, and checks actual encoded rows, nodes, depth, and bytes.

The repository operation performs authorization in SQL using the current cookie
principal or attenuated API principal. The query wrapper cannot opt out. If the
same resolver is also exposed through `[http_api]`, Diamond finds its query plan by
checked function identity and applies the same argument, database-query, and result
budgets there.

`contract.json` publishes every query's resources, exact selection, pagination
arguments, and ceilings. That is safe bounded introspection; it exposes no data and
does not weaken resource policy.
