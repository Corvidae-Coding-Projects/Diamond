# Shared accounts and explicit HTTP APIs (W5)

Diamond 0.11.0 adds an opt-in `accounts-v1` identity profile for relational
PostgreSQL applications. Two application servers use the same account, session,
challenge, credential and rate tables. This is a loopback development profile;
TLS/proxy deployment, SMTP, uploads, outbound HTTP and durable jobs remain later
checkpoints. It does not establish production readiness.

## Run the reference application

```sh
cargo build --locked
mkdir -p .diamond/mail
chmod 700 .diamond/mail
export DIAMOND_DATABASE='host=/your/private/socket user=your_owner dbname=diamond'
target/debug/diamond project-init examples/accounts-workspace/diamond.toml --postgres-env DIAMOND_DATABASE
target/debug/diamond project-admin-add examples/accounts-workspace/diamond.toml --postgres-env DIAMOND_DATABASE --user alice --email alice@example.invalid
target/debug/diamond serve examples/accounts-workspace/diamond.toml --postgres-env DIAMOND_DATABASE --origin http://127.0.0.1:3000 --listen 127.0.0.1:3000 --identity-mailbox .diamond/mail
```

Provisioning prompts for the password; automated consumers may use
`--password-stdin`. Do not put passwords in command arguments. Use an owner role
for initialization and a separate nonowner runtime role with SELECT on metadata,
DML on managed resource tables, and SELECT/INSERT/UPDATE/DELETE on identity
users/sessions/challenges/credentials/rates. The runtime needs no schema CREATE or
object ownership. Grant schema USAGE explicitly. See the runnable qualification
fixtures for the exact exercised grants. The database connection remains a
trusted host grant: never expose it in the manifest or browser.

Open http://127.0.0.1:3000. A second node may listen on another loopback port using
the same database, public origin and mail directory. A routing proxy must preserve
the declared Host and Origin; forwarded authority headers are rejected. The
current native adapter accepts a local Unix PostgreSQL socket. Cookie requests
use Secure/HttpOnly/SameSite=Strict cookies, exact Origin and CSRF. Browser secure
context behavior for loopback is required.

The reference application uses invitation enrollment. Sign in as the provisioned
administrator and invite the recipient's email. Each delivered JSON file in the
private mail directory contains recipient, purpose and a one-use token. Register
using the invitation, then verify with the separate verification token. Tokens
are entered into page controls, never URLs. For registration, reset and password
change, the top Password field supplies the new password. Change also requires
Current password. A successful reset/change ends existing sessions and requires
fresh sign-in. Clear any copied credential when finished.

The development mail directory must already exist, belong to the server user,
and have mode 0700. Files are created exclusively with mode 0600. It is an
explicit local delivery grant, not an SMTP adapter. The operator may manually
relay a message through an appropriate channel. Keep the directory private and
outside tracked source. No real email is sent by this adapter. Remove obsolete
files deliberately; issuance refuses when the directory reaches its bounded
message count. Files carry plaintext one-use tokens and must be treated as
credentials. PostgreSQL stores their app/purpose-bound digests.

## Profile and lifecycle

```toml
[identity]
profile = "accounts-v1"
enrollment = "invite" # open or closed are also supported

# Optional. Omitting this table retains the secure 600/300/120 API defaults.
[identity.api_requests_per_minute]
global = 20000
peer = 10000
credential = 2000
```

API capacity overrides are declarative so an application cannot forget to
enforce admission control in one handler. Each value must be between 10 and
10,000,000 requests per database-clock minute, and the limits must be ordered
`global >= peer >= credential`. Raising them is an operational decision; keep
the defaults until measured traffic, edge controls, and database capacity
justify a reviewed override.

Registration creates a pending, nonadministrator account. Only successful
verification permits authentication. `closed` disables registration; trusted CLI
provisioning still works. Administrators are provisioned with
`project-admin-add`, never chosen by registration or a client role field.
Usernames are ASCII nominal IDs (1–128 bytes); email addresses use the documented
ASCII, case-folded development profile. Passwords require 12–256 UTF-8 bytes and
use the bounded Argon2id adapter. Existing `project-user-add` under this profile
is trusted development provisioning of a verified nonadministrator with an
`USERNAME@development.invalid` address; it is not public enrollment.

Browser events call the checked `session` methods below. All return Text; token
listing returns metadata encoded as JSON Text. These are HTTP POST routes under
`/_diamond/accounts/NAME`, with cookie identity, CSRF and exact Origin even for
anonymous enrollment/recovery. Bodies reject unknown/duplicate JSON fields.

| Name | JSON fields / positional browser arguments |
| --- | --- |
| register | username, email, password, invite (Text) |
| verify | token (Text) |
| resend / recover / invite | email (Text); invite requires administrator |
| reset | token, password (Text) |
| change_password | current_password, password (Text) |
| logout_all / list_tokens | no arguments; authenticated user required |
| disable | username (Text), disabled (Bool); administrator required |
| create_token | label (Text), operations (List<Text>), scopes (JSON Text in Diamond; object on HTTP), lifetime_seconds (Int in Diamond; canonical integer string on HTTP) |
| revoke_token | id (Text), owned by caller |

Registration, resend and recovery return the same generic acceptance for valid
known/unknown recipients. Requesting recovery changes no password or existing
credential. Verification/recovery challenges expire after 30 database-clock
minutes, bind app/purpose/recipient/account epoch, and redeem once atomically.
Resend invalidates previous challenges of that purpose. Invitation registration
consumes the invite and issues a separate verification challenge.

