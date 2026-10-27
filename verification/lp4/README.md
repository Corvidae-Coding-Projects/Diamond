# LP4 — components, messages, and native controls

Qualified 27 October 2026 on the local Linux/Chrome profile. Language 0.19.0,
artifact format 4, RPC wire 6, manifest schema 1, HTTP API contract 1.
Final executable SHA256:
`6f5c7e0427abfc9483ce165ef78effbf0cdccbd8643176d2e05675f6dff5fbd7`.
This is bounded LP4/AC-5 qualification; it is not a whole-runtime security proof.

## Reproduce

```sh
cargo build --locked --all-features
python3 verification/lp4_compiler.py
python3 verification/lp4/independent/replay.py --suffix final
python3 verification/lp4_components.py --serve
```

The last command owns two Diamond servers, isolated PostgreSQL, synthetic accounts,
a restricted runtime SQL role, and a byte-transparent HTTP/WebSocket proxy. It
prints http://127.0.0.1:39171/ and disposable login values. Use only those values.
Standard input accepts `observe`, `insert`, `reorder`, `remove`, `restore`, `pulse`,
`arm`, `release`, and `stop`. `arm` holds the next completed native action response
for at most 45 seconds; the action is not replaced or simulated. `observe` reads
independent SQL and records actual dispatch/held-response counts.

The exact original focus failure is in [focus_before.json](focus_before.json).
Sign in as alice, edit B's Title, press Home then Shift+Right three times. Without
clicking another browser control, publish `insert`, `reorder`, `insert`, `pulse`
from the driver. The third change moves B's own ancestor. Check the same native
input remains focused with its draft and selection 0–3. Trigger two field errors
with Enter, repair and save, then check `observe` and the parent completion count.

For stale messages, arm a save, wait until `observe` reports the native completed
response held, then remove B with `remove`. Release, restore, and verify no parent
completion was added. Repeat using “Replace editors,” then “Sign out.” These
tests deliberately distinguish a committed database change from a discarded late
browser completion. A replacement mounts fresh state. A new identity starts with
zero completions and no old notification, forms, or selected files.

“Duplicate keys” must preserve the prior DOM and report a bounded error. “Recover
rendering” restores valid operation. B's “Emit 65 messages” adds exactly 64 parent
completions before reporting the limit; “Emit one message” then succeeds. Component
cycles and wrong message signatures fail during compilation, rather than requiring
a runtime dispatch cycle.

```sh
python3 verification/lp4_examples.py forge --port 39172
python3 verification/lp4_examples.py workspace --port 39173
target/debug/diamond serve examples/components/diamond.toml --listen 127.0.0.1:39174
```

The first two drivers use the actual example manifests and source, native adapters,
accounts, actions, and fresh PostgreSQL. They print synthetic login values and
accept `observe` / `stop`; unrelated integrations are configured but never invoked.
Forge additionally seeds two repositories and two issues through its actual RPCs.
Choose the organization by name, show its repositories, click “Open Zebra
repository,” go to Issues, and click “Edit Forge target.” No repository/issue ID
is copied or selected for that journey. Save in the child; both child and parent
show completion. Independent SQL confirms only the target changed. Changing the
repository picker unmounts the old child immediately.

In the workspace, sign in, create a workspace/project using Name, create “Workspace
alpha” and “Workspace target,” click the target's Edit button, and save in the child.
The parent refreshes the card title and completion status; SQL confirms the other
issue is unchanged. Changing Workspace ID unmounts the child. No new field/error
relationships are manually authored in either editor.

## Observed campaign

| Interface/campaign | Result and retained evidence |
| --- | --- |
| Real CLI and exported Rust renderer | 34 cases pass: [compiler.json](compiler.json). Checked negatives include missing/blank/wrong labels, wrong bindings/scalar kinds, authored IDs, missing image descriptions, output restrictions, identity placement, duplicate keys, and render budgets. Native fragment equals the fragment in the exported shell. |
| Independent CLI/native verifier | 17 cases pass: [results-final.json](independent/results-final.json). Separate expectations and generated programs; no implementation edits by the verifier. |
| Actual Chrome mixed controls | 14 native fields across repeated keyed editors; successful hydration, unique IDs, resolved labels/descriptions/errors, Unicode/punctuation key parity, keyboard checkbox/submit, canonical Int failure then minimum Int recovery, enum/Option selections, parent output, and reordered state. [browser.json](browser.json) |
| Native live updates | ABC→DABC→DCAB→DABC plus unrelated C update preserves B's control, draft, focus, and selection. Two local field errors focus Title and dispatch no action; repair gives one target mutation/completion. |
| Cancellation/lifetime | Held completed save after removal, different instance replacement, and logout never emits a stale parent completion. SQL records committed writes independently. Duplicate-key rejection and 64-delivery limit both recover on the next valid event. |
| Identity | Logout needs no authored clearing in the fixture; all private child state and parent completion state reset. Bob's later denied load does not restore it. Alice can subsequently reload fresh authorized state. |
| Actual Forge/workspace journeys | Exact card target, native checked child action, parent completion, independent SQL, context-change unmount. [forge-fixture.json](forge-fixture.json), [workspace-fixture.json](workspace-fixture.json) |
| Prior checkpoint replays | LP2 spine: 62 observations; LP3 inputs: 65; LP3 compiler/native: 35, all pass on 0.19/4/6. Receipts alongside this ledger preserve earlier checkpoint receipts. |

