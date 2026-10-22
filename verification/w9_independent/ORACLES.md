# Independent W9 adversarial oracle

Written before executing this campaign. Expectations come from the checked
connected-app, account/API, file/integration, jobs/events, and deployment/recovery
contracts in the W5-W8 guides and from AC-5, AC-6, AC-8, AC-10, AC-12, AC-14,
AC-15, AC-17, and AC-20 in `.design/general-web-apps.md`. A status code alone is
not proof: protected state is read back through an ordinary consumer or directly
from this campaign's isolated PostgreSQL/object/receiver fixture.

| Case | Adversarial action | Independent expected observation |
| --- | --- | --- |
| H1 | Send raw HTTP requests with duplicate/conflicting Host, Origin, Cookie, Content-Length, Forwarded, absolute-form paths, encoded paths, transfer coding, and body framing edge cases | Noncanonical requests fail before dispatch; no authenticated or application side effect occurs; the same node still serves a fresh canonical request |
| H2 | Mix cookie, bearer, CSRF, API-version, method, content type, and strict JSON shapes across RPC/API/account/file endpoints | Profiles do not fall back into one another; missing/duplicate/hostile authority fails; duplicate or extra JSON fields fail; protected state is unchanged |
| P1 | Exercise one parent through page/RPC, scoped API, file, job, and event interfaces as owner, reader, cross-workspace member, and outsider | Each interface recomputes current identity and parent policy; opaque IDs, upload tickets, job IDs, cursors, or credentials never grant authority by themselves |
| P2 | Revoke a session, API credential, membership, or account while another server/socket/worker remains live | New operations after the revocation commit fail on both nodes; queued work and event delivery use fresh policy; already transmitted bytes and already serialized transactions remain outside the claim |
| J1 | Hold an external job after its receiver-side effect, let its lease age, race another worker, then release or terminate the first holder | At-least-once effects retain one receiver business effect under the stable job key; one current fence may complete; stale completion cannot overwrite newer state |
| J2 | Cancel pending/running jobs and choose restore recovery `cancel` with pending and running records | Cancellation increments fencing and prevents later dispatch; recovery cancels both states, unpauses explicitly, and no cancelled effect reaches the receiver |
| J3 | Retry identical and conflicting stable-key submissions after unrelated terminal rows fill the 10,000-row application quota | The identical retry returns the original opaque job ID without allocating a row; a changed payload remains a conflict; quota pressure cannot break the documented deduplication/idempotency contract |
| E1 | Seed just below/at/above the 10,000-event retention edge, publish through the real RPC, and reconnect at old/current/ahead cursors | Automatic trimming retains at most 10,000 application events; unavailable/ahead cursors produce explicit resync; current cursors replay ordered typed values |
| E2 | Use malformed upgrade headers, origins, cookies, cursor encodings, frames, oversized/fragmented/inbound data, connection quota, and policy changes | Only canonical authenticated upgrades succeed; unsolicited client data never publishes; bounded connections close and ordinary HTTP remains available |
| F1 | Interrupt upload phases; race finalization/deletion; corrupt, replace, remove, or permission-change owned object bytes; retry from another node | Pending bytes stay unreadable; ready bytes must match recorded length/hash and current policy; unsafe or missing storage fails closed and later repair permits fresh work |
| D1 | Stop and restart the owned PostgreSQL process with both app nodes/workers alive; terminate one node/worker at selected points | Database loss grants no fallback authority; requests fail safely without process panic; surviving/current processes recover or fail explicitly after the database returns |
| D2 | Probe deployment/recovery inputs, active-actor exclusion, object identity, backup ciphertext/manifest boundaries, and wrong/nonempty targets | Unsafe, incompatible, tampered, or active operations reject without publishing partial restored application state; explicit successful recovery is independently inventoried |

## Exploration budget and boundaries

The campaign uses only generated synthetic accounts, records, credentials,
payloads, and objects in private temporary roots with a private Unix-socket
PostgreSQL cluster. It drives the frozen executable through CLI, real HTTP/1.1,
masked WebSocket frames, PostgreSQL, shared files, and a controlled loopback
receiver. Direct SQL is fixture setup and independent observation only; product
operations still traverse Diamond interfaces.

The bounded run will vary identities, two app servers, at least two workers,
cursor boundaries, request framing/header multiplicity, payload sizes, job
leases/fences, revocation timing, process/database loss, and object states. It
does not run the separate long capacity/SLO campaign, expose a public host, use
a remote PostgreSQL/TLS service, prove kernel/storage power-loss durability, or
claim exhaustive security. Suspected failures must be rerun from a fresh process
or clean state, minimized, and preserved with exact baseline/final executable
hashes. Checker mistakes are recorded separately and do not become product
findings.
