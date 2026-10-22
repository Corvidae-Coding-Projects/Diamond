# W6 behavioral contract

W6 adds explicit runtime grants for private shared files and named outbound HTTP.
This is a bounded loopback development profile, not a production deployment claim.

## Files

The `shared-fs-v1` adapter requires a private, persistent directory mounted on all
application servers. PostgreSQL stores the directory identity and attachment
metadata. A different directory identity must fail closed. There is no implicit
local-disk fallback. The operator must provide the shared mount and its durability.

A manifest `[files]` section selects bindings to existing relational resources.
Read access follows the parent record's read policy; creating, finalizing, and
deleting follow its update policy. Upload ownership never bypasses that policy.
Accounts and relational storage are required. File names are metadata only; object
paths contain generated identifiers. Downloads are attachment/octet-stream with
nosniff. Declared MIME types never authorize inline content.

A reservation is principal-bound, expires after 15 minutes, carries a separate
random upload token (hash only in PostgreSQL), and permits at most 8 MiB. At most
four unfinished reservations per principal, 512 MiB and 10,000 metadata rows per
application are allowed.
Body transfer and download streams have a 30 second deadline. Pending bytes are
unreadable. Concurrent finalization publishes once. A committed database row is
required before any bytes are created. Publication uses rename before database
commit; retry recognizes the renamed object. Expired pending/deleting objects are
removed by bounded reconciliation on startup and every minute, with an explicit CLI
for immediate recovery. Each pass removes at most 100 entries, including deleted
tombstones. Database rollback does not undo filesystem work. Missing/corrupt objects fail closed.

Routes under `/_diamond/files/BINDING`: POST `begin` (parent, filename, length as
canonical integer string, media_type); PUT `ID` (raw bytes); POST `ID/finish`;
GET `ID/status`; GET `ID`; POST `ID/delete`. Mutations require cookie, exact Origin,
CSRF, and the upload token where applicable. Downloads require a current cookie
and parent read permission. Secret tokens never appear in URLs. Uploads use bounded
streaming, never a whole-body application buffer.

## Outbound HTTP and secrets

Only statically named manifest capabilities and separately supplied native runtime
grants enable an outbound destination. `host.secret("name")` produces an opaque
`Secret` handle, usable only by its destination's adapter. It cannot be displayed,
compared, stored, or transported. Credentials come from a named environment variable
at native startup, never project artifacts. Typed JSON uses Diamond's wire format.

`host.http_get<Reply>("name", path, secret)` and
`host.http_post<Request, Reply>("name", path, secret, request)` return typed Results.
Normal destinations require HTTPS with certificate and hostname validation. The
separate `loopback` network grant permits local fixtures. Named path prefixes,
methods, asynchronously resolved IPs, connected IPs, and each redirect are checked.
DNS work must obey the operation deadline without an uncancelable system resolver
thread holding runtime shutdown. Native loopback-only `dns_server` supports isolated
DNS fixtures without host resolver changes. Proxies,
automatic retries, and response decompression are disabled. Redirects are same-origin
GET only, at most two; POST never follows redirects. Inputs and outputs are bounded
at 1 MiB; each operation has a maximum five-second deadline. Errors redact URL,
headers, credential, and response body. Failed POST delivery has unknown outcome,
never an automatic replay. External calls cannot run inside explicit transactions
or alongside application writes. A read-only authorization snapshot may remain open.

## Verification expectations

Use two actual Diamond processes, a real PostgreSQL cluster, actual shared files,
and local HTTP/TLS fixtures. Verify cross-node bytes and metadata; unauthorized and
pending reads; size, interruption, expiry, concurrent publication, permission changes,
filename traversal, MIME behavior, shared-root mismatch, crash recovery and cleanup.
Verify typed calls, malformed/oversized/compressed output, deadlines, redirect bounds,
cross-origin and private-IP denial, destination/secret mismatch, redaction, no POST
replay, compiler effect/transaction/wire rejection, and cleanup after cancellation.
Exercise the Diamond frontend's upload workflow in Chrome using a synthetic file.
Record exact binaries, raw observations, gaps, and independent checks. Prior checkpoint
evidence remains historical and must not be overwritten.
