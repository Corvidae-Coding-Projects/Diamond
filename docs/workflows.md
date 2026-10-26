# Typed durable workflows

Diamond workflows are bounded, persistent state machines. Application code owns
business transitions. The compiler and runtime own persistence, current authority,
idempotency, concurrency, timers, retries, fencing, and cancelation.

Define an enum whose variants are the complete state space:

```diamond
enum DocumentFlow:
  Queued
  Waiting
  Completed(message: Text)
  Cancelled

export fn advance(parent: Text, input: Text, state: DocumentFlow) -> Result<DocumentFlow, DataError>:
  let document = documents.get(DocumentId(parent))?
  match state:
    Queued => Ok(Waiting)
    Waiting => Ok(Completed(input))
    Completed(message) => Ok(Completed(message))
    Cancelled => Ok(Cancelled)
```

The ordinary exhaustive `match` proves every state is handled. The manifest seals
the initial and terminal variants and supplies bounded scheduling behavior:

```toml
provides = ["Workflows", "DbRead<Documents>", "DbWrite<Documents>"]

[workflows.document_flow]
function = "server.advance"
resource = "Documents"
state = "DocumentFlow"
initial = "Queued"
terminal = ["Completed", "Cancelled"]
timers = { Waiting = 60 }
compensation = "server.compensate"
grants = ["DbRead<Documents>", "DbWrite<Documents>"]
max_attempts = 3
max_transitions = 64
retry = "transactional"
concurrency = "parent"
```

The initial variant must have no fields. Terminal and timer names must be real
variants, terminal states cannot also be timers, timers are 1..86,400 seconds,
attempts are 1..8, and transitions are 1..256. The only current retry profile is
`transactional`, and the only current concurrency profile is `parent`. These are
required declarations rather than permissive defaults.

Submit and inspect workflows through typed host operations:

```diamond
export fn start(parent: Text, input: Text, key: Text) -> Result<Text, DataError>:
  host.workflow_submit<Text>("document_flow", parent, input, key)

export fn state(id: Text) -> Result<DocumentFlow, DataError>:
  host.workflow_state<DocumentFlow>("document_flow", id)

export fn status(id: Text) -> Result<Text, DataError>:
  host.workflow_status("document_flow", id)

export fn cancel(id: Text) -> Result<Text, DataError>:
  host.workflow_cancel("document_flow", id)
```

Binding names and generic types are compiler-bound. A repeated identical submit by
the same owner returns the same execution ID even after the state advances. Reusing
the key with another input, parent, implementation version, or API restriction is
a conflict. Only one active execution for a binding/parent is admitted atomically.
Possessing an ID grants no access; state, status, and cancelation require the
original owner and current parent policy.

Each worker attempt recreates a non-administrator principal, rechecks the account,
API scope, and parent write policy, decodes the persisted input/state against the
compiled types, and commits one transition with a lease and fence. A timer uses
PostgreSQL time. Successful transitions reset their retry count. Transition,
attempt, execution, and encoded-value ceilings prevent accidental infinite work.

Cancelation advances the fence and schedules the optional compensation function.
Compensation receives the same `(parent, input, state)` arguments and returns
bounded text. The execution becomes `cancelled` only when compensation commits.
Authorization failure, exhausted retries, corrupt state, or transition exhaustion
becomes a redacted `dead` execution.

Workflow handlers and compensation may make declared transactional database and
event changes. They cannot call HTTP, mail, Git, delivery, operations, jobs, or
workflows. That prohibition is deliberate: automatic retries cannot duplicate an
external effect merely because a developer forgot an idempotency ritual. Use a
separate durable adapter after the workflow reaches a terminal database state.

Run workflow workers with the same command as jobs and mail:

```sh
diamond project-work diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```
