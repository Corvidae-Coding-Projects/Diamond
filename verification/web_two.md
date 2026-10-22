# W2 observable contract

Written before implementation. W2 is the first connected local full-stack slice,
not completion of W3–W10 or a production deployment profile.

- A checked project explicitly declares its public RPC names and exported server
  functions. Only bounded scalar wire signatures are accepted initially. The
  versioned codec uses decimal strings for Int64, JSON booleans and JSON text;
  unknown fields, coercions, incompatible contracts and undeclared operations
  fail before application evaluation. Private/server source stays off HTTP and
  out of client assets. Browser imports still grant no server authority.
- Server modules can declare typed records/resources and Diamond policies.
  Application and module DbRead/DbWrite budgets propagate through the closed
  call graph. A separate explicit connected PostgreSQL grant is required.
  PostgreSQL CRUD enforces the same checked policy evaluator; owner identities
  and resource IDs are server-derived. Policies and protected mutations share
  one transaction. Caught failures poison that transaction; uncertain commit is
  never reported as a confirmed abort, and mutations are never auto-retried.
- A minimal tracker keeps create/read/update/delete business handlers and
  ownership policies in Diamond. Its compiled component has bound text/password
  inputs and serialized asynchronous events calling generated RPC. An error
  preserves prior component state, reports a safe error, and allows a later
  event. A failed mutation response cannot promise no server-side change.
- Explicitly provisioned Argon2id accounts, hashed opaque sessions, expiry and
  logout live in PostgreSQL and work across processes without sticky routing.
  Every RPC obtains current identity from that store. Login rotates the session;
  cookie-authenticated changes require the configured exact origin and a
  session-bound CSRF token. Cookies are Secure, HttpOnly, SameSite=Strict and
  host-only. Registration/recovery/API credentials remain W5.
- Run two real Diamond processes behind a local proxy with a single isolated
  PostgreSQL server. Sign in through A, create through B, read/edit through A,
  restart B and retrieve the same committed record. A second user and anonymous
  requests cannot read/edit/delete it; denial and invalid input leave independent
  database observations unchanged. Exercise the actual browser controls too.
- Check body/argument bounds, origin/CSRF/contract/identity forgery, missing grants,
  private dispatch, Unicode/injection text, multiple resources/users, concurrent
  updates, transaction failure/recovery, revocation and restart persistence.
  Preserve discovered failures and replay relevant W1/legacy compatibility.

W2 uses a versioned, bounded PostgreSQL document-row storage profile and reference
policy evaluation before selection/pagination. It does not claim W4's relational
schema planner, joins, query optimization, migration/backfill system or pools.
Connections and server listeners remain local-only; production TLS, proxy and
deployment configuration arrive in later checkpoints. Out-of-band database
tampering and native-host compromise remain outside managed-storage assumptions.
