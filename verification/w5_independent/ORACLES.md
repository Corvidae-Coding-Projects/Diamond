# Independent W5 qualification oracles

Written before execution from `verification/web_five.md`, the W5 plan row and
AC-5/6/10. These requirements, not a run's outputs, define the expected answers.
The driver operates the real Diamond CLI, generated contract, actual two-node
HTTP service and isolated PostgreSQL. Its mailbox is a private development sink;
no message goes to a real address. Identity addresses end in `.invalid`.

## Identity state and authority

Accounts transition `absent -> pending -> active -> disabled -> active` through
explicitly authorized operations. Registration cannot choose administrative
status. Invite/open/closed are separate enrollment policies. An absent, pending,
unverified or disabled account cannot create an authenticated session or API
credential. Requests for registration, recovery and resend return the same
public acceptance shape for known and unknown valid recipients; requesting
recovery changes neither password nor existing valid authentication.

A credential's secret is an opaque bearer value, never a principal/role supplied
by a caller. PostgreSQL stores its digest. Every challenge binds application,
purpose and recipient, has a database-clock expiry and one successful redemption.
A verification secret cannot reset a password or accept an invitation; a
recipient or app mismatch cannot consume it for its real recipient. Resend
invalidates the previous unredeemed secret. Expiry and consumed/invalid secrets
leave all protected state unchanged. Concurrent redemption permits at most one
success; no failure may resurrect or reapply the challenge.

Password reset/change and disablement revoke sessions and API credentials.
Enablement cannot resurrect them. Logout revokes its session. Logout-all revokes
all sessions while retaining separately managed API credentials. A request begun
after the revocation commits denies on either node. An earlier serializable
snapshot may finish in its declared serial order or distinctly abort; the
qualification does not demand real-time cancellation of an earlier transaction.
No positive identity cache or database failure may provide fallback access.

## External API and resource policy

Only `[http_api]` declared operation names under supported `/api/v1/NAME` routes
are callable. Every body is a strict declared wire contract. Unknown or duplicate
fields, coercions, incompatible versions, wrong method, malformed nominal IDs,
forged principal/role/forwarded authority and undeclared handlers cannot grant
access. Cookie authentication cannot substitute for bearer API credentials, and
API tokens cannot substitute for the cookie/RPC profile. Public exports must be
read-only. Cross-origin credentials are not silently accepted.

A token has an explicit nonempty operation allowlist and scope IDs. Its visible
relations are the intersection of (1) current account identity and membership,
(2) resource policy on every base relation, and (3) token operation and scope
limits. Scope restrictions precede filters, ordering, cursor position, count,
join and projection. The same rule protects get and mutation pre/post state,
and return-read. An unrestricted owner cannot use a restricted token to move a
record into/out of another scope or join a hidden right-hand base relation.
Changing role/membership can only remove or grant current owner authority; it
cannot expand the token's fixed operation or scope allowlists. Raw token values
are visible once at issuance, not list/readback. Expiry is checked at each call.

## Commit, delivery, rates and recovery

Challenge issuance commits before delivery. A delivery failure after commit is
not reported or classified as a rolled-back database operation. A response or
commit acknowledgment loss does not authorize implicit mutation replay. A
confirmed database abort is distinct from an unknown commit outcome. Actual
committed rows, not a response string, determine which side effects occurred.

Rate decisions are shared by both nodes, atomic, and commit independently before
fallible protected work. Alternating nodes or causing subsequent rollback does
not reset failed attempts. Limits are parameterized from the documented contract
before execution, not inferred from whichever threshold happens to pass.
Input/hash bounds, credential/session/issuance bounds, database deadlines and
worker admission must reject or backpressure without granting authority.

Restart of either owned node preserves committed accounts, challenges, scopes,
revocations and rates. Terminating the owned PG service produces unavailable
responses, no fallback grants, and recovery after explicit fresh requests.
All normal owned server shutdowns must exit zero, without forced kills/panics;
explicit kill scenarios record their expected signal and independently inspect
committed state. Shutdown errors fail qualification even after passed requests.

## Independent fixtures and bounded exploration

Seed `0xD1A05005`. Nominal identities are alice, bob, carol and outsider;
workspaces are red, blue and private. Distinct per-scope values expose routing
mistakes. Alice has two scopes, Bob one, Carol read-only, Outsider none. A join's
left row may be readable while its right row is denied, so authorizing only the
left base cannot pass. Direct sequences vary purpose/recipient/application and lifecycle ordering.
Generated inputs vary strict codecs, identities, operations and scope ID sets. Model
transitions are specified explicitly; they do not parse implementation policy
SQL or treat responses as expected values.

Retain each distinct failure as a concrete replay with prerequisites, expected
behavior, original observed result, exact final artifact fingerprints and final
replay result. Keep plaintext synthetic one-use/API/password secrets only in
owned temporary resources. Receipts retain secret fingerprints, never values.
No 100-client/30-minute capacity, TLS/proxy, SMTP, uploads or jobs claim is made.

## Protocol clarifications before final qualification

- A stale/revoked cookie returns401. A current anonymous cookie returns403 for
  protected RPC. Identity responses may clear the caller's cookie; the raw driver
  honors deletion and fetches a fresh session/CSRF after changing identity.
- PostgreSQL's password column is `password_hash` (the initial metadata message
  called it `hash`). This affects only the observation adapter, not an oracle.
- Undeclared credential operations are authority denials403. Invalid credential
  field shapes are400. Neither may issue a credential.
- Authentication subject limits use declared username/email fields. Bodyless
  account operations have global/peer limits and issuance bounds. API subjects
  are token digests, or one shared literal public subject. Caps are auth
  global120/peer100/subject12 and API global600/peer300/subject120 per minute.
  Peer/global qualification uses distinct real token subjects and loopback source
  addresses so a narrower subject cap does not mask those bounds.
- An API operation touching an unscoped resource without `allow_unscoped=true`
  is rejected during checking. The driver therefore uses a compile-negative
  case for this gate and operates a separately explicit allowed binding.
