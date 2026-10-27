# LP1 — Current language contract and baseline

**Complete, 2026-10-27.** LP1 establishes the specification, profile differences,
security invariants, and independent scenarios required by its exit gate. It does
not implement LP2 syntax or qualify the full production-readiness plan. Overall
AC-1 remains open until LP7/LP8 update and qualify the expanded language/guides.

Deliverables:

- [Canonical implemented contract, C01–C18](../../docs/language-specification.md)
- [Profile, restricted-sink, budget, and drift matrices](../../docs/language-profiles.md)
- [Positive and adversarial LP2–LP8 scenarios](SCENARIOS.md)
- [Real CLI/build/HTTP/SQLite driver](../lp1_contract.py) and
  [60 fresh observations](results.json)
- [Browser fixture](browser/diamond.toml), [eight browser observations](browser_results.json),
  and seven fresh L1–L7 compiler/CLI baseline reports linked below

## Candidate and observations

Runtime source: `4c0195faaa44fe42c1619b38d6532ae7dc33c101`. No Rust/JavaScript runtime
implementation changed during LP1. The new documents, fixture, and verification
driver are additional checkout files, so HEAD alone does not identify those
additions. Browser fixture digests are recorded in its receipt. The assembled
runtime built with `cargo build --locked` has SHA-256
`9be3c601b87ddc61272d67a14a880614d0841d4933252d74cb3dad4f74116b29`.

| Fresh observation | Outcome | Evidence boundary |
| --- | --- | --- |
| Literal scalar/control cases S01–S19 | 19 passed | Real `.dia` execution, expected values stated literally before execution |
| Rejected/failing cases N01–N13 | 13 passed | Real checker/runtime; diagnostics and failure return codes, not a crash |
| Generated integer cases G00–G15 | 16 passed | Seed 53665; independent Python integer oracle, small valid values |
| Transaction regressions | 2 passed | Real SQLite file independently inspected: zero rows after returned/caught transaction failure |
| Project/artifact/server cases P01–P10 | 10 passed | Server shared result; versions; rechecked load; SSR text/escaping; public canary; tampered/incompatible artifacts; real HTTP; owned shutdown |
| Browser B01–B08 | 8 passed | Actual Chrome UI + read-only DOM inspection; shared semantics, hydration, text safety, failed-update preservation, native select, recovery |
| [L1](l1_baseline.json) | 18 passed | Markdown output/budgets, Html forgery, real nested-secret entry/RPC rejection |
| [L2](l2_baseline.json) | 10 passed | Opaque-file compiler restrictions plus one emitted-runtime marker inspection; no file-transport runtime qualification |
| [L3](l3_baseline.json) | 13 passed | Stream compiler/descriptor restrictions plus one emitted-runtime marker inspection; no new live revocation/socket campaign |
| [L4](l4_baseline.json) | 11 passed | Declarative-mail compiler restrictions; no provider delivery campaign |
| [L5](l5_baseline.json) | 15 passed | Workflow checked-shape/effect restrictions; no new worker fault/concurrency campaign |
| [L6](l6_baseline.json) | 10 passed | Declarative-secret compiler/configuration restrictions; no credential rotation/provider campaign |
| [L7](l7_baseline.json) | 15 passed | Query compiler restrictions; no new relational planner/pagination/concurrency campaign |

Reported baseline totals: **152 fresh automated observations** plus **8 observed browser cases**.
Counts describe distinct observations, not security coverage percentages.
Two retained L2/L3 observations inspect emitted JavaScript markers; they do not
execute Blob/Stream runtime behavior and are not counted as real runtime paths.
N09 rejects a Text-forged secret field at type checking; it does not independently
establish nested-secret escape prevention. L1's valid secret-bearing record/enum
entry and record-RPC cases supply that compiler evidence instead.

The browser fixture is pure and requires no database, accounts, file uploads, or
external adapter. Server CLI and SSR return this same expected semantic summary;
the real browser event then computes it independently in the emitted runtime:

```text
9007199254740993|3|-2|-0.000002|😀|·a·b·
```

Failed division and duplicate options display a generic error while preserving
the preceding valid state. Valid recovery clears the error. This verifies these
specific precommit failures, not arbitrary DOM patch exception atomicity.

## Contract-to-source-to-consumer map

The source review was targeted at the listed definitions/callers. This is a
coverage record of LP1 contract reconnaissance, **not a whole-file/whole-codebase
audit**. Source-backed behavior without a fresh consumer observation is labeled
as such rather than inferred from another test's success.

