# Private files and outbound integrations (W6)

Diamond 0.16.0 provides a private shared-filesystem attachment adapter, typed
outbound JSON HTTP, and opaque destination credentials. The connected server
remains loopback-only behind the W8 proxy profile. Remote PostgreSQL TLS and
object-store replication remain operator responsibilities.

Use [files-workspace](../examples/files-workspace/diamond.toml) for a complete
Diamond frontend over the existing account/workspace example. Application handlers
and frontend events are Diamond; the compiler supplies the trusted browser and
native adapter implementations.

## Native grants and startup

Project capability declarations set a maximum budget. They do not supply a file
store or credentials. Pass runtime authority separately as JSON in an environment
variable. Do not put its contents in a manifest, build, command-line argument,
source control, browser state, or logs.

```json
{
  "objects": "/srv/diamond-private-shared",
  "http": {
    "inventory": {
      "origin": "https://inventory.example.invalid",
      "network": "public",
      "paths": ["/v1/"],
      "methods": ["GET", "POST"],
      "credential_env": "INVENTORY_TOKEN",
      "timeout_ms": 3000,
      "max_response_bytes": 65536,
      "max_redirects": 0
    }
  }
}
```

`objects` is optional without a file profile. `http` defaults to empty. A
credential is optional for an unauthenticated destination, but a destination handle
is still required. Missing grants reject startup. Unknown/duplicate JSON fields,
invalid credentials, origins and limits reject without printing configuration.
All declared HTTP budgets need native grants, including unused declarations.

Prepare a **canonical absolute directory with mode 0700** on a persistent shared
mount, outside public content. All nodes for an application must access the same
bytes and marker. The adapter does not provision NFS, replicate local disks, or
supply a production storage service. Its owner and mount administrator are trusted.
A different marker fails closed. Copying a marker does not create shared storage.
The marker is an operator binding, not proof that two paths share a mount; an
administrator who copies it can make separate local directories appear bound.
Regular-file SHA-256 verification detects missing, truncated and same-length
corruption; it does not defend against a malicious administrator rewriting both
PostgreSQL and storage. Filesystem service availability remains an operator concern:
a deadline cannot cancel an indefinitely blocked kernel filesystem operation.
Each running server revalidates its bound marker before every new reservation and
physical path resolution. Removing or replacing the root therefore fails file
operations as well as readiness until the original store identity is restored.

```sh
# Set DIAMOND_POSTGRES, DIAMOND_RUNTIME, and referenced credential variables
# privately in the launching environment; do not print their contents.
diamond project-init examples/files-workspace/diamond.toml --postgres-env DIAMOND_POSTGRES
diamond project-files-init examples/files-workspace/diamond.toml --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond serve examples/files-workspace/diamond.toml --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --identity-mailbox /srv/diamond-private-mail --origin http://127.0.0.1:3000 --listen 127.0.0.1:3000
```

Provision a synthetic/local administrator using `project-admin-add` as described
in the [account guide](accounts-and-apis.md). The file directory must already exist;
`project-files-init` creates its private marker and binds that identity to the
application's current PostgreSQL metadata. Stop servers before schema changes.
There is one current schema; no mixed-version upgrade compatibility is provided.
The native embedding API is `RuntimeGrants::from_json`,
`CheckedProject::serve_with_runtime`, and `CheckedProject::maintain_files`.
`RuntimeGrants` Debug output is redacted and it has no serialization API.

## Attachment bindings and authorization

```toml
[files]
profile = "shared-fs-v1"
[files.bindings.issue_files]
resource = "Issues"
max_bytes = 8388608
```

Bindings require accounts, relational storage, and application
`DbRead<Issues>`/`DbWrite<Issues>` budgets. The adapter evaluates the parent record's
current read policy for downloads/status and its read plus update policy for
reservation, transfer, publication and deletion. The update check uses the unchanged
parent row. Use a parent update policy that represents the intended attachment
permission; a policy requiring a particular field change may deny file writes.
Upload ownership and a valid ticket never replace parent permission. Pending
status is owner-only. An authorized download uses its initial policy snapshot;
revoking permission denies subsequent requests and cannot recall transmitted bytes.

