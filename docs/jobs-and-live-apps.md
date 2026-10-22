# Durable jobs and live applications (W7)

Diamond 0.16.0 provides a PostgreSQL transactional outbox, bounded workers, typed durable
server events and generated browser subscriptions. This is a Linux, accounts-v1,
relational-v1 profile. It does not provide detached Diamond tasks or exactly-once
external execution. See [deployment and recovery](deployment.md).

A project explicitly declares bindings and capabilities:

```toml
provides = ["Jobs", "Events", "DbRead<Issues>", "DbWrite<Issues>"]
# Repeat the required budget in the server module's provides list.
[jobs.summarize]
function = "server.summarize"
resource = "Issues"
grants = ["DbRead<Issues>", "DbWrite<Issues>", "Events"]
max_attempts = 3
delivery = "transactional"
[events.issue_changes]
resource = "Issues"
payload = "Text"
```

The handler is an exported checked server function with `(parent: Text, payload: T)`
and `Result<Text, DataError>`. `T` must be a finite wire-safe type. The binding's
explicit grants must contain both read and write access to its parent resource,
cover all transitive handler effects, and stay within the application's budget.
Recursive submission by workers is rejected. Browser/shared modules cannot invoke
host operations. Unknown static bindings or mismatched payload types fail checking.
An event payload may be `Text` or a declared server/shared record or enum, such as
`shared.Change`; a browser watch must use an accessible matching state type.

```diamond
export fn queue_summary(parent: Text, payload: Text, key: Text) -> Result<Text, DataError>:
  host.job_submit<Text>("summarize", parent, payload, key)
export fn summarize(parent: Text, payload: Text) -> Result<Text, DataError>:
  let row = issues.get(IssueId(parent))?
  let event = host.event_emit<Text>("issue_changes", parent, payload)?
  Ok("completed")
```

`host.job_submit<T>(binding, parent, payload, dedup_key)` returns an opaque job ID.
`host.job_status(binding, id)` returns bounded JSON text with state, attempts and a
redacted outcome. `host.job_cancel(binding, id)` cancels pending/running work and
increments its fence. Status/cancel require the submitting owner and current
parent authorization; possession of a job ID grants nothing. The deduplication key
is 1..160 bytes, unique per app/binding/owner; a repeated identical submission
returns the original job, including when retained jobs have filled the application
quota. Quota checks apply only to new keys. Reusing a key for another payload,
parent, version or API scope fails. Submissions, events and application mutations
share the request transaction. A propagated error rolls them all back; no worker
can see an uncommitted outbox entry. A caught adapter failure still poisons its
transaction.

Initialize with the ordinary `project-init`. Start two workers using the same
checked build and explicit runtime grants:

```sh
diamond project-work .diamond/build --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

Workers poll every 250 ms and claim at most one job per process. Claims use
`FOR UPDATE SKIP LOCKED`, a 30-second lease and an incrementing fence. Each attempt
reconstructs a fresh non-administrator user principal, checks verified/enabled
account state, restores any actual API scope restriction, and authorizes the
parent under current policy. Cookie sessions' absence of API restrictions stays
distinct from an empty restrictive API scope. Handlers do not capture request
capabilities or trust identity fields in their payload.

A handler validates its current fence without holding the job row across its
bounded evaluation, so cancellation and lease recovery can advance the fence
while an attempt is in flight. Application writes, event publication and the
conditional successful completion marker still commit atomically; losing the
fence race rolls those database changes back. Obsolete or expired completion
fails. Transient failure retries after
1, 2, 4, ... seconds (at most 60), with 1..8 configured attempts. Exhaustion and
current authorization denial become visible dead letters. A killed worker leaves
a lease for another worker to recover. Cancellation cannot undo bytes already sent
to an external service; it fences subsequent completion. SIGINT stops new claims
and drains the current bounded attempt.

External handlers use `delivery = "deduplicated"`; their grants may include
`Http<destination>` and `JobKey`. Existing HTTP/application-write separation stays
in force: put transactional changes in a separate checked handler. In an external
handler, `host.job_key()` is a stable runtime-generated job ID across attempts.
Pass it to a destination with an explicit deduplication or reconciliation contract,
for example `/v1/dedup/` plus the key. The manifest declaration cannot prove the
remote service's behavior. Failure after a side effect and before acknowledgment
can repeat delivery, including after recovery from an unknown outcome. A healthy
concurrent attempt may retry too. This profile makes no exactly-once claim.

Queue schema shape seals declared bindings/payloads. Jobs additionally seal the
server/shared implementation version. UI/CSS edits do not change that job version;
workers never claim a job for a different server implementation. Stop all actors
before replacing code. Reconcile/cancel old pending jobs explicitly after a server
change; never rewrite their version to force execution. Mixed old/new deployments
and payload migrations are outside this profile. A changed binding/schema requires
an explicit current-schema setup; no automatic destructive migration occurs.

## Live data

`host.event_emit<T>(channel, parent, value)` returns an authoritative decimal cursor.
Publishing requires current parent read/update permission and `Events`; protected
reads use the same parent read policy. Events commit with application mutations.
Publication serializes cursor assignment through commit, avoiding a later committed
cursor skipping an earlier uncommitted event.

The authenticated subscription endpoint is
`/_diamond/events/CHANNEL/PARENT/CURSOR`. It requires a cookie and the exact public
Origin, rejects bearer credentials and hostile/missing origins, and exposes only
server-to-client data. Parent path IDs use 1..128 ASCII letters/digits/underscore/
hyphen. Cursor `0` starts retained history. Messages are typed wire envelopes:
`{"cursor":"42","value":...}`. Reconnect from the last applied cursor. A cursor
outside the retained interval yields `{"resync":true,"cursor":"..."}` and closes;
reload authoritative application data before continuing.

A Diamond component subscribes without authored JavaScript:

```diamond
state selected: Text = ""
state live: Text = ""
state cursor: Text = "0"
# Inside html:
<watch channel="issue_changes" parent={selected} into="live" cursor="cursor"></watch>
<p aria-live="polite">{live}</p>
```

The watch owns one bounded latest-value subscription. It updates typed state and
cursor together, holds at most one latest message while a component event is
pending, and discards stale callbacks after identity/parent/component changes.
Unmount, navigation, page suspension and identity changes close owned sockets.
Reconnect is bounded to three attempts. The server sends distinct close codes when
its periodic recheck observes session invalidation or lost parent access. The
generated runtime then clears component state in that stale tab immediately;
transient node or network closes retain state while reconnecting. A closed or
resync subscription shows a visible error and does not silently grant access.
Applications also clear private form/results on local identity change; both examples
demonstrate this. Initialize/reset the cursor deliberately when changing the
selected resource.

The server polls durable state every 500 ms, revalidating session expiry and current
parent permissions. A batch is no older than three seconds when a delivery starts;
individual sends have a two-second deadline. The configured authorization bound is
five seconds; already transmitted bytes cannot be recalled. There are 32 socket
permits separate from the 16 ordinary request permits, 32 events per fetch, 16 KiB
encoded event/job payloads, 10,000 retained events and 10,000 job rows per application.
Slow consumers are disconnected. Terminal jobs require explicit operator archiving
before exhausting the queue quota; no retention daemon removes deduplication keys.

The [live tracker](../examples/live-workspace) includes membership, issues,
comments, attachments, durable edits and live activity. The
[publishing catalog](../examples/catalog) includes private drafts, author edits,
public reads/APIs, live content and a deduplicated external notification.
