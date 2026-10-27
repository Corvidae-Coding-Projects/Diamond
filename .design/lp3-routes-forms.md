# LP3 typed routes and forms

Status: complete, 27 October 2026. Expectations derive from LP3 and AC-3/AC-4
in language-production-readiness.md and verification/lp1/SCENARIOS.md.

## Chosen contract before implementation

```diamond
export route IssuePage(id: IssueId, page: Int = 1, search: Option<Text<length: 0..80>>) = "/issues/{id}?page={page}&search={search}"
export form EditIssue(title: Text<length: 1..120>, estimate: Int, published: Bool, parent: WorkspaceId, status: Status, note: Option<Text<length: 0..80>>)
```

- Routes accept nominal IDs, canonical Int, Bool, bounded Text, and unit enums.
  Option is query-only. Every declared field occurs once, query keys match field
  names, path fields cannot default. Query defaults are checked literals; absent
  Option is None. Required query fields must exist. Query keys sort lexically;
  default values and None are omitted. Noncanonical targets reject, not redirect.
- Decode percent escapes once as strict UTF-8; re-encoding must reproduce the
  input. Emit uppercase escapes and only unreserved ASCII literally. No `+` as
  space, duplicate/unknown keys, encoded separators/controls, `%` in decoded
  values, path dot segments, external or protocol-relative destinations.
  Text retains exact Unicode scalars without normalization. Empty ordinary query
  Text is allowed by its bounds; optional empty means None in forms, but route
  Some(empty) is rejected to avoid two representations. Paths are nonempty.
- Full pathname+query, not only pathname, reaches SSR, hydration, links, history,
  route data, and descendant lifetime. IDs still confer no authority.
- Forms decode bounded text, Int, Bool, IDs, unit enums, and one-level Option of
  those scalar kinds. Text/Int/ID use Text drafts; Bool uses a checkbox or a
  canonical select; unit enums/optional Bool use select controls. Blank optional
  Text draft is None. Canonical integer rules match strict wire decoding.
- Checked schema metadata supplies finite codecs; server action codecs enforce
  the same complete declared record independently. Legacy form scalar decoding
  reuses the native codec. No user-supplied principal/created_by/secret field.
- The compiler supplies fixed-code field errors and field associations. Invalid
  ordinary drafts stay in bounded local component memory, not browser storage;
  errors never echo raw values. Correction followed by submit recovers. Schema
  password controls reject explicitly: authentication uses dedicated account
  operations, not ordinary form records. Secret types reject at every schema sink.
- No generalized query parsing, arbitrary nested form objects, file controls,
  user callbacks/regex validators, or secret-draft persistence is introduced.

## Qualification

Completed on language 0.18.0 / artifact 3 / wire 5. See the
[guide](../docs/typed-inputs.md), [reference app](../examples/typed-inputs/README.md),
and [real-interface ledger](../verification/lp3/README.md). Unit-enum defaults
and Some query defaults remain explicit unsupported non-literal/default modes;
the finite scalar/default/Option profile and all LP3 scenarios are bounded there.

Exercise a real app with all form kinds, deep links containing page=2 and Unicode
query text, browser click/fresh-tab/reload/history, invalid '-' and overflow drafts,
empty/overlong multibyte title and correction. Independent SQL must equal the
intended typed fields, and raw wrong tags/duplicates/extra/forged fields/CSRF/
Unicode must leave rows unchanged. Generate valid/hostile URL cases; run native
render and both static/connected HTTP servers, not only compilation. Retain exact
failures, final hashes, and evidence gaps. Version the changed IR/contracts.
