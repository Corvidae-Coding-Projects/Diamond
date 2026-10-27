# W4 relational data and authorization

**Historical W4 guide.** Statements below about W5–W10 remaining are the
boundary of the original W4 checkpoint, not the current implementation. See the
[canonical language contract](language-specification.md) and
[profile matrix](language-profiles.md) for the LP8-qualified surface.

Diamond 0.10.0 adds an opt-in `relational-v1` PostgreSQL profile. It stores checked
fields in typed columns and executes supported queries in SQL. Ordinary requests
do not load and validate every row. The SQLite adapter and the bounded W2
PostgreSQL document adapter remain separate choices for source applications.
There is no old/new schema coexistence or upgrade compatibility requirement:
stop all application servers, migrate explicitly, and restart the current build.

The reference application is `examples/relational-workspace/diamond.toml`. It
creates workspaces, projects, scoped editor memberships and issues. It renders
10-row cursor pages, authorized totals and issue/project joins in Diamond.
Accounts still use explicit development provisioning; enrollment, invitations,
recovery, public APIs and the other W5–W10 features remain later work. Unix-socket
PostgreSQL and loopback HTTP remain the qualified deployment boundary.

## Run the application

Use an existing local PostgreSQL cluster and a database dedicated to the app.
Keep the migration grant separate from runtime credentials. Environment values
are private connection strings, not application manifest values or build inputs.

```sh
cargo build --locked
export DIAMOND_MIGRATION_POSTGRES='host=/your/private/socket dbname=diamond user=diamond_migrator'
target/debug/diamond project-init examples/relational-workspace/diamond.toml --postgres-env DIAMOND_MIGRATION_POSTGRES
printf '%s\n' 'a disposable development password' | target/debug/diamond project-user-add examples/relational-workspace/diamond.toml --postgres-env DIAMOND_MIGRATION_POSTGRES --user alice --password-stdin
```

The migrator must own the managed schemas. Create a distinct PostgreSQL runtime
role with `USAGE` on `diamond_w2` and `diamond_w4`; grant only `SELECT` on
`diamond_w2.apps`, `diamond_w2.users` and `diamond_w4.apps`, and
`SELECT, INSERT, UPDATE, DELETE` on `diamond_w2.sessions`, `diamond_w2.rates` and
this application's generated resource tables. It needs no schema `CREATE`,
metadata writes, superuser, database creation, role creation or migration rights.
Use separate databases/roles for independently trusted apps. The resource table
name is `diamond_w4.r_<first 16 app-hash characters>_<first 16 resource-hash characters>`;
both hashes are SHA-256 of the UTF-8 application/resource names. Inspect the
schema as the owner to grant the specific tables; do not grant all tables to a
runtime role, because that would include migration metadata. Regrant new resource
tables after an explicit migration.

```sh
export DIAMOND_RUNTIME_POSTGRES='host=/your/private/socket dbname=diamond user=diamond_runtime'
target/debug/diamond serve examples/relational-workspace/diamond.toml --listen 127.0.0.1:3000 --origin http://localhost:3000 --postgres-env DIAMOND_RUNTIME_POSTGRES
```

Open http://localhost:3000, sign in, enter a name and create a workspace, then a
project. Create issues, page through them, and grant or revoke another provisioned
user's editor membership. Workspace/project IDs are nominal checked IDs. A second
server can share the database and exact public origin behind a local proxy. The
runtime neither grants database roles nor discovers database credentials.

## Declare storage

Every resource must have a configuration entry. Names are checked source names;
`output` can be an entry-module record name or `module.Record` in the server graph.

```toml
[storage]
profile = "relational-v1"
version = 1

[storage.resources.Issues]
scope = "workspace"
indexes = [["workspace", "rank"]]

[storage.resources.Issues.references.project]
resource = "Projects"
fields = { workspace = "workspace", project = "id" }

[storage.resources.Projects]
scope = "workspace"
unique = [["workspace", "id"]]

[storage.joins.IssueProjects]
resource = "Issues"
reference = "project"
output = "IssueProject"
```

The join output record must contain exactly `left: Issue` and `right: Project`.
Relationships target a primary or declared unique key, have matching nominal
scalar types, and bind both scopes when both resources declare a scope. Nullable
local references use PostgreSQL's ordinary `MATCH SIMPLE` behavior: a missing
component means no parent reference. Cyclic references and arbitrary/deep joins
are rejected. Resource references do not implicitly grant parent read access.