The directed race/budget/SQL observations are transcribed in
[directed-observations.json](directed-observations.json). All 13 example project
manifests compile; the existing function catalog executes its direct CLI/storage/
HTTP checks. [Quality commands](quality.json) and the final source fingerprint
manifest accompany these results. [Forge screenshot](forge.png) shows the native
child editor's successful final save.

Generated exploration is a finite source mutation/native corpus: 34 main checks and
17 independently specified cases, plus the retained LP2/LP3 native/HTTP corpora.
Browser sequences are the directed journeys above, not randomized browser coverage.
They exercised actual controls and native service boundaries, not injected handlers,
mock responses, or JavaScript state mutation.

## Findings and fixes

1. Moving a keyed ancestor with `insertBefore` dropped focus to BODY. The runtime
   now snapshots native focus/selection and restores the same still-connected
   element after a successful patch. Removed/replaced controls inherit neither.
   The original exact three-update sequence passes on the final build.
2. A raw authored ID could collide with the generated control namespace. Both
   native and browser renderers now reserve the generated prefixes before patching
   or publishing artifacts. Original valid-shape reproducer and final rejection:
   [raw_id_collision](independent/raw_id_collision/diamond.toml).
3. A single field could expand into enough options to bypass the render budget.
   Both renderers now charge conservative expanded-control work. The independent
   original 100×64-choice case produced 13,715 native nodes despite an 8,192 budget;
   it now rejects before artifact publication. [large_choice_forms](independent/large_choice_forms/diamond.toml).
4. Identity epochs fenced replies but could retain local private component state.
   The root-owned identity barrier now resets state and disposes old children;
   checked source restrictions keep old proposals from restoring it. The held-save
   logout/login/Bob-denial sequence passes without authored state cleanup.
5. Example context switches could leave a previous resource's child editor mounted.
   Editors now follow their repository/workspace context. Both direct card and
   existing picker flows were reviewed; browser context changes unmount them.

The independent original failure measurements are retained in
[failures-original.json](independent/failures-original.json). The proxy initially
lacked WebSocket upgrade forwarding, and its restricted SQL role initially lacked
event insert/sequence grants. Those were fixture corrections, not product defects.
The direct-render/export comparison initially compared a fragment with a whole
document; the corrected oracle compares the public fragment boundaries.

## Source coverage and limits

LP4 changed/affected source reviewed: `src/template.rs`; `project/lower.rs` and
`lower/{controls,forms,interactions,reach}.rs`; `project/render.rs` and
`render/controls.rs`; `project/{browser.rs,inputs.js,interactions.js,runtime.js}`;
artifact/wire version declarations; shared/server/browser callers and manifests
for Forge, live workspace, and components. Existing account/policy/action/transport
code is exercised through real consumers and prior checkpoint replays; it was not
re-audited in full for this checkpoint.

Build, strict all-target/all-feature Clippy, 10 existing tests, 38 doctests, rustfmt,
rustdoc, and diff whitespace checks pass. No public Rust API or MSRV change and no
unsafe code was introduced. The source/IR behavior change is intentionally versioned;
old trusted sources must be rebuilt. No upgrade compatibility or pipeline work is
included, and no commit/push was requested.

Accessibility evidence covers actual native controls, keyboard focus/activation,
the browser accessibility tree, labels and described-by relationships, and live
region roles/bounds. A screen-reader binary exists, but controlled speech/native
screen-reader observation was unavailable through this session's enabled tools.
No spoken-announcement or WCAG conformance certification is claimed. Raw legacy
forms still use authored labels. Whole-runtime taint proofs, prolonged leak tests,
cross-browser/IME/mobile qualification, durable action reconciliation, custom
validation callbacks/slots, and full production qualification remain outside LP4.