Password change/reset and disable/enable advance the account epoch: existing
sessions and API credentials cannot resurrect after reenablement. Logout ends
one session; logout-all ends all account sessions while retaining separately
managed API credentials. Sessions expire after 30 minutes. Each request consults
PostgreSQL; there is no fallback identity during an outage. An operation that
already holds an earlier serializable snapshot has W4's serial-order boundary;
revocation does not cancel an already running operation in real time.

Issuance commits before writing delivery files. Delivery failure after commit or
lost commit acknowledgment returns `outcome_unknown`; a database rollback must
not be inferred. The Diamond client retries only explicit pre-execution overload
rejections, with bounded backoff; unknown outcomes never enter that loop. Browser/proxy transport
can repeat an HTTP request after connection loss; qualification observed Chrome
repeat a zero-byte logout delivery and receive401 for the consumed session.
There is no general exactly-once HTTP guarantee. Explicitly request a new
challenge after checking state. This development sink is not a durable mail
queue. Generic normal responses are not a claim of indistinguishable timing or
of concealed service/delivery failure conditions.

## Versioned external operations

```toml
[http_api]
version = 1
[http_api.operations.read_issue]
function = "server.read_issue"
auth = "token"
# allow_unscoped = true  # only if intentionally exposing an unscoped resource
```

Bindings require an explicitly exported, reachable, wire-safe server function.
No Host effects are allowed. `auth="public"` allows anonymous read-only functions;
write effects reject at compile time. An unscoped resource requires explicit
`allow_unscoped=true`. Nothing reflects undeclared server functions.

`POST /api/v1/NAME` requires `Content-Type: application/json`,
`X-Diamond-API-Version: 1` and, for protected bindings,
`Authorization: Bearer dia_...`. The strict body is `{"arguments":[...]}` and the
success envelope is `{"api_version":1,"value":...}`. Cookie headers reject on
this profile, including mixed cookie/bearer requests. Token headers reject on
cookie/RPC routes. Public operations reject supplied bearer credentials. Origin,
Host, body/path/header limits and forwarded-header rejection still apply. There
is no CORS expansion or cookie fallback.

`GET /api/v1/_contract` and build `contract.json.http_api` describe declared
operations, reachable record/enum definitions, types and wire version 7. Int uses
a canonical signed decimal string in the i64 range. Decimal uses
`{"tag":"Decimal","value":"1000000"}` for 1.000000; Timestamp/Duration use their
own tags and signed millisecond strings. IDs use `{"tag":"workspace","value":"red"}`;
tags are nominal, not interchangeable. Option uses `{"tag":"None"}` or
`{"tag":"Some","value":...}`. Enums use `{tag,fields}`; records use exact field
objects. See [reactive values](reactive-apps.md) for the remaining codecs. JSON
numbers, extra fields and coercions cannot substitute for checked wire values.

Authenticated users issue credentials with a nonempty allowlist of 1–32 declared
protected operations, explicit scope IDs for exactly those operations' nominal
scope tags, and a 60–2,592,000 second lifetime. For example:
`{"label":"Reader","operations":["read_issue"],"scopes":{"workspace":["red"]},"lifetime_seconds":"3600"}`.
Scope sets contain 1–32 IDs per tag. Issuance returns raw secret once as `value`
and a separate nonsecret `id`. Listing exposes neither raw secret nor digest.
Revocation is owner-only. Account epoch, expiry, revocation, operation allowlist
and current resource policies are checked on every call.

A credential attenuates the owner's current authority. Every base relation in
queries/joins/aggregates is filtered before disclosure; get, mutation pre/post
state and return-read also enforce the scope. A restricted token cannot move a
row into another workspace even when its owner may do so. Declaring effects or
supplying a principal/role/cursor never creates authority.

Safe HTTP errors are 400 invalid input/version, 401 missing/stale credentials,
403 policy denial, 409 confirmed abort, 429 rate limit, and 503 unavailable or
unknown outcome. A live anonymous cookie receives 403 for protected RPC; a stale
cookie receives 401 under accounts-v1. No database text or account secret is
returned in errors. During identity transitions, competing event/input/navigation
changes cannot cancel the pending handler. Unknown identity outcomes and401
identity loss reset client component state before fresh sign-in. Unknown outcome and unavailable remain distinct error codes.

## Bounds and verification

Shared one-minute database-clock counters commit in a separate short transaction
before fallible identity/API work. Auth global/peer/subject caps are 120/100/12;
API caps default to 600/300/120 and can use the bounded declarative override
shown above. Login uses username and email operations use canonical
email as subject. Requests without an identity subject use global/peer limits.
API subjects are each bearer secret's digest, or one shared `public` bucket for
all anonymous operations. The peer is the actual TCP address, never a forwarded
header. These development defaults can deny busy shared clients; they are not a
production abuse-control configuration.

Per app bounds include 10,000 accounts, 4,096 live sessions, 20,000 retained
challenges and 4,096 rate buckets; active credentials are capped at 64 per user.
The request body is at most 16 KiB with a three-second read deadline. Each server
defaults to 16 admitted requests, four blocking workers and four pooled database
connections, plus 128 waiting requests with a one-second admission deadline.
Checked `[runtime]` limits and automatic overload backoff are documented in
[deployment](deployment.md#bounded-runtime-capacity); database deadlines and
cleanup retain the relational adapter's bounded behavior.

Native consumers use `add_postgres_administrator` and `serve_accounts` with
explicit connection and mailbox grants. Applications without `[identity]` retain
the declared development identity provider; there is no deployed-version upgrade
support. Stop servers for current-schema migration. See the [W5 contract](../verification/web_five.md)
and [verification evidence](../verification/web_five_results.md) for actual
coverage, retained regressions and remaining deployment/integration gaps.
