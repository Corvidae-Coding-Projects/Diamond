# Checkpoint-four findings and repair decisions

Baseline: package 0.3.0, executable SHA-256
`8dd247c6ac73f49704bc56c8607a0eb7c1dba5c0c674774b1334457d0c1285f7`.
The original executable and source were copied to an isolated temporary directory
before repairs. Concrete source fixtures and normal-interface replays are retained
in `verification/regressions/` and the enforcement drivers.

| Finding | Confirmed baseline | Decision before editing |
| --- | --- | --- |
| Explicit HEAD exposure | Private HEAD returns 200 via earlier public GET; public HEAD returns 401 via earlier private GET. | Category B: exact method routes precede GET fallback, independent of declaration order. |
| Undeclared HTTP database | A source without a database declaration starts with `--database` and creates that file. | Category B: validate the source declaration before opening the host path, consistent with native evaluation. |
| Unreachable anonymous sessions | A public failing GET creates an anonymous registry entry but returns no cookie. | Category B: transport the newly issued cookie on the sanitized failure response as well as success, so subsequent requests can reuse the session. |
| Transaction scope checking | Undeclared, direct nested, and transitively nested transactions pass checking and fail only during execution. | Category A: infer transaction-opening behavior through the closed call graph and revalidate nesting; require the source database declaration. Preserve data effect budgets. |
| Unsupported input/storage types | List-valued database fields/forms and reserved/derived form fields pass checking and fail only at their runtime sinks. | Category B: check the supported SQLite/form scalar subset and reserved fields before deployment. |
| Partial invalid-row selections | A malformed hidden row is filtered away and selection returns a successful valid partial list. | Category A: validate the bounded base relation in the same read snapshot before returning policy-filtered/paginated results. Failure or budget exhaustion returns no partial result. |
| Malformed stored IDs | A valid lookup returns an owner ID containing slashes without a type error. | Category B: enforce opaque identifier shape wherever a value is checked against Id, including nullable stored IDs. |
| Unknown correlation lost at HTTP | Actual journal-unlink acknowledgment loss returns 503 and leaves one committed row, but omits the operation identifier. | Category B: preserve the opaque correlation ID in the generated 503 response; retain connection discard and no-retry wording. |

The actual SQLite tests distinguish a blocked COMMIT followed by confirmed
rollback (409, zero rows, connection reusable) from acknowledgment loss after
the real journal deletion (503, one committed row, poisoned connection denied).
The baseline transaction classification itself is correct; no replacement of
that guard is warranted. HTTP correlation transport is the defect.

These changes preserve native API signatures and the declared Rust 1.96 minimum.
Source programs using unsupported transaction/sink shapes now fail earlier;
invalid persistent data fails closed. No lint suppressions are proposed.
Final verification and the reviewed-source record are recorded separately.

## Final disposition

All eight rows above have passing final replays on package 0.4.0. The supported
scalar policy/effect/scope/sink paths reviewed without a reproduced defect retain
their implementation; the campaign supplies positive and negative evidence for
those paths as well. Additional case-colliding column and derived-ID checks are
contract-focused regressions, not claims of separately observed original failures.

- [checkpoint_four_results.json](checkpoint_four_results.json): all 13 final
  commands pass, with stable before/after input fingerprints and executable
  SHA-256 `c24890807ddfa962e37d44dfbd031d9166182ea40b5b4fcf56b45e6d45995b95`.
- [checkpoint_four_transactions.json](checkpoint_four_transactions.json): full
  24-case final CLI/HTTP fault observations, independent persisted rows, operation
  IDs, poisoned-connection denials and successful fresh-process readbacks.
- [checkpoint_four_transactions_before.json](checkpoint_four_transactions_before.json):
  12-case intermediate pre-HTTP-repair run, 10 passes and two missing-ID failures.
  Its fingerprint differs from the original 0.3.0 raw baseline and is recorded
  explicitly; both independently reproduced the same HTTP correlation defect.
- [checkpoint_four_coverage.md](checkpoint_four_coverage.md): complete authored
  source inspection record, actual consumers/recovery paths, behavioral evidence,
  dependency boundaries and unexecuted schedules.

The final API/manifest check confirms only the package version changed in the
lockfile; dependency versions and features are unchanged. No public native
signature changed. All required quality checks pass without suppressions on
Rust/Cargo1.96.0, including eight doctests and the real embedding consumer.
There is no authored unsafe Rust path requiring a Miri replay. The verification
shim is C code, separately compiled with warnings denied and confined to exact
disposable child/journal paths; that test is not a Miri or power-loss result.

The pre-edit snapshot, successful temporary databases, fixture credentials,
processes and browser tab have been removed. Minimal source regressions, driver
recipes, receipts and the observed browser screenshot remain in the project.
