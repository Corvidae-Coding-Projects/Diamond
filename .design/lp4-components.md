# LP4 components and native controls

Status: complete, 27 October 2026. Scope is LP4/AC-5 and the predeclared
LP4 scenarios in verification/lp1/SCENARIOS.md, preserving prior checkpoint work.

Implementation and final evidence: [component guide](../docs/components.md) and
[LP4 ledger](../verification/lp4/README.md). Language 0.19.0 / artifact 4 / wire 6.
Identity operations require a direct root assignment before other remote work;
the runtime resets all component state while retaining the initiating handler.

## Source contract chosen before implementation

```diamond
<form schema="EditIssue" as="value" on-submit={save(value)}>
  <field name="title" label="Title" description="Choose a short title." bind-value={draft}></field>
  <field name="published" label="Published" bind-checked={published}></field>
  <submit label="Save issue"></submit>
</form>
<status value={message}></status>
```

- Fields belong to one static schema form. The compiler chooses native Text/
  checkbox/select/textarea controls from the checked scalar kind; `multiline`
  is allowed only for Text. Field labels are required author-supplied nonempty
  Text, optional descriptions are checked Text. Dynamic names validate before
  rendering. IDs and label/description/error references derive from keyed
  instance identity in both SSR and browser; authors never construct them.
- Submit is a native submit button with a required label and runtime-owned
  pending disabling. Status is a native polite/atomic live region with at most
  512 Unicode scalars. Identical messages are not rewritten. Invalid submission
  focuses the first invalid field, retains ordinary drafts, and publishes fixed
  messages without echoing values. No screen-reader compatibility certification
  is inferred from ARIA attributes alone.
- Existing typed handler/output machinery is reused: checked positional data,
  child postcommit emission, exact parent signatures, ancestor-only delivery,
  shared 64-message/30-second chain, generation/binding/auth/lifetime fences.
  Static component recursion and native authority/closure capture reject.
  A failed 65th message cannot erase the preceding valid committed states.
- Reordering moves surviving native nodes without losing focus/selection or
  drafts. Removed nodes do not transfer focus or state to replacements. Identity
  transitions dispose private child state/pending work while allowing the
  initiating authentication handler to finish and apply its verified result.
- Images require an explicit alt attribute; empty alt is an author's explicit
  decorative choice. Diamond cannot infer contextual descriptions. Generated
  controls and submit labels cannot be empty. Legacy raw controls remain a
  separate authored-label profile; new journeys use the managed primitives.
- Forge cards open their exact typed repository/issue and child editors report
  typed completion. Live workspace uses the same pattern. Neither journey asks
  users to select/copy IDs, nor application authors to construct field IDs.

## Qualification

Before fixes, reproduce focus/selection behavior using actual real-service live
updates while B remains focused. After implementation, exercise ABC→DABC→DCAB,
unrelated live updates, validation and recovery, delayed save/removal/replacement,
logout/login, duplicate identities, 65 outputs and a subsequent valid event.
Inspect native labels/IDs/errors, keyboard activation and statuses in a real
browser; exercise screen-reader observation where available and record limits.
Run both example journeys with protected independent SQL readback. Preserve
reproducers, final hashes, source coverage and bounded conclusions. Version the
changed IR/language; no upgrade compatibility or pipeline work is included.