Supported fields are Int, Bool, Text with optional scalar-length bounds, nominal
Id, Decimal, Timestamp, Duration, unit-only enums and one Option layer around a
scalar. Lists, nested Options, payload enums and record-valued columns reject at
checking. Numbers use signed BIGINT; Decimal stores millionths and time values
store the existing integer units. Text uses lowercase UTF-8 hex in `TEXT COLLATE
"C"`: it preserves NUL and exact Unicode sequences, and lexical order agrees with
Diamond. Database checks validate encoding, bounds, nominal-ID shape and enum
values. Option uses SQL NULL. Fetched rows are decoded and validated again.

The profile allows at most 64 resources, 64 columns per resource, 32 declared
indexes/keys/references per resource and four columns per key. Indexed Text must
be bounded; a declared key's conservative encoded maximum is 1,800 bytes. Ordinary
indexes append `id` to support deterministic ties. Unique keys use PostgreSQL's
ordinary distinct-NULL semantics. A unique key must contain its scope; unscoped
or cross-scope uniqueness needs explicit `global_unique = true`. This acknowledges
availability disclosure across the resulting key domain. Foreign-key and unique
violations return the same safe denial class as policy/absence failures. A caller
permitted to propose a write can observe whether its scoped constraints succeed;
applications needing tighter existence secrecy must restrict that write policy.

## Scoped policy lookups

```toml
[storage.lookups.Membership]
resource = "Members"
scope = "workspace"
principal = "user"
role = "role"
purpose = "Permit workspace reads and role-limited edits without exposing the directory."
```

Use `principal.is_related("Membership", row.workspace)` or
`principal.has_role_in("Membership", row.workspace, .Editor)` in a resource policy.
The scope field must be a nonoptional nominal Id, principal an `Id<"user">`, and
role a unit enum or bounded Text. The lookup name/role are checked literals.
An index must begin with scope and principal (a primary-ID scope is already
indexed). The lookup tests only the verified principal, exact scope and optional
role. It is terminal and purpose-limited: it does not recursively run the lookup
resource's policy, return its rows or grant general `DbRead` on it. Control writes
to membership resources with their own policies, for example an owner lookup.
Anonymous callers cannot satisfy membership. Connected RPC itself requires a
signed-in session, even for a function whose source policy would admit anonymity.

Every query base relation has its own read policy in SQL, including the right side
of a join. User filters, sorting, pagination, aggregates and returned projections
cannot include rows excluded by those policies. Scalar optional equality uses
Diamond's two-valued semantics: absence equals absence. Application code cannot
inject SQL or invoke arbitrary application functions from a policy.

## Query methods

```text
issues.select(workspace: w, rank__gte: 0, limit: 20, offset: 0)
issues.page(workspace: w, order: "rank", descending: false, after: cursor, limit: 20)
issues.cursor(row: last_row, order: "rank", descending: false)
issues.count(workspace: w)
issues.sum(workspace: w, field: "rank")
issues.join(relation: "IssueProjects", workspace: w, order: "rank", after: cursor, limit: 20)
```

All return `Result<..., DataError>`: select/page return lists of resource records,
join a list of configured output records, cursor Text, and count/sum Int. Equality
filters use field names; `__gt`, `__gte`, `__lt`, `__lte` are supported range
suffixes. Filters require an index prefix; page ordering requires its field after
fixed equality-prefix fields. Residual supported filters remain SQL predicates.
Unsupported selectors/query shapes fail checking. Query selectors `order`,
`field` and `relation` must be source literals. These names and the other query
argument names cannot be resource columns.

Limits are mandatory and range from 1 through 100; only select accepts offset,
from 0 through 1,000. Ordered cursor fields are nonoptional Int, numeric/time,
Text or Id; Bool and optional ordering reject. Ordering is `(field, id)`, ascending
by default, with both reversed for descending pages. Start with `after: ""` and
obtain the next position from the last returned row using matching order/direction.
An empty list is the end. A cursor is a bounded hex-encoded versioned JSON
position containing resource, order, direction, key and ID; it is not a secret,
a signed authority token or a stored authorization decision. All policies and
filters run again, so a changed identity/scope cannot inherit cursor authority.
Cursor traversal is a live view across requests, not a cross-request snapshot;
concurrent insert/delete/reorder may change later pages. Each request's counts,
pages and mutations share its transaction snapshot. Sum is Int-only, empty sums
are zero, and overflow rejects rather than wrapping.

