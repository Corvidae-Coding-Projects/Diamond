# Typed URLs and forms

Diamond 0.20.0 builds internal URLs and decodes form drafts from shared schemas.
The compiler and runtime own canonicalization, parsing, bounds, error association,
and strict server validation. The [runnable example](../examples/typed-inputs/)
uses all supported scalar kinds and a protected PostgreSQL save.

## Declare a route

```diamond
export type IssueId = Id<"issue">
export route IssuePage(id: IssueId, page: Int = 1, search: Option<Text<length: 0..80>>) = "/issues/{id}?page={page}&search={search}"
```

Import the shared module into both server and browser modules. Build links with
named record fields, then read typed fields in the outlet:

```diamond
<a href={IssuePage(id: row.id, page: 2, search: Some("雪😀"))}>Open issue</a>
<route name="IssuePage" as="detail">
  <p>{detail.id}</p><p>{detail.page}</p>
</route>
```

The checked URL is `/issues/…?page=2&search=%E9%9B%AA%F0%9F%98%80`.
`IssuePage(id: row.id)` omits the default page and absent search, producing
`/issues/…`. Dynamic Text is not a URL. An ID names a resource; current policies
still decide whether the user can read or change it.

| Field | Path | Query |
| --- | --- | --- |
| Nominal ID | Yes; canonical ASCII identifier | Yes |
| Int | Canonical signed i64 decimal | Yes |
| Bool | `true` or `false` | Yes |
| Bounded Text | Nonempty; maximum at most 256 scalars | Yes; empty when its bounds allow |
| Unit enum | Exact public variant name | Yes |
| One-level Option of those kinds | No | Missing is None; present empty optional value rejects |

Every declared field must appear once. Query keys equal their field names.
Path fields cannot have defaults. Required query fields must be present; query
Int/Bool/Text defaults use checked literals. Optional fields default to None.
Unit-enum defaults and Some defaults for route queries are unsupported and
reject explicitly. An optional alias may require an explicit `= None` if you
want omission in its record constructor; decoding an absent query still yields
None. General record constructors otherwise keep their normal field rules.

## Canonical URL rules

- Query keys sort lexically. Defaults and None are omitted. Noncanonical requests
  reject instead of redirecting; `?page=1` rejects when the default is 1.
- Percent escapes decode once as strict UTF-8. Re-encoding must reproduce the
  input: uppercase hex, unreserved ASCII emitted literally, space as `%20`.
  `+` is never interpreted as space in a route. Encoded unreserved characters,
  lowercase escapes, malformed UTF-8, duplicate/unknown keys, and empty query
  markers reject.
- Decoded controls, `/`, `\\`, `%`, `#`, and `?` reject as field data. Text path
  values cannot be empty, `.` or `..`. Unicode is preserved without normalization.
  Safe punctuation such as `&` and `<` is encoded in the URL and rendered as text.
- Int rejects `+1`, `01`, `-0`, whitespace, overflow, and unfinished `-`.
- Literal namespace prefixes, reserved-path checks, and overlap rejection prevent
  a typed route from taking over runtime/API/assets namespaces. Query differences
  do not make two overlapping pathname patterns unambiguous.
- A URL is at most 2,048 UTF-8 bytes, with at most 16 path segments, eight path
  parameters, and 16 query fields. These are separate from scalar bounds.

Direct GET/HEAD, the public render API, client links, hydration, reload, and
history use full pathname+query. A query-only change remounts named-route
descendants and invalidates their pending work. Root state survives client
navigation. Public initial shells never execute private reads or mutations.
Ordinary browser fragments remain separate from route field data. The HTTP URI
parser discards a literal fragment before Diamond sees a request; encoded `%23`
field data is rejected by Diamond. Typed builders never append a fragment.

## Declare and submit a form

```diamond
export enum Status:
  Open
  Closed
export form EditIssue(title: Text<length: 1..120>, estimate: Int, published: Bool, parent: WorkspaceId, status: Status, note: Option<Text<length: 0..80>>)
```

