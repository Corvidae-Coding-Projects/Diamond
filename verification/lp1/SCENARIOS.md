# LP2–LP8 observable acceptance scenarios

**Status:** LP2 and LP3 scenarios were exercised; see the [LP2 ledger](../lp2/README.md)
and [LP3 ledger](../lp3/README.md) for exact coverage and limits. LP4–LP8 remain planned expectations, not passed
tests. LP1 established these before implementation. Source spelling/IR layout was
selected in LP2. Implementers may
split cases or tighten bounds, but must not replace a required behavior with
accept-and-ignore scaffolding. Each checkpoint must record the actual candidate
source/binary/artifact hashes, inputs, interface, independent state/readback,
failure reproductions, and unexercised cases.

Use disposable apps/databases: Alice is an active member of workspace `alpha`;
Bob is an active member of `beta`; neither can access the other's private records.
An editor can update their workspace; a reader cannot. IDs use distinct nominal
types for workspace, issue, and user. A minimal non-Git issue app and Forge are
the reference consumers. Positive and negative cases must reach the actual
boundary being claimed; early type rejection alone cannot prove runtime policy.

## LP2 — Connected compiler/runtime spine

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP2-P1 | Declare a named issue-detail route, bounded title form, row-specific event, child completion output, and protected database action. Open issue A, edit title from `Old` to `New`, submit, and reload. | Each checked node reaches its intended layer; route opens A, output reaches the parent, and one authorized saved title reads `New`. | CLI check/build; direct HTTP; browser; independent SQL; fresh read |
| LP2-P2 | Put shared pure validation and the issue ID/error types in a shared module and import from server/browser. | Both consumers resolve the same declarations and agree on a valid value; public assets omit server-only source. | CLI execution; browser; built public asset inspection |
| LP2-N1 | Swap a user ID into the issue event; mismatch a component output payload or form field type; omit the required transitive effect. | Each program fails checking with a source location; no executable artifact or mutation is produced. | Real CLI diagnostics/build output |
| LP2-N2 | Request action replay/recovery before implemented support, or declare an unsupported new field/mode. | Explicit rejection identifies the unsupported construct; no accepted inert node or hidden mutation replay. | CLI and raw dispatcher; count actual saved rows |
| LP2-N3 | Call the protected action as Bob, without a session, and from a native consumer without its host grant. | Denial at the authoritative boundary and zero writes, independent of browser affordances. | Raw HTTP; native checked consumer; SQL |
| LP2-N4 | Load a previous/incompatible artifact; mix the candidate browser with an incompatible wire contract; include a nested secret in a new output/action shape. | Clear incompatibility/unsafe-type rejection before execution; no secret in artifacts, errors, or result. | Real loader; HTTP/browser; compiler and public output inspection |

## LP3 — Typed reactive routes and forms

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP3-P1 | Build the checked URL for issue A and page query 2; navigate from list, paste the URL into a fresh tab, reload, and use back/forward. | All paths select A and page 2 with the same canonical URL and parsed types. Defaults/absent optional queries agree. | Actual browser history; direct HTTP; visible record and server read |
| LP3-P2 | Submit a form containing bounded title `雪😀`, integer 17, Bool, an allowed nominal parent ID, a unit enum, and a supported optional field. | Browser/server derive equal validated values; exactly the intended fields persist. | Browser; raw API; independent SQL |
| LP3-N1 | Route/query corpus: missing required parameter, duplicate/unknown query key, malformed percent sequence, encoded separator/control, double encoding, noncanonical integer/overflow, wrong ID, oversized text, external/protocol-relative link. | Bounded rejection under the documented canonicalization rule; no alternate interpretation, external navigation, or application mutation. Resolve exact empty/default/unknown-key rules in LP2–LP3 and add them to the specification before replay. | Direct HTTP and browser navigation; handler/SQL observation |
| LP3-N2 | Enter draft integer `-`, then overflow, an empty required title, and overlong multibyte input; correct them. | Invalid drafts remain bounded without becoming validated values; field errors identify fields; a corrected submission succeeds and preserves ordinary non-secret edits. | Native input/keyboard; DOM associations; SQL |
| LP3-N3 | Bypass the UI with duplicate/extra fields, forged `created_by`, invalid CSRF, wrong nominal tag, and invalid UTF-8/Unicode. | Independent server validation rejects before the action; client validity flags are irrelevant; no writes or protected data leak. | Raw request bytes; SQL before/after |
| LP3-N4 | Submit a password/secret-like field that fails validation and revisit/reload the draft. | No echo into error payload, persisted draft, browser storage, or logs; approved ordinary fields remain usable. | Browser/response inspection; disposable runtime logs |