## Transactions and connection ownership

Each W4 RPC uses one PostgreSQL serializable transaction. Mutations lock their
target rows and evaluate pre/post-state and return-read policies in that same
transaction. A caught database/policy failure makes the whole request unable to
commit. Cross-row policy reads participate in PostgreSQL's serialization checks.
A committed revocation is visible to requests starting afterward on either node;
an earlier transaction may complete in a valid serial order before revocation or
abort. This is the database's serializable guarantee, not a promise that commit
order equals authorization order. See the [PostgreSQL isolation documentation](https://www.postgresql.org/docs/current/transaction-iso.html).

Confirmed serialization/deadlock aborts become `Error::Conflict` (`RetryableAbort`
when transported as a typed DataError, HTTP 409 when propagated). Other deliberate
rollbacks are aborted/denied/input failures. Loss of the COMMIT acknowledgment is
`OutcomeUnknown`; it carries an operation identifier and is never automatically
replayed. HTTP delivery loss can also leave a committed mutation whose reply was
not received. Reconcile current data before deciding to retry. W4 never retries
a mutation automatically.

Each server admits at most 16 owned requests and four blocking workers. W4 owns
at most four reusable PostgreSQL connections per server; admission waits at most
five seconds. Connection setup and individual statements have five-second
limits, lock waits two seconds, and transaction idle/request deadlines ten
seconds. Only confirmed rollback cleanup returns a healthy connection to the
pool; uncertain/broken drivers are destroyed and joined. Graceful shutdown joins
owned work and destroys the pool outside the async runtime. These are resource
ceilings, not a production throughput or availability claim.

## Explicit migration, backfill and integrity commands

```sh
target/debug/diamond project-status path/diamond.toml --postgres-env DIAMOND_MIGRATION_POSTGRES
target/debug/diamond project-migrate path/diamond.toml --postgres-env DIAMOND_MIGRATION_POSTGRES
target/debug/diamond project-backfill path/diamond.toml --postgres-env DIAMOND_MIGRATION_POSTGRES --batch 100
target/debug/diamond project-audit path/diamond.toml --postgres-env DIAMOND_MIGRATION_POSTGRES
```

Stop all application servers before migrating. Increment `storage.version` by
exactly one; the migration coordinator serializes initialization, migration and
backfill. Supported expansion adds resources, nullable/defaulted fields,
constraints or indexes; removing/changing existing fields, defaults, keys or
relationships rejects. No destructive reset or raw migration SQL fallback exists.
Startup requires the exact current version and physical shape hash. Source builds
are reproducible; rebuild from trusted sources for 0.10.0/wire version 3.

For a new optional field, declare a literal backfill:

```toml
[storage.backfills.priority]
resource = "Issues"
field = "priority"
value = 10
```

The field must already be `Option<Int>` (or another supported scalar Option) in
the checked current schema. Each command commits at most the requested 1–1,000
ID-ordered rows for one unfinished plan. It changes only NULL values, preserving
existing values. Progress and data commit together; rerun until all plans report
done. A committed batch advances even across non-NULL rows. Killing an unfinished
batch rolls back its data and checkpoint. Changed plan contents require a new
plan name. New out-of-order inserts during/after a backfill are outside this
stopped-application migration workflow; initialize new writes appropriately.

The audit scans bounded 100-row batches under one repeatable-read snapshot,
validates every field, checks total scanned rows and compares the managed schema
catalog against its initialization/migration fingerprint. It detects removed or
changed constraints, indexes, triggers and columns as well as invalid fetched
values. It is a deliberate full audit, not an ordinary request scan. Schema-owner
or metadata tampering is outside the trusted managed-storage boundary; someone
who can rewrite both the baseline and schema can defeat that comparison. Concurrent
out-of-band database writers are also outside the managed contract. The SQL
optimizer remains free to choose a sequential scan for a legitimate aggregate;
that does not imply full-table managed decoding.

Native consumers use `CheckedProject::{postgres_status, migrate_postgres,
backfill_postgres, audit_postgres}` with an explicit connection grant; each
returns a JSON report string or an error. Initialization, account provisioning,
and serving retain their existing public methods. See the [W4 contract](../verification/web_four.md)
and final evidence for the exact exercised boundary.