The browser API is restricted to event expressions:

```diamond
on upload:
  attachment = Some(files.upload("issue_files", selected_issue, "chosen_file"))
on download:
  attachment = match attachment:
    Some(file) => Some(files.download("issue_files", file))
    None => None
on remove:
  attachment = match attachment:
    Some(file) => files.delete("issue_files", file)
    None => None
```

Use `<input id="chosen_file" type="file">` without text bindings, `value`, or
multiple selection. Bindings and input IDs are static literals; parent IDs are
ordinary Text validated by the server. Component state stores the result as
`Option<Blob<"issue_files">>`. A successful upload returns that nominal, opaque
value only after publication. It has no source constructor, display, comparison,
persistence, or generic RPC representation. Status returns metadata as JSON Text;
deletion returns `None`; download returns the same opaque value while invoking the
browser's native attachment download. Every native use rechecks the current
principal and parent policy, so a Blob carries identity, not authority.
The runtime retains the selected native File and upload ticket internally; neither
is a Diamond value. Identity changes clear file selections. Stale event generations
cannot apply an old response to current component state. No automatic upload replay
occurs; refresh status after uncertain delivery before choosing to retry.

The native protocol is available to cookie-authenticated clients:

| Request below `/_diamond/files/BINDING` | Body / additional authority | Result |
| --- | --- | --- |
| `POST /begin` | `{parent,filename,length,media_type}` | `{id,upload,state:"reserved"}` |
| `PUT /ID` | raw octet-stream, exact Content-Length; `X-Diamond-Upload` | staged |
| `POST /ID/finish` | `{}`; `X-Diamond-Upload` | ready; explicit retry is idempotent while the ticket lives |
| `GET /ID/status` | current session | ID, owner, original metadata, length, state, SHA-256 |
| `GET /ID` | current session and parent read permission | attachment bytes |
| `POST /ID/delete` | `{}` and parent update permission | deleted |

Mutations require exact Origin, CSRF, and the account cookie; JSON controls require
`application/json`. File routes reject bearer credentials. Internal IDs are generated
64-character lowercase hex; tickets are separate random values, stored as hashes,
bound to app/binding/parent/principal/expiry, and never placed in URLs. `length`
is a canonical nonnegative integer **string**. Zero-byte uploads are supported.
Original names and MIME types are untrusted metadata. Traversal/separators/control
characters reject. Physical paths contain generated IDs only. Downloads always use
`application/octet-stream`, `attachment`, `nosniff`, and no-store, including HTML.
This profile provides no malware scanning or safe inline preview.

Each binding permits 1–8 MiB maximum; a file may be empty. Four unfinished uploads
per principal, 512 MiB reserved/ready bytes and 10,000 metadata rows per application
bound allocation. Reservations expire in 15 minutes. Raw upload/download streams
have 30-second deadlines and bounded chunks; JSON controls retain the 16 KiB,
three-second limit. Request permits remain owned by transfers and blocking work.
SHA-256 and exact lengths are checked before publication and downloads.

The states are reserved → receiving → staged → ready → deleting → deleted.
Disconnects remove partial bytes while the reservation remains until reconciliation.
A committed database row precedes physical creation. Publication renames and syncs
bytes **before** committing ready metadata. Database rollback does not undo that
rename. A retry verifies a renamed object; expiry cleanup removes it if publication
never commits. Readers require ready metadata. Deletion commits unreadability before
unlinking; cleanup completes interrupted deletion. Each node reconciles on startup
and every minute, removing at most 100 expired/deleting/deleted entries. For immediate
operator recovery, run `project-files-clean PROJECT --postgres-env VARIABLE
--runtime-env VARIABLE`. Ready attachments are never garbage-collected by that pass.

## Typed HTTP and destination secrets

Declare `Http<inventory>` in both application and server-module budgets. Only
statically registered server operations exist:

```diamond
export fn inventory(path: Text) -> Result<Text, DataError>:
  host.http_get<Text>("inventory", path)

export fn notify(path: Text, body: Text) -> Result<Text, DataError>:
  host.http_post<Text, Text>("inventory", path, body)
```