## LP4 — Events, component outputs, and controls

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP4-P1 | Render keyed rows A/B/C; click B's parameterized handler and submit B's child editor. | The typed B ID reaches the parent/action without manual copy/selection state; only B changes. | Browser interactions; independent row read |
| LP4-P2 | Focus B's draft, set a selection, insert D before B, reorder C/A/B, and deliver an unrelated live update. | B retains instance state, native focus, selection, and draft; generated IDs and labels still point to B. | Browser native control state/DOM; visible values |
| LP4-N1 | Remove B with a completion pending; reuse its position for D; then deliver B's delayed output. Repeat across logout/login. | No output/state/action is delivered to D or the replacement identity; owned pending work is disposed. | Delayed actual response/output; browser; SQL |
| LP4-N2 | Duplicate a component identity, declare a wrong output signature, use cross-nominal event arguments, or create an output dispatch cycle. | Static mismatch rejects; dynamic duplicate/cycle work fails at its declared bounded boundary without replacing valid state; next valid interaction works. | CLI; real browser; runtime diagnostics |
| LP4-N3 | Render repeated forms with generated controls, trigger two field errors, navigate entirely by keyboard, and inspect status announcements. | Unique IDs; associated labels/descriptions/errors; accessible names; native keyboard behavior; focused invalid field and bounded status behavior under the documented contract. | Browser native/DOM; accessibility tree; screen-reader observation where available |
| LP4-N4 | Omit a required accessible name/author-supplied image description or request a callback that captures native authority. | Checked rejection or explicit author requirement; no invented contextual description and no serializable closure/authority escape. | CLI; rendered accessible name inspection |

## LP5 — Reliable actions and reconciliation

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP5-P1 | Submit one eligible database action; then double-click and submit the same issued identity concurrently from two clients. | One database mutation/receipt; authorized clients obtain the same typed outcome. Pending transport and durable outcome remain distinct. | Actual browser/raw dispatcher; concurrent requests; SQL |
| LP5-P2 | Disconnect after commit but before the response, then reconcile the original identity from a fresh authorized client/reload. | Recovery reports the recorded success without repeating the write; no body/secret stored in browser storage. | Real response fault; fresh browser; receipt and mutation SQL |
| LP5-N1 | Interrupt before mutation, during transaction, and after commit request/acknowledgement loss. | Confirmed rollback is reported only when established; otherwise completion is unknown until safe reconciliation. A deliberately declared application Err rolls back and returns its typed rejection. | Process/network fault; independent database state; subsequent status read |
| LP5-N2 | Reuse an issued identity with changed input, principal, parent/API scope, or action version; corrupt/expire a retained receipt and retry its identity. | Explicit conflict/expiration/corruption outcome; no silent fresh execution or cross-scope result. Receipt size/count/lifetime cleanup is bounded. | Raw action/status requests; SQL and cleanup inspection |
| LP5-N3 | Revoke access/disable account after successful action but before status/result retrieval; try another user's identity and an attenuated token. | Current-authority enforcement prevents protected receipt/status/result disclosure, even for a previously successful invocation. | Fresh requests after revocation; independent protected-data canaries |
| LP5-N4 | Put HTTP/Git/unsupported external effects into a replayable action or bypass through the old raw RPC path. | Effect-mode rejection or explicit non-replayable uncertainty contract; no distributed exactly-once claim or unguarded parallel dispatcher. | CLI; actual dispatch paths; isolated external-side-effect counter |

