# LP2 compiler/runtime spine

Status: complete, 27 October 2026. This resolves LP2 spelling and the minimum
connected slice from [the staged plan](language-production-readiness.md).

## Source contract selected before implementation

```diamond
export route IssuePage(id: IssueId) = "/issues/{id}"
export form EditIssue(title: Text<length: 1..120>)

export action rename(id: IssueId, value: EditIssue) -> Result<Issue, EditError>:
  # Ordinary checked server body and effects; policies still decide authority.
  ...

export component Editor(issue: IssueId):
  output changed(value: Action<Issue, EditError>)
  state draft: Text = ""
  state result: Action<Issue, EditError> = ActionIdle
  on save(value: EditIssue):
    result = action.rename(issue, value)
    emit changed(result)
  html:
    <form schema="EditIssue" as="value" on-submit={save(value)}>
      <label for="title">Title</label>
      <input id="title" name="title" type="text" bind-value={draft}>
      <button type="submit">Save</button>
    </form>
```

The ellipsis is explanatory, not valid accepted syntax. The [reference app](../examples/language-spine/)
contains an actual protected mutation and independent readback. The runtime owns
Submitting state; authors do not need to remember a pending convention.

- Routes/forms declare nominal record schemas using the existing finite type
  checker. Constructors use named fields. They are data, not grants. A typed
  route value is accepted in `href`; arbitrary Text still is not. A route outlet
  uses `<route name="IssuePage" as="detail">` and reads `detail.id` in its body.
- LP2 supports canonical literal segments plus nominal-ID path parameters, and
  bounded Text form fields. Other path/query/form kinds reject explicitly until
  LP3. Overlapping route patterns and undeclared/reserved paths reject.
- Schema forms decode the actual named controls at submit into the declared
  record. Compiler-generated bounds complement, rather than replace, server
  checked input. Input drafts remain Text. Field-error polish and wider scalar
  decoding belong to LP3.
- Events accept typed positional parameters; outputs declare typed data messages.
  `<use ... on-changed={parent_handler}>` binds an output. Emissions follow local
  commit and are fenced by instance lifetime, binding, and identity epoch. No
  closures or captured native authority cross the component boundary.
- Server actions are explicit declarations and explicit manifest `[actions.NAME]`
  bindings (`function = "server.rename"`, `replay = "never"`). They share checked
  codecs, scope/policy enforcement, and the existing request transaction. Raw RPC
  must not expose the same action through a weaker entry point.
- `Action<T,E>` is a managed local outcome type with Idle, Submitting, Succeeded,
  declared Rejected, Invalid, Denied, Aborted, and Unknown variants prefixed
  `Action`. It is not generic RPC/persistence authority. Confirmed abort and
  uncertain delivery remain different. Replay/recovery modes are unsupported
  and reject. A real Chrome response-loss test exposed transparent POST retry;
  LP2 therefore adds native one-attempt submission proofs so transport retries
  cannot repeat execution. Proof consumption commits before the mutation and
  retains no result. LP5 supplies atomic mutation receipts and recovery; LP2
  does not claim exactly-once completion. Actions require relational-v1 storage
  and accounts-v1 identity, with current policy rechecked before execution.
- New declarations/events retain original source and locations; build artifacts
  retain exact source including comments. LP7 will add formatter/editor behavior.
- Language 0.17.0, artifact format 2, and wire 4 identify this spine; manifest
  schema 1 and API contract 1 retain their independent meanings.
  Incompatible artifacts and browser/server contracts reject; prototype upgrade
  compatibility is not implemented.

## Acceptance completed

Use LP2-P1/P2 and LP2-N1–N4 in [the independent scenario list](../verification/lp1/SCENARIOS.md).
Ran the reference app against real PostgreSQL; mutated one intended issue via its
typed form/child output and inspect SQL plus fresh-client readback. Test Bob,
anonymous calls, missing native grants, wrong nominal types, nested secrets,
raw RPC bypass, unavailable replay, and contract/version mismatch. Run relevant
existing examples and compiler/security regressions plus Rust quality checks.
Final hashes, actual browser operations, source coverage, findings/replays, and
unexercised boundaries are in [verification/lp2](../verification/lp2/README.md).
Wider routes/forms, component ergonomics/accessibility, durable receipts, resource
stress, and editor tools remain the subsequent checkpoints.
