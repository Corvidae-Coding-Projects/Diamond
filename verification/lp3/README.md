# LP3 typed navigation and forms evidence

LP3 is complete for the finite scalar route/form profile in
[the current guide](../../docs/typed-inputs.md) and
[the decision record](../../.design/lp3-routes-forms.md). This is bounded language
qualification, not a whole-codebase security review or production release.

The tested binary SHA-256 is
`41e31757fd9507a9eaa9f1557cd29271f3fec03b7bdf3c4b663768b576f763ee`.
Versions are language 0.18.0, artifact 3, wire 5, manifest 1, API 1. The reference
app contract is `54cd629abd535f5bb408697de62233d7212ea748f0b49bd94d243f8f010bb5ba`.
These describe the working tree; HEAD still names the earlier committed source.

## Observed acceptance

| Requirement | Real consumer evidence |
| --- | --- |
| LP3-P1 / AC-3: identical deep-link meaning | [browser.json](browser.json): typed B/page2/Unicode link, query-only navigation, back/forward, reload and fresh tab. [Independent campaign](independent.json): 59 exact URLs through native public render, static HTTP and connected HTTP |
| LP3-P2 / AC-4: same declared scalar values | [results.json](results.json): protected action and independent SQL for Text, Int, Bool, nominal parent ID, unit enum and Option. Browser save/reload agrees; [independent URL-encoded/wire decoder campaign](independent.json) checks native parity |
| LP3-N1: URL hostile corpus | Native/static/connected consumers reject malformed/noncanonical percent/UTF-8, wrong/missing/extra/duplicate fields, encoded separators/controls, overflow, wrong integer spelling, overlong text and invalid path shapes. Typed Text href and overlaps reject at compilation |
| LP3-N2: invalid drafts and recovery | Actual Chrome preserves `-`, overflow, empty/121-scalar Unicode title and ordinary note, displays associated fixed-code errors, dispatches no action for those failures, then commits the corrected fields |
| LP3-N3: raw bypass | Strict duplicate JSON, extra/created_by, malformed Unicode, wrong nominal tag, Bool/enum/Option shapes and invalid CSRF reject before mutation. Current membership/parent policy still denies syntactically valid unauthorized IDs |
| LP3-N4: sensitive draft boundary | Schema password controls explicitly reject at compilation; restricted/nested Secret/server-derived schemas reject at the actual sink. No password draft submission/persistence facility is accepted. Errors contain fixed messages, not raw values. Dedicated account operations retain their separate contract |
| Full URL lifetime | Chrome delayed a completed save, changed only the query, mounted the new view and read committed data, then delivered the old reply. No old child output reached the parent; a later valid save succeeded |

The main backend driver made 65 observations; the separate verifier made 211.
The [compiler/native corpus](compiler.json) made 35, including real execution of
the bounded optional literal regression and old artifact rejection. These counts
describe distinct runs and overlap; they are not coverage percentages.

## Fixes and source decisions

- A valid `Some("雪😀")` passed to a bounded optional record field initially
  failed with `D110: expected Option<Text<length: 0..80>>, found Option<Text>` in
  the real example compile. Complete literal fitting now handles Some values
  before rejecting a record argument. The minimized reference-evaluator case is
  [lp3_optional_bounded_literal.dia](../regressions/lp3_optional_bounded_literal.dia).
  The initial executable hash was not captured, so this is not a before/after
  hash comparison. The final CLI and browser case both pass.
- Schema forms now own `method="post"`; source cannot override it. This is
  default-path hardening from source/DOM inspection, not a claimed exploitation
  reproduction. Native fallback cannot append drafts as a GET query or reach an
  application mutation. The independent SSR check and Chrome DOM confirm POST.
- The first HTTP corpus incorrectly treated a literal `#fragment` as query field
  data. The `http` crate's URI parser strips fragments before Diamond. The oracle
  was corrected with that primary source inspection; `%23` field data remains a
  rejecting case, and the native full-URL render API rejects a literal fragment.
- Unit-enum query defaults are explicitly unsupported non-literals (D900), as
  are Some defaults for optional route queries. Int/Bool/Text defaults and absent
  optional values were exercised. No expectation was changed to claim those
  unsupported modes work.

## Source/caller coverage

| Area | Source reviewed and exercised |
| --- | --- |
| Declarations/defaults | `parser.rs`, `checker.rs`, `scalar_input.rs`: schema fields, literal defaults, finite codecs, full literal fitting; actual CLI/reference program |
| Canonical routing | `project/schemas.rs`, `schemas/routes.rs`: field kinds, prefix/reserved/overlap rules, defaults/Option, exact percent round trip and builder; native render and both real HTTP servers |
| Form codecs | `forms.rs`, `eval.rs`, `scalar_input.rs`, `project/lower/forms.rs`: legacy/native reuse, matching controls, generated constraints/errors, unsafe sink rejection; real URL-encoded service and checked browser |
| Managed browser | `project/inputs.js`, `interactions.js`, `runtime.js`, `browser.rs`: scalar metadata/decoding, bounded drafts, fixed error associations, full URL state and generations; actual Chrome and source review |
| Native routing/publication | `project/render.rs`, `server.rs`, `connected.rs`, `mod.rs`: typed href/outlets, full request target, isolated public shell, guarded cross-site documents, artifact/wire versioning; direct native/static/connected interfaces and loader |

Unchanged action/policy/wire consumers were exercised through actual saves and
raw inputs; this does not claim an unrelated adapter audit. The
[LP2 regression replay](lp2-regression.json) passed 62 action/authority/proof/
rollback/abort cases on this binary. The [baseline](baseline.json),
[L1](l1.json)–[L7](l7.json) compiler replays, and [example catalog](examples.json)
cover directly affected existing paths. Two retained compiler observations are
source markers, not runtime execution. Earlier checkpoint receipts remain historical.

## Reproduce and limits

```sh
cargo build --locked
python3 verification/lp3_compiler.py
python3 verification/lp3_inputs.py
python3 verification/lp3/independent_inputs.py
python3 verification/lp2_spine.py --language-version 0.18.0 \
  --format-version 3 --wire-version 5 --report .diamond/lp3-lp2-regression.json
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all-features
cargo fmt --check
```

PostgreSQL tools must be available. `lp3_inputs.py --serve` runs the actual
Diamond app at http://127.0.0.1:39171/, prints synthetic credentials, and accepts
stdin `observe`, `arm`, `release`, and `stop` for the browser sequence documented
in browser.json. The gate only forwards/delays actual bytes; it supplies no
counterfeit UI, codec, authority or business response. Owned fixture servers and
PostgreSQL clusters were stopped and temporary data removed after verification.

The final [quality record](quality.json) pins source fingerprints and build,
Clippy, test/doctest, formatting and diff checks. MSRV remains 1.96; no dependencies
or unsafe code were added. New language/IR semantics use the minor 0.18.0 version.
The final Rust run passed 10 existing unit tests and 38 doctests; formatting and
warning-free all-target/all-feature Clippy passed. Miri was not applicable because
the crate forbids its own unsafe code and none was introduced.

Browser coverage is one Chrome environment. There is no cross-browser, prolonged
resource, formatter/editor or full-DOM transaction qualification here. General
nested forms, arbitrary validation callbacks, password draft transport and enum
defaults are not accepted features. Control ergonomics/focus/reuse remain LP4;
durable action receipts/recovery remain LP5. Native URL-encoded text acquires its
nominal type from the declaration; JSON wire carries/checks a tag. Neither grants
authority. No pipeline work was included.