Inside a component, bind controls to ordinary Text/Bool drafts and pass the
decoded record to a typed event:

```diamond
on save(value: EditIssue):
  result = action.rename(issue, value)
```

```diamond
<form schema="EditIssue" as="value" on-submit={save(value)}>
  <label for="title">Title</label>
  <input id="title" name="title" type="text" bind-value={draft}>
  <label for="estimate">Estimate</label>
  <input id="estimate" name="estimate" type="text" bind-value={estimate}>
  <label for="published">Published</label>
  <input id="published" name="published" type="checkbox" bind-checked={published}>
  <label for="parent">Parent</label>
  <input id="parent" name="parent" type="text" bind-value={parent}>
  <label for="status">Status</label>
  <select id="status" name="status" bind-value={status_draft}>
    <option value="Open">Open</option><option value="Closed">Closed</option>
  </select>
  <label for="note">Optional note</label>
  <textarea id="note" name="note" bind-value={note}></textarea>
  <button type="submit" disabled={pending}>Save</button>
</form>
```

These fragments require the surrounding types, component properties/state, and
action binding in the [complete example](../examples/typed-inputs/).

Text, Int and ID controls use Text drafts, so a value such as `-` can remain
editable without becoming an Int. Bool uses a checkbox or a select containing
exactly `true` and `false`. Unit enums use a select with exactly the declared
public variants. Optional Bool/enum selects also include `value=""`; optional
Text/Int/ID controls use an empty draft for None. The example's **Optional form**
demonstrates each optional kind. Arbitrary nested records/lists/options, payload
enums, file controls, hidden controls, and password controls reject.

Each field requires one static named control. Missing/extra/duplicate controls,
dynamic control structure, mismatched control kinds, and author-supplied bounds
reject. Schema forms own their POST method; it cannot be overridden to GET or an
author-selected action. If hydration is unavailable, native submission cannot
append drafts to the URL or perform an application mutation.

The compiler emits finite scalar metadata and field error slots. Invalid submit
does not dispatch the handler or action. It preserves ordinary drafts and displays
fixed codes/messages, associates each error with its control, and sets
`aria-invalid`. Codes are Required, TooShort, TooLong, InvalidInteger,
InvalidBoolean, InvalidId, and InvalidChoice; messages never quote the draft.
These are runtime-owned presentation metadata, not arbitrary author callbacks.
Correction followed by submit clears those local errors. A valid form can still
receive `ActionDenied` or another server outcome, which remains distinct from
local parsing errors.

Drafts live only in bounded component memory. Text field drafts are capped at
16 KiB UTF-8; native maxlength also bounds ordinary editing (at most 8,192 UTF-16
units, or a lower generated field limit). Declared text lengths count Unicode
scalars. Remote bodies retain their separate 16 KiB total limit. No browser draft
storage or secret replay facility is provided. Use dedicated account operations
for passwords; Secret and server-derived fields cannot become ordinary schemas.

## The authoritative server boundary

The browser decodes drafts, then emits the ordinary typed record wire shape.
The server independently validates every field/tag, canonical integer, enum
arity, Option, text bound, exact record shape, current session/CSRF, and policy
before committing. Browser validity is never a grant. URL-encoded single-file
forms reuse the native scalar codec, including unit enums and canonical integers;
they still use their separate URL-encoded transport/CSRF contract.

URL/form text does not carry a nominal tag: the checked field declaration supplies
it. JSON wire inputs carry an explicit nominal tag and must match it. Neither
representation proves membership. The example refuses a syntactically valid
parent workspace that the current resource policy does not allow.

The [LP3 evidence ledger](../verification/lp3/README.md) records real browser,
native-render, static-server, connected-server, PostgreSQL, URL-encoded and wire
observations, with replay commands and limits. Current versions are language
0.20.0, artifact format 5, wire 7, manifest schema 1, and API contract 1.
Use [managed fields](components.md) for generated labels/descriptions and first-error focus. Rebuild
older trusted source; mixed artifacts/contracts reject.
