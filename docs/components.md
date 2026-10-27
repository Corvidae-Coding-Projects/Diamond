# Reusable components and native controls

Diamond 0.20.0 lets a row pass a typed ID directly to an event, and a child
editor report a typed result to its parent. A component is an instance with its
own state and lifetime. It is not a callback closure or a permission grant.

Run the complete, dependency-free example:

```sh
cargo run -- serve examples/components/diamond.toml --listen 127.0.0.1:39174
```

Open http://127.0.0.1:39174/. Each editor has its own drafts, native controls,
validation errors, and completion message. The second editor's Unicode key also
demonstrates that generated IDs survive server rendering and browser hydration.

## Pass data directly

Declare the handler's parameters and pass matching values at the use site:

```diamond
on open(id: IssueId):
  selected = [id]
html:
  <each items={issues} as="issue" key={issue.id}>
    <button type="button" on-click={open(issue.id)}>Edit {issue.title}</button>
  </each>
```

The compiler checks arity and nominal types. An organization ID cannot replace an
issue ID even when their text is identical. A rendered ID still grants no access:
the server rechecks current resource policy when loading or changing the record.

## Let an editor report completion

```diamond
export component IssueEditor(issue: IssueId):
  output changed(value: Action<Issue, EditError>)
  state draft: Text = ""
  state result: Action<Issue, EditError> = ActionIdle
  on save(value: EditIssue):
    result = action.rename(issue, value)
    emit changed(result)
  html:
    <form schema="EditIssue" as="value" on-submit={save(value)}>
      <field name="title" label="Title" description="Describe the work." bind-value={draft}></field>
      <submit label="Save issue"></submit>
    </form>
    <status value={outcome_text(result)}></status>
```

The shared module declares `EditIssue`, `Issue`, `EditError`, and `outcome_text`;
the manifest binds the database action. See the complete
[language spine](../examples/language-spine/) for those pieces and the
[Forge editor](../examples/git-browser/ui.dia) for an integrated version.

The parent binds every output to a declared handler of the exact signature:

```diamond
on saved(value: Action<Issue, EditError>):
  notification = outcome_text(value)
html:
  <each items={selected} as="id" key={id}>
    <use component="IssueEditor" issue={id} on-changed={saved}></use>
  </each>
```

Emissions follow the child's successful local commit, in source order. They
share a limit of 64 deliveries and 30 seconds for the chain. Static component
recursion is rejected; output bindings lead to ancestors. A limit failure does
not undo earlier handlers. Reordering a keyed child retains its state and native
control. Removing or replacing it disposes pending work; late replies cannot
update a replacement or deliver its output. Aborting browser work cannot undo a
server transaction that already committed. Transactional actions now reconcile
that uncertainty through runtime-owned receipts; see [reliable actions](actions.md).

## Use generated fields

Each `<field>` belongs inside its own static schema form. It needs a schema field
name, a nonblank Text label, and a mutable draft binding. Diamond owns its ID,
label association, description link, error link, native kind, and constraints.
Every schema field must occur exactly once. Field controls cannot be hidden,
password, or file inputs; use the dedicated account/file APIs for those flows.

| Schema kind | Draft binding | Native control |
| --- | --- | --- |
| Text, bounded Text | `bind-value` to Text | Text input; `multiline="true"` selects textarea |
| Int, nominal ID | `bind-value` to Text | Text input with checked scalar decoding |
| Bool | `bind-checked` to Bool | Checkbox |
| Unit enum | `bind-value` to Text | Select with exactly the public variants |
| One-level Option of a scalar | `bind-value` to Text | Empty draft means None; Bool/enum use a select with “Not set” |

`description` is optional Text. Dynamic labels validate before rendering; labels
contain 1–256 scalars and cannot be whitespace. Empty image descriptions must be
explicit (`alt=""`) for decorative images. Diamond requires an image's `alt`
attribute, but the author must decide what the image means in context.

Submitting an invalid form retains ordinary drafts, associates fixed error
messages with their controls, focuses the first invalid control, and makes no
action request. Native keyboard controls continue to work. A `<submit>` needs a
nonblank `label`; it disables while the owner is pending. A `<status value={text}>`
is a polite, atomic live region of at most 512 Unicode scalars. Identical text
is not rewritten. These primitives implement the associations described by the
[W3C labeling tutorial](https://www.w3.org/WAI/tutorials/forms/labels/) and
[notification tutorial](https://www.w3.org/WAI/tutorials/forms/notifications/).
Their presence alone does not certify a whole application's accessibility.

The existing typed-input transport limits apply: drafts are bounded to 16 KiB,
the generated text-control maxlength is at most 8,192 UTF-16 code units, and the
submission request is bounded. This profile does not offer rich-text editing,
custom validation callbacks, slots, or arbitrary browser callbacks. Raw HTML
controls remain available under their separate authored-label contract. IDs
beginning with `diamond-control-` and `diamond-field-` are reserved.

## Identity and lifetime are runtime responsibilities

Login, logout, verification, password reset/change, ending sessions, and account
disable/enable start an identity barrier. Diamond clears component state to its
initial values, disposes old children and pending outputs, closes watches, and
clears selected files. The initiating root handler may finish with its verified
result, then reload authorized data. The application does not need a list of
private state fields to erase.

These operations must be direct assignments in the root component before any
other remote work in that handler. Wrapping a login in a helper/match expression,
putting it in a child, or doing a remote read first is a checked error. Pure setup
assignments before the transition are allowed. This restriction makes the reset
boundary explicit and prevents old event proposals from restoring private state.

Choose keys that identify the edited resource, and tie the editor's presence to
the current application context. The Forge and live workspace examples unmount
their editor when its repository or workspace changes. Diamond cannot infer those
application relationships from arbitrary IDs.

The [LP4 evidence ledger](../verification/lp4/README.md) records observed native,
HTTP, PostgreSQL, and Chrome behavior, retained failures, and accessibility limits.