| Contract | Reviewed implementation and consuming boundary | Fresh evidence / gap |
| --- | --- | --- |
| C01 source/declarations | [lexer](../../src/lexer.rs) token/string/indent paths; [parser](../../src/parser.rs) declarations, `component`, type/expr/block parsing; [syntax](../../src/syntax.rs) checked shapes/integer serialization | Real fixture compilation; N04/N05/N11; exhaustive lexical/declaration fuzzing deferred |
| C02 module/checked-program boundary | [project](../../src/project/mod.rs) manifest/module validation, `check_server`; [resolver](../../src/project/resolve.rs) import/export and qualification; [HIR](../../src/hir.rs) checked structures | P01/P02/P06 + L baseline project cases; full import-graph campaign historical |
| C03 order/control | [checker](../../src/checker.rs) `block`, `expr`, `call`, match/return checks; [eval](../../src/eval.rs) block/call/match/for; [runtime](../../src/project/runtime.js) `__block`, `__eval`, `__call` | S01/S05/S06/S17; N05/N06/N11–N13; browser shared expression; record-order/complex recursion source-backed |
| C04 numbers | eval checked arithmetic; [standard](../../src/standard.rs) signatures/evaluation/rounding; runtime `__int`, `__round`, `__builtin` | S02–S04/S12–S15; N01/N02/N04; generated G cases; B03; complete numeric cross-product deferred |
| C05 text/lists | standard slice/replace/trim/list operations; eval comparisons/materialization; runtime scalar comparison/range/text/value checks | S07–S11/S16/S18; N03; browser shared summary; broad grapheme/normalization corpus deferred |
| C06 effects/IDs/authority | checker function-effect fixed point, budget/routes/ID checks; [authority](../../src/authority.rs) borrowed `Scope::require`; [value](../../src/value.rs) nominal fits; project/lowerer grant boundaries | N07/N10, L compiler cases; real missing-grant/revocation execution not rerun here |
| C07 results/errors | [error](../../src/error.rs), evaluator Try/transaction outcomes, [wire](../../src/project/wire.rs) failure codecs, [rpc](../../src/project/rpc.rs) wrapper/result boundary, runtime `__unwire` | S16/S17, transaction regressions, L cases; complete typed public application-error HTTP matrix not rerun |
| C08 transaction/external effects | eval transaction/caught failure; [data](../../src/data.rs) adapter boundary; [project storage](../../src/project/storage.rs) request transaction/poisoning; [relational store](../../src/relational/mod.rs); project `check_effect_boundaries`; [workflow](../../src/project/workflows.rs) effects | Two real SQLite rollback regressions; static L5 constraints; PostgreSQL commit-loss/worker failure paths source/historical only |
| C09 current policy | [policy](../../src/policy.rs), [PostgreSQL policy](../../src/policy/postgres.rs) checked selection/update paths; [restrictions](../../src/relational/restriction.rs) per-base/poststate attenuation; adapter account/parent gates in jobs/workflows/files/events | Compiler baselines; multi-server/race and delayed-work observations remain historical, not LP1 passes |
| C10 sinks | Entire [security classifier](../../src/security.rs); checker/integration signature guards; project main/RPC checks; [lowerer](../../src/project/lower.rs) managed types; wire safe_type; named files/events/secrets/jobs/workflow boundaries | L1–L7 compiler cases incl genuine nested secret rejection; no blanket dynamic sink audit claimed |
| C11 output/forms/URLs | [template](../../src/template.rs) URL/markup validators; eval Html; [rich text](../../src/rich_text.rs) sanitizer; [forms](../../src/forms.rs) strict fields/record parser; lowerer attribute whitelist | L1 Markdown cases; P05 and B02; raw form parser campaign historical |
| C12 component lifetime/state | lowerer `Node`/`Attribute` and checks; [render](../../src/project/render.rs) native select/each; runtime initial/environment/render/dispatch/key/select paths | B01–B08; broad lifecycle/focus/reorder/unmount and DOM patch failure cases deferred |
| C13 cancellation/epochs | runtime remote deadline, auth epoch, dispatch/input/dispose/watch paths; [connected](../../src/project/connected.rs) owned blocking permits; [pool](../../src/relational/pool.rs) lease ownership | P10 owned static server shutdown; pending mutation/revocation/resource cleanup campaign not rerun |
| C14 navigation/SSR | lowerer literal Route; render pure route selection; runtime navigation/hydration/normalization paths | Root SSR/hydration B01/P05/P09; route/history/delayed-hydration corpus historical |
| C15 wire/privacy | wire strict codec/shape/depth checks; API/RPC exports; [browser emitter](../../src/project/browser.rs) reachable maps; project server public allowlist | P06 server canary exclusion; L unsafe-signature cases; malformed raw JSON campaign historical |
| C16 compatibility | project FORMAT_VERSION/schema checks; [artifact](../../src/project/artifact.rs) emit/load/recompile/compare; [API](../../src/project/api.rs) version contracts | P03/P04/P07/P08; no mixed-version negotiation implemented |
| C17 budgets | lexer/parser; value weight; evaluator fuel/call/value work; runtime depth/fuel/render; render bytes; connected body/admission; pool/host timing | L1 Markdown boundaries; arithmetic failures/recovery; most exact limit and prolonged-run observations deferred to LP6/LP8 |
| C18 evidence/change policy | These reports and [future scenarios](SCENARIOS.md); no executable production mechanism is claimed | Fresh evidence separated from source reasoning and old receipts |

