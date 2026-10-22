# W4 observable contract

Implement W4 from `.design/general-web-apps.md` without changing the SQLite
whole-relation integrity contract or silently removing W2 document limits.

- An explicit `relational-v1` project storage profile maps checked resource
  fields to constrained PostgreSQL columns, primary/unique/foreign keys and
  declared indexes. Scalar, optional, unit-enum, decimal/time and nominal ID
  fields are supported. Text includes Unicode and NUL losslessly; its physical
  storage encoding is versioned and validated. Unsupported shapes fail checking.
- Select/page/count/sum and declared one-hop joins apply every base resource's
  read policy in SQL before filtering, ordering, projection, pagination or
  aggregation can disclose values. No raw application SQL or full-table managed
  decoding is used to implement ordinary requests. Bounds and deterministic
  `(sort field, id)` cursor ordering are explicit; cursors never grant authority.
- Declared, purpose-limited principal lookups support resource ownership and
  workspace roles without granting general access to the lookup relation.
  Lookups are terminal, indexed and typed; arbitrary lookup chains/cyclic
  relationships are rejected. Unsupported SQL/policy translations fail closed.
  Compare SQL results against an independent policy model, including optional
  null equality, mixed identities, cross-workspace IDs, joins and counts.
- Mutations evaluate pre/post-state and return-read policies in one serializable
  transaction. Concurrent database aborts are distinguishable from unknown
  commit outcomes; no mutation automatically retries. Requests begun after a
  committed membership revocation observe it; earlier snapshot transactions may
  finish in a valid serial order or abort. Concurrent edits cannot lose updates.
- Connection admission and reuse are bounded. Only fully cleaned connections
  return to the pool; failed/uncertain transactions cannot contaminate another
  request. All owned work and drivers are drained or joined.
- One current storage schema version and shape hash are explicit. A migration
  coordinator serializes schema changes. Additive migrations and deterministic,
  bounded backfill batches commit progress for restart and resume. Unsupported
  destructive changes refuse. Old/new schema coexistence, compatibility ranges,
  rollback windows, and application-version leases are outside this checkpoint
  following the user's scope correction: there are no deployed users to upgrade.
- Provide explicit migration/backfill/status/integrity commands and a native
  public consumer. Startup rejects missing/incompatible metadata. Managed runtime
  credentials do not receive migration/schema-owner authority in qualification;
  fetched corruption fails, and an explicit bounded audit checks stored data.
- Operate a multi-workspace Diamond application through real generated RPC and
  Chrome. Seed at least 100,000 rows, inspect actual PostgreSQL query plans,
  independently compare pagination/count/join results, exercise allowed/denied
  writes, concurrent edits/revocations, two servers, explicit schema migration,
  interrupted backfill and commit/delivery faults. Retain concrete failures and
  fingerprints; no green suite implies complete security or production capacity.

W5 account enrollment/recovery/API tokens and W6–W10 services remain later work.
Local Unix-socket PostgreSQL and loopback serving remain the qualified deployment
boundary; W4 does not deploy infrastructure.
