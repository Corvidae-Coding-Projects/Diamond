# W5 observable contract

Implement the approved W5 identity and explicit JSON API boundary, using one
current account/schema version. No old/new application coexistence is required.
Existing source applications without the new identity profile keep their declared
local development provider; new accounts/API behavior is opt-in and relational.

- `[identity] profile = "accounts-v1"`, `enrollment = "invite" | "open" | "closed"`
  selects an explicit enrollment policy. Administrators are provisioned through a
  trusted CLI/native operation, never through registration fields. Usernames are
  nominal IDs, unique email addresses are conservatively canonicalized ASCII.
  Passwords use the existing bounded Argon2id adapter. Pending/unverified,
  disabled and nonexistent accounts cannot authenticate or use API credentials.
- Admin invitations, registration, email verification/resend, password recovery,
  password change, account disable/enable, logout and logout-all use real shared
  PostgreSQL state. Unknown/known registration/recovery/resend requests have
  generic public acceptance responses; no account is changed merely by requesting
  recovery. Invalid input shapes and policies reject before protected writes.
- Verification/recovery/invitation secrets are random, scoped to app/purpose/
  recipient, stored only as digests in PostgreSQL, expire by database time, redeem
  once atomically and cannot be substituted across purposes or applications.
  Password change/reset and disablement invalidate all sessions and API credentials;
  enablement cannot resurrect them. Logout-all ends sessions but leaves separately
  managed API credentials intact. Membership remains authoritative per operation.
- A private explicitly granted directory is the real development delivery sink,
  shared by both test servers; no fake mail or fabricated application responses.
  Delivered messages necessarily carry plaintext one-use credentials, are owner-only
  files, and are never database plaintext, response/log/URL/browser-bundle secrets.
  Commit occurs before delivery; delivery failure cannot claim rollback. Resend
  creates a fresh challenge and invalidates the previous one. SMTP and durable
  external delivery are later adapters; no credentials are sent to real people.
- Shared rate decisions commit independently before fallible identity/API work.
  They apply across nodes and failures/rollback cannot erase failed-attempt counts.
  Keys, stored rows, input/hash cost, issuance/session counts, deadlines and worker
  admission are bounded. Authoritative-store failure grants no fallback identity.
- Authenticated users may create/list/revoke their API credentials. Raw credentials
  are shown only at issuance and digests persist. Scope is an explicit nonempty
  allowlist of declared API operations plus nominal workspace/resource scope IDs;
  expiry and account state are rechecked from PostgreSQL on every operation.
  Scope can only attenuate the owner's current resource policies, including base
  relations of joins/aggregates, get and mutation pre/post state. No client-supplied
  principal, global role or forwarded header supplies authority.
- `[http_api] version = 1` and explicit exported operation bindings expose only
  declared wire-safe server functions at `/api/v1/NAME`. Public operations are
  read-only; protected operations require bearer credentials. Cookie/RPC identity
  and token API credentials are separate profiles. No cookie fallback, anonymous
  mutation, cross-origin credential expansion, undeclared function reflection or
  raw SQL escape. Unknown fields/coercions, invalid versions and methods reject.
  Contract metadata documents Int64/Decimal/time/Id/Option/enum codecs and errors.
- Scope filtering runs before every base relation can disclose through fields,
  filtering, cursor position, count or join. Mutation pre/post state and return-read
  share the existing serializable request transaction. Concurrent revocation has
  W4's declared snapshot boundary; later requests on either server deny. Confirmed
  abort and unknown commit remain distinct and the Diamond client never starts an automatic application retry. Underlying
  browser/proxy transport can repeat an HTTP request after connection loss; no
  general exactly-once HTTP guarantee is implied.
- Browser account controls are authored in Diamond. Identity-changing operations
  supersede stale async replies and clear cached private application state.
  Test real registration/verification/recovery, scoped credential use, revoked
  access, malformed raw requests, replay/races, delivery/commit faults, restart,
  and two-node rate/revocation behavior through real interfaces and independent
  database observations. Record explicit bounds/gaps and exact final fingerprints.

The deployment boundary remains loopback HTTP and local Unix-socket PostgreSQL.
Public TLS/proxy deployment, uploads/outbound integrations and durable jobs remain
W6–W10 work. This checkpoint does not claim complete security or production capacity.

Primary guidance checked during implementation: OWASP [Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html),
[Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
and [REST Security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).