## Drift decisions and inherited protections

The [drift register D01–D12](../../docs/language-profiles.md#differences-and-drift-register)
classifies intentional profile differences, known limitations, corrected guide
drift, and evidence gaps. Current docs link to the canonical contract. Historical
W1/W2/W3 snapshots are explicitly identified; they retain their original evidence.
The single-file guide's scalar list/call rules and the reactive guide's error-code
list now agree with source. No runtime behavior was broadened to repair prose.

LP2 inherits effect checking, separate host grants, default-deny policies,
restricted-value classification, strict wire input, ID nominality, secret
injection, transaction poisoning, current-authority checks, and uncertainty/no
automatic replay. These are prerequisites to preserve, not features to rebuild.
General action identities/receipts are not inferred from existing workflow fences
or adapter-specific deduplication.

## Replay

```sh
cargo build --locked
python3 verification/lp1_contract.py --report verification/lp1/results.json
python3 verification/secure_language_l1.py --report verification/lp1/l1_baseline.json
python3 verification/secure_language_l2.py --report verification/lp1/l2_baseline.json
python3 verification/secure_language_l3.py --report verification/lp1/l3_baseline.json
python3 verification/secure_language_l4.py --report verification/lp1/l4_baseline.json
python3 verification/secure_language_l5.py --report verification/lp1/l5_baseline.json
python3 verification/secure_language_l6.py --report verification/lp1/l6_baseline.json
python3 verification/secure_language_l7.py --report verification/lp1/l7_baseline.json
```

The driver creates its own temporary source/build/SQLite files under ignored
`.diamond/`, starts a loopback HTTP server on an available port, verifies actual
state, and stops its owned server. It does not run PostgreSQL or contact providers.
The L baseline scripts check real compiler/build output; L1 also executes actual
Markdown programs. They do not run native embedding consumers or launch the
earlier full runtime fixtures.

For actual browser replay:

```sh
target/debug/diamond serve verification/lp1/browser/diamond.toml --listen 127.0.0.1:39171
```

Open http://127.0.0.1:39171/ in a real browser, follow B01–B08 in the
[browser receipt](browser_results.json), and compare the visible/DOM values.
Stop the owned fixture with Ctrl-C. The checked-in browser receipt is a manual
observation record from browser control, not output manufactured by the CLI driver.

## Exit-gate assessment and remaining coverage

- Implemented behavior is inventoried for S/PS/SH/B, with concrete source and real
  consumer interfaces, limits, security sinks, compatibility policy, and drift.
- Every later checkpoint has positive and adversarial scenarios with observable
  expected state/failure and a named consumer. All remain planned.
- Fresh samples agree with the documented current semantics; no new confirmed
  runtime defect was reproduced. Documentation inconsistencies were corrected or
  labeled as historical; known language limitations remain explicit.
- Existing [L8 qualification](../secure_language_l8_coverage.md), W/G runtime
  ledgers, and old release results remain historical. LP1 did **not** repeat their
  whole PostgreSQL/account/provider/Git/runner/restore/browser campaigns or inspect
  every implementation caller and recovery path.
- Firefox/WebKit, assistive technology, sustained resource/cancellation stress,
  malformed-wire fuzzing, and full concurrent policy/adapter faults were not run.
  They are required at the appropriate LP6–LP8 gates. A clean bounded baseline is
  not proof of universal security, process isolation, or production readiness.
- No pipeline, deployment, runtime feature, commit, or push is part of LP1.

Final document checks also passed: local link targets; all 18 contract/source
entries; positive/negative scenarios for all seven later checkpoints; matching
binary/driver/fixture digests; Python syntax; and `git diff --check`. The canonical
Diamond example (with a minimal main) and the five recommended pure function
examples ran successfully. The owned browser fixture stopped cleanly and its
loopback listener was absent afterward. These checks are supplementary to the
reported baseline counts above.
