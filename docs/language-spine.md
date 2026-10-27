# Connected language guide (LP2)

The LP2 spine connects five application concepts through checked source, server
dispatch, and the managed browser. The complete runnable source is
[examples/language-spine](../examples/language-spine/). This is the first connected
slice. LP3 expands its routes/forms; the [current typed input guide](typed-inputs.md)
supersedes LP2's narrow input limits below. The [profile matrix](language-profiles.md)
states current boundaries.

## Declare shared inputs

Put schemas and pure business helpers in a shared module imported by both the
server and UI:

```diamond
export type IssueId = Id<"issue">
export route IssuePage(id: IssueId) = "/issues/{id}"
export form EditIssue(title: Text<length: 1..120>)
```

Both declarations define nominal records. `IssuePage(id: issue.id)` builds a typed
route value. Use it in `href={IssuePage(id: issue.id)}`. A named outlet exposes its
checked fields:

```diamond
<route name="IssuePage" as="detail">
  <p>{detail.id}</p>
</route>
```

This small example uses nominal IDs and a literal namespace prefix such as
`/issues/`. LP3 also supports canonical scalar paths and query schemas as described
in the [typed input guide](typed-inputs.md). Reserved paths and overlapping
patterns reject. Direct requests, reload,
client links, and history use the same schema. Rendered initial pages remain pure
public shells; fetch private data through checked authenticated operations.

## Submit a checked form

The example Editor owns a Text draft. A schema form reads its actual named
controls, checks their exact shape and scalar bounds, and supplies one typed
record to `save`:

```diamond
on save(value: EditIssue):
  result = action.rename(issue, value)
  emit changed(result)
html:
  <form schema="EditIssue" as="value" on-submit={save(value)}>
    <label for="issue-title">Title</label>
    <input id="issue-title" name="title" type="text" bind-value={draft}>
    <button type="submit" disabled={pending}>Save title</button>
  </form>
```

This fragment belongs inside a component declaring `issue`, `draft`, `result`,
and `changed`; see the complete [Editor](../examples/language-spine/ui.dia).
Each Text schema field requires one static named Text control. Other scalar
kinds use their checked control kinds. The compiler owns
constraint attributes; duplicate, extra, nested, or conditional controls reject.
The native maximum length accommodates UTF-16, then the managed decoder enforces
the declared Unicode-scalar count. Text bounds and exact record decoding are
checked again on the server, independent of browser validity. Pure shared
business validation can run in both targets; the server must apply business
rules within its action. Generated field errors and other scalar kinds are
documented in the typed input guide.

## Pass data through events and outputs

An event can accept typed positional data:

```diamond
on open(id: IssueId):
  selected = [id]
```

Use `on-click={open(row.id)}` inside a keyed row. An unrelated `UserId` fails
checking. A child declares its output in the component body:

```diamond
output changed(value: Action<Issue, EditError>)
```

The parent binds `on-changed={saved}` on `<use component="Editor" ...>` and
declares `on saved(value: Action<Issue, EditError>):`. Every output must bind a
compatible parent event. `emit` delivers after the child's local commit. Outputs
from disposed/replaced instances or an old identity cannot update the parent.
Outputs contain checked data, not callbacks or host authority. Lifecycle events
cannot take parameters or emit outputs. Chained messages have a 64-message and
30-second limit.

## Declare a protected action

The [server example](../examples/language-spine/server.dia) declares:

```diamond
export action rename(id: IssueId, value: EditIssue) -> Result<Issue, EditError>:
```

Its real body reads the current row and updates it under the Issues update
policy. The manifest explicitly exposes the boundary:

```toml
[actions.rename]
function = "server.rename"
replay = "transactional"
resource = "Issues"
parent = "id"
label = "Save issue"
```

Transactional actions require relational-v1 storage, accounts-v1 identity, a
declared application-error enum, a nominal parent matching the declared resource,
and bounded CRUD-only effects. The source effect declaration does not grant
access: the current verified session/token and resource policies still decide
authority. Actions cannot be called as ordinary source functions or bound as
ordinary RPC. A token API can explicitly bind the action, with its own
operation/scope restrictions and the same checked durable dispatcher.

The browser stores `Action<Issue, EditError>` initialized with `ActionIdle`.
It receives `ActionSubmitting` automatically while dispatching, then one of:

| Outcome | Meaning |
| --- | --- |
| `ActionSucceeded(issue)` | The action transaction committed its checked result. |
| `ActionRejected(error)` | Declared application rejection; the transaction rolled back. |
| `ActionInvalid` | The invocation encountered a checked input failure. Malformed transport input can reject before a typed invocation. |
| `ActionDenied` | Current authority/policy denied the invocation; no committed action mutation. |
| `ActionAborted` | The runtime confirmed the action transaction aborted. |
| `ActionUnknown` | Completion cannot be established; inspect current saved state before a deliberate new attempt. |
| `ActionExpired` | The invocation expired or was pruned and can never execute again. |
| `ActionConflict` | Its input, parent, application, identity, or authority binding changed. |
| `ActionCorrupt` | Stored control state failed authenticated decoding or binding checks. |

Match outcomes explicitly, as in the shared `outcome_text` helper. Generic RPC,
HTML compound interpolation, and application storage do not serialize Action.
Restricted nested payloads cannot use outputs or the action result transport.
Caught repository policy failures still poison the enclosing action transaction.
Canceling local work never claims to undo a server commit.

## Submission protocol and uncertainty

Source authors call `action.rename`; the runtime owns the two-phase submission
protocol. There is no author-managed nonce, retry convention, or hidden replay.
This is needed because a browser or transport can resend a POST after losing its
response even when application JavaScript did not request a retry.

For a raw cookie client, first POST `/_diamond/actions/rename/prepare` with the
normal session/CSRF/origin guards and `{contract, arguments}`. The checked reply
is `{invocation}`. Execute with `{contract, arguments, invocation}`; status and ack
use `{contract, invocation}` at `/status` and `/ack`. A bearer API uses the same
suffixes under `/api/v1/rename`; successful API replies include `api_version: 1`.
API credentials must allow the base operation `rename`. Cookie proofs, API
credentials, and aliases are not interchangeable.

The native identity is random, expires in 24 hours, and binds the current action
version, canonical decoded inputs, verified user, endpoint/profile, API authority,
and declared parent. The mutation and sealed receipt commit in one PostgreSQL
transaction. Duplicate execution serializes to one outcome. Status reauthorizes
the original CRUD facts and current rows under current account/scopes/policy.
A lost commit acknowledgement remains Unknown until status establishes the same
invocation's committed success or confirmed abort; it never triggers a new write.

Reload discovers unacknowledged server references without storing form bodies,
results, secrets, or invocation metadata in browser storage. The managed recovery
notice offers Check outcome and Dismiss; identity changes clear its private state.
See [reliable actions](actions.md) for bounds, deleted-row handling, explicit HTTP
shapes, the `replay = "never"` profile, and the external-effect boundary.

## Build and compatibility

```sh
cargo build --locked
target/debug/diamond check examples/language-spine/diamond.toml
target/debug/diamond build examples/language-spine/diamond.toml \
  --out .diamond/language-spine-build
```

Current version identifiers are language 0.20.0, manifest schema 1, artifact format 5,
wire 7, and API contract 1. Rebuild older trusted sources; old artifacts and
mixed browser/server contracts reject. Exact original source/comments remain in
the private artifact for later tooling. Do not publish `server.json` or
`contract.json`; the server exposes only its generated public asset allowlist.
