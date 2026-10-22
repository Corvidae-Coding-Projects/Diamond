# Independent W4 verification

`ORACLES.md` states the requirements and expected semantics independently of the
implementation. `model.py` specifies ordinary records and workspace memberships,
with deterministic seed `0xD1A04004`. `query_fixture.py` and `schema_fixture.py`
write actual Diamond projects. They contain no JavaScript, alternate web app,
authentication implementation, or expected response server.

Run after building the current program:

```sh
cargo build --locked
python3 verification/web_four_independent.py
```

The wrapper pins executable/library hashes, then runs real CLI schema checks,
typed scalar CRUD over generated RPC with a constrained runtime database role,
100,003-row query/model comparisons on two servers, actual PostgreSQL plan
capture, and explicit migration/interrupted-backfill/native-consumer workflows.
Each driver owns and stops its isolated PostgreSQL cluster and app processes.
Expected application values come from Python data, not Diamond outputs. The
query plan is captured from the actual server's PostgreSQL statement execution
and explained with the same synthetic parameter values.

The final combined JSON embeds the individual receipts. Earlier exploratory
receipts are separate and are not final-build qualification. Distinct discovered
product failures and their concrete replay fixtures are retained in `findings/`.
All data, accounts and passwords used by these drivers are disposable fixtures.
Runtime database credentials have DML only on application rows and session/rate
tables; schema metadata and users are read-only, and runtime has no schema-owner
or role-management authority.

Chrome UI controls, a deployment proxy, long-duration capacity, broad transport
fault injection and APIs/jobs/downloads/subscriptions from later checkpoints
are not established by these drivers. Root records the actual browser and
additional compatibility/failure exercises separately. No result here establishes
total security or production readiness.