Declare the binding once in `[secrets]`; the compiler requires it and the native
adapter injects its credential. Application source never acquires a credential or
opaque handle. The retained low-level `Secret<"inventory">` compatibility type is
still non-comparable, non-displayable, non-persistent, non-wire-safe, and unavailable
to browser code, but new applications do not need it. JSON request/reply types must
be finite and wire-safe, using the existing [Diamond wire codecs](accounts-and-apis.md).
This is JSON HTTP, not an arbitrary binary/socket/native-loading API.

Normal destinations require HTTPS with certificate and hostname verification.
`network:"public"` rejects local/private/reserved address classes. A separate
`network:"loopback"` grant permits HTTP or TLS fixtures on loopback only.
Optional `ca_file` adds a bounded PEM certificate captured at startup, retaining
hostname verification; it does not disable verification or change system trust.
An origin contains only scheme, host and optional port. Paths must be absolute
origin-relative, within an allowed prefix at a segment boundary, with no encoded
path escapes, dot segments, authority, backslash or fragment. GET redirects are
same-origin/path-policy only, at most two; POST redirects reject. Each hop performs asynchronous DNS using the native startup snapshot of system
resolver configuration and hosts, and validates every returned address, pins connections to that set and checks the
actual connected peer. DNS/IP changes cannot silently permit private destinations.
Proxies, automatic retries, referer forwarding, and decompression are disabled.
The DNS sockets and HTTP work belong to the operation runtime, so a stalled system
`getaddrinfo` cannot hold its deadline or shutdown open. Optional native
`dns_server: "127.0.0.1:PORT"` supplies an isolated UDP DNS fixture; it is accepted
only for a loopback network grant and a nonzero loopback address. It never changes
the host resolver and does not relax destination or TLS checks. Normal deployments
use the captured system DNS configuration; restart native grants after changing it.

Inputs/outputs are at most 1 MiB; a destination may lower its output limit. Total
operation deadlines are 1–5000 ms, default 3000. GET transport/format failures are
redacted typed failures. Once POST is attempted, transport, HTTP or response-decoding
failure conservatively reports `outcome_unknown`. The adapter never retries a POST
or claims rollback. Incoming duplicate requests may independently execute; this is
not an exactly-once/deduplication contract.

External calls cannot execute in explicit transactions or share a function's
transitive effects with application writes. A read-only database authorization
snapshot may remain open. Authorize any relevant parent/resource **before** an
external call; exporting a handler that checks no resource gives every permitted
caller that handler's declared destination authority. External side effects are
outside the database's rollback boundary. A canceled browser delivery may leave
bounded server work running until its deadline; owned connections and drivers close
before the worker releases its request permit.

Errors exclude destination URLs, headers, credential bytes and remote bodies.
Direct credential reflection through successful replies also rejects, including
JSON Unicode escapes. The destination is a trusted recipient of the credential;
this is not universal taint tracking of arbitrary transformations it returns.
Build contracts describe the adapter signatures, limits, effects, replay, failure,
transaction and cancellation behavior. There is no dynamic native library loading,
arbitrary shell execution, browser-supplied native grant, or blanket network grant.

See the [W6 behavioral contract](../verification/web_six.md) and
[verification record](../verification/web_six_results.md) for observed coverage and gaps.

The upload/SSRF controls were informed by the official [OWASP upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html), [OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), and pinned [Hickory asynchronous resolver documentation](https://docs.rs/hickory-resolver/0.26.3/hickory_resolver/struct.Resolver.html), and [reqwest API documentation](https://docs.rs/reqwest/0.12.28/reqwest/struct.ClientBuilder.html). Verification remains bounded to the recorded observations.

Public IPv6 grants conservatively exclude returned 6bone/documentation prefixes,
including the late verified `3ffe`/`3fff` denial regressions. Address-policy inputs
were checked against the [IANA IPv6 registry](https://www.iana.org/assignments/iana-ipv6-special-registry/) and [address-space allocations](https://www.iana.org/assignments/ipv6-address-space/). This does not promise acceptance of every globally allocated special-purpose prefix.
