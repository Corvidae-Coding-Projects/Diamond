# Reliable database actions

Diamond 0.20.0 makes recovery from an uncertain database mutation a runtime
responsibility. Application authors declare the action and its authorization
parent; they do not invent idempotency keys, retry loops, or browser persistence.

## Declare an eligible action

The server function is an exported `action` returning a checked application
`Result`:

```diamond
export action rename(id: IssueId, value: EditIssue) -> Result<Issue, EditError>:
  match issues.get(id):
    Ok(before) =>
      match issues.update(id, IssueChange(title: value.title, revision: before.revision + 1)):
        Ok(saved) => Ok(saved)
        Err(problem) => Err(SaveFailed)
    Err(problem) => Err(SaveFailed)
```

Bind it in the manifest:

```toml
[actions.rename]
function = "server.rename"
replay = "transactional"
resource = "Issues"
parent = "id"
label = "Save issue"
```

`resource` names the protected parent resource. `parent` names a function
parameter whose nominal ID must exactly match that resource. `label` is the
accessible name shown by runtime-owned recovery UI. Transactional actions require
relational-v1 storage, accounts-v1 identity, current read authority for every CRUD
dependency, and database-only effects. The compiler rejects set/aggregate queries,
HTTP, Git, events, mail, files, jobs, workflows, and other external effects.

`replay = "never"` remains available for the older single-attempt database action
contract. It cannot declare `resource` or `parent`, has no result receipt, and can
remain uncertain after delivery loss. Use it only when that narrower contract is
intentional.

## Use the action in a component

```diamond
state outcome: Action<Issue, EditError> = ActionIdle

on save(value: EditIssue):
  outcome = action.rename(issue, value)
```

Diamond publishes `ActionSubmitting` while the request is pending. Terminal
states are:

| State | Meaning |
| --- | --- |
| `ActionSucceeded(value)` | Mutation and receipt committed together. |
| `ActionRejected(error)` | The declared business error was recorded; action writes rolled back. |
| `ActionInvalid` | Checked action input was invalid. |
| `ActionDenied` | Current identity or policy denied execution/result access. |
| `ActionAborted` | The runtime established that the action did not commit. |
| `ActionUnknown` | Transport completion is uncertain; reconcile the same invocation. |
| `ActionExpired` | The invocation expired or was pruned; it cannot execute again. |
| `ActionConflict` | Input, parent, contract, identity, or authority binding changed. |
| `ActionCorrupt` | Stored control state failed authenticated decoding or binding checks. |

Match every state. Do not interpret cancellation or a network error as rollback.
Do not submit a new mutation merely because the result is unknown.

## What the runtime does

Before execution, the runtime creates a random 256-bit invocation identity and
binds it to the application/action version, canonical checked arguments, verified
account, cookie/API profile, current API credential restrictions, declared parent,
and expiry. An identity is a lookup handle, not authority.

For an eligible action, one PostgreSQL transaction contains the action mutation
and an encrypted, authenticated receipt. Duplicate execution serializes on the
invocation and returns the recorded outcome. A declared application rejection
rolls the action body back to a savepoint before its terminal receipt commits.
PostgreSQL commit acknowledgement loss remains `outcome_unknown` at the transport
boundary; status lookup then distinguishes committed success from confirmed abort.

Receipts include the bounded protected CRUD facts used by the action. Status and
duplicate result reads recheck the current account, API restrictions, declared
parent, saved facts, and current rows. Moving or recreating a row cannot use an old
receipt to bypass policy. A committed deletion may use its sealed tombstone facts,
but a same-ID replacement is independently authorized and is never deleted again.

The browser keeps recovery references in server control state, not localStorage or
sessionStorage. Reload discovers up to 16 unacknowledged references for the current
account. Runtime-owned, accessible UI lets the user check an outcome or dismiss the
notice. Identity changes clear the browser's private recovery state and requests.
Successful local handling acknowledges the reference automatically.

## Explicit HTTP clients

Cookie clients use the normal exact-origin, session, and CSRF checks:

```text
POST /_diamond/actions/rename/prepare  {contract, arguments}
POST /_diamond/actions/rename          {contract, arguments, invocation}
POST /_diamond/actions/rename/status   {contract, invocation}
POST /_diamond/actions/rename/ack      {contract, invocation}
POST /_diamond/recovery                {contract}
```

Token clients use the declared `/api/v1/NAME[/prepare|/status|/ack]` operation.
The token must permit the base operation, and every request revalidates its current
credential epoch, operations, scopes, and resource policy. Cookie, token, and API
alias bindings are intentionally not interchangeable.

## Bounds and guarantees

- Lifetime: 24 hours.
- Unacknowledged references: 16 per application/user.
- Total retained invocations: 128 per application/user and 4,096 per application.
- Protected CRUD footprint: 64 unique accesses.
- Receipt plaintext: 256 KiB before authenticated encryption.

Preparation prunes expired records under a shared capacity lock. Reaching a bound,
losing a receipt, or failing to decode one never authorizes fresh execution.

The guarantee is transactional reconciliation for bounded PostgreSQL CRUD. It is
not distributed exactly-once execution. External effects remain outside the
transactional profile and require their dedicated adapter/outbox contract or an
explicit uncertainty workflow.

The runnable implementation fixture and adversarial evidence are in
[`verification/lp5`](../verification/lp5/) and [`verification/lp5_actions.py`](../verification/lp5_actions.py).