## LP6 — Security, budgets, and cleanup

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP6-P1 | Run the supported mutation/navigation/stream/file/job journeys, then valid operations after each induced failure. | Required protections stay enabled; later operations work; owned resources return to the declared bound. | Actual runtime, browser, worker, SQL, process/task/FD/pool observations |
| LP6-P2 | Execute generated valid scalar/composite/validation programs in Rust and the browser, with independently computed results. | Equal values/error categories within common budgets, including negative division, Unicode, rounding, enum and Result paths. | Real CLI/browser; seed/corpus; independent oracle |
| LP6-N1 | Place Secret/Blob/Stream/authority shapes in every new route/form/output/action/receipt sink through record/enum/List/Option/Result wrappers; forge tags, shadow symbols, and call effectful helpers. | Full-type/checker/consumer rejection at each claimed boundary; protected bytes/authority never escape. Retain minimized valid-shape counterexamples if a defect is found. | CLI and raw/native consumers; emitted bytes and saved state |
| LP6-N2 | Race revocation, parent reassignment, token attenuation, receipt read, delayed job, and stale output. | Results follow documented transaction ordering; later denied consumers cannot reuse old authority. | Synchronized real transactions/requests; policy state inspection |
| LP6-N3 | Exercise limits below/at/above depth, fuel, work, value, render, request, receipt, and admission bounds; use slow/failed dependencies. | Explicit bounded failure/overload and cleanup, no validation bypass or uncertain write replay; valid follow-up succeeds. | Actual program/dependency behavior; time/resource measurements |
| LP6-N4 | Prolonged create/update/reorder/navigate/unmount/reconnect sequences with cancellation at different phases. | No unbounded retained tasks, descriptors, subscriptions, or pool leases under the declared workload. Separate Diamond from child-process resource use. | Seeded action sequence; sampled process and independent server state |

## LP7 — Diagnostics, formatter, editor, and guides

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP7-P1 | Format representative routes/forms/components/actions with comments and Unicode strings twice, then run before/after. | Deterministic idempotent formatting; comments/string contents preserved; parsed meaning and observable result unchanged. | Real formatter/check mode; parser comparison; execution |
| LP7-P2 | Edit an open document; request hover, definition, references, and completion; trigger a known browser event failure. | Compiler-backed types/locations; browser error maps to the Diamond event expression; no server source published. | Actual editor protocol consumer; browser map/stack; public assets |
| LP7-N1 | Give formatter/editor incomplete, invalid, huge/deep input and cancel outdated analysis. | Bounded diagnostic/explicit formatting refusal; no invented code, stale diagnostics, crash, or orphan work. | Real protocol/CLI; process state; later valid request |
| LP7-N2 | Trigger an effect chain/type error through imports and a secret-bearing diagnostic; create malformed maps. | Useful source/module/expected-actual context without secret disclosure; malformed maps never enable server-source access. | CLI/editor/browser; error/public byte inspection |
| LP7-P3 | Follow current guides from a fresh temporary source directory and run every newly recommended example; benchmark reference journeys. | Documented syntax works; measured checking/render/event timings are recorded for the actual candidate. | Real CLI/HTTP/browser; recorded workload/hardware |
| LP7-N3 | Audit every current-reference claim against C01–C18 and the profile matrix. | Historical promises are labeled; unsupported features do not appear as current; optimization preserves earlier failed-case replays. | Source/doc readback; retained behavioral campaign |

## LP8 — Assembled language qualification

| ID | Input/action | Expected observation | Real interface/state |
| --- | --- | --- | --- |
| LP8-P1 | On the frozen candidate, complete Forge and non-Git app journeys: deep link, validate/edit, child output, action/reconciliation, jobs/live state, restart/fresh client. | Both apps use checked primitives; independent saved-state readbacks match intended changes across process/client boundaries. | Real browser/CLI/HTTP/PostgreSQL/workers/native consumer |
| LP8-P2 | Repeat relevant journeys in Chromium, Firefox, and WebKit, keyboard/mobile reflow, and a practical screen-reader combination. | Specified behavior and associations hold; exact browser/assistive combinations and any gaps are named. | Actual browser/assistive interfaces, not emulation-only claims |
| LP8-N1 | Replay every retained relevant finding and LP2–LP7 adversarial path against final hashes; run fresh seeded valid/hostile program/action campaigns. | No unresolved confirmed finding within the promised scope; valid generated cases reach execution; failures have minimized reproducers. | Real consumer coverage ledger; reproducible inputs/results |
| LP8-N2 | Change an implementation/artifact after an earlier pass, then assess qualification. | Affected observations are rerun against the new hashes; old reports remain historical and cannot qualify changed code. | Candidate manifest/hash comparison; fresh behavior |
| LP8-N3 | Examine all unexercised callers, recovery paths, dependencies, budgets, and trust boundaries. | Explicit coverage/gap record; no “secure everywhere,” allocator-isolation, operational deployment, or distributed exactly-once claim from a bounded clean run. | Source-to-guarantee review and final acceptance ledger |

LP1's own fresh observations are in [results.json](results.json),
[browser_results.json](browser_results.json), and the seven `l*_baseline.json`
reports. Those are prerequisites and samples, not substitutes for the scenarios
above. Pipeline integration and deployment acceptance remain outside this plan.
