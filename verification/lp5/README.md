# LP5 qualification ledger

LP5 completed on 27 October 2026 against Diamond 0.20.0, artifact format 5,
wire 7, manifest schema 1, and API contract 1.

The final exercised debug binary SHA-256 is
`9ed5bfe28ebff433262582bc78121fe4a651f27b5afadad0739abc325701bc70`.
This identifies the tested artifact; it is not a release signature.

## What is implemented

Transactional actions use runtime-issued invocation identities and PostgreSQL
control state. The identity binds checked input, action/application version,
principal and cookie/API profile, current API restrictions, declared nominal
parent, and expiry. The bounded action mutation and an XChaCha20-Poly1305 sealed
receipt commit in one database transaction. Business rejection rolls the action
body back to a savepoint before committing its terminal receipt.

Receipts capture protected CRUD facts. Status and duplicate result retrieval
reauthorize the current account, credential restrictions, declared parent, saved
facts, and current rows. Server-side discovery supplies the browser with at most
16 unacknowledged references. The browser stores no invocation, form body, result,
or secret in localStorage/sessionStorage and owns accessible check/dismiss UI.
Identity change clears its private recovery state.

The supported profile is bounded PostgreSQL CRUD. Set/aggregate queries and
external effects reject for `replay = "transactional"`; there is no distributed
exactly-once claim. `replay = "never"` remains the single-attempt profile.

## Coverage map

- Declaration and compile enforcement: `src/project/actions.rs`,
  `actions/eligibility.rs`, `rpc.rs`, `api.rs`, resolver/lowering reachability, and
  Action type/wire checking.
- Native call paths: cookie and token profiles in `connected/profiles.rs`, route
  decoding in `connected.rs`, transaction/advisory-lock lifecycle in `storage.rs`,
  and policy replay in `relational/runtime.rs`.
- Durable control: `actions/durable.rs`, `actions/receipts.rs`, and
  `actions/footprint.rs`, including prepare/execute/status/ack/discovery, encryption,
  expiry/capacity, savepoint rollback, duplicate serialization, and reauthorization.
- Browser consumers: lowering metadata, `interactions.js`, `recovery.js`,
  `runtime.js`, and the generated recovery region in `artifact.rs`.
- Application/readback: the isolated ActionLab fixture plus transactional action
  journeys in Forge and Live Workspace. LP2–LP4 compiler and real backend suites
  were replayed against the current version boundary.

## Final evidence

| Evidence | Result | Scope |
| --- | --- | --- |
| [`actions.json`](actions.json) | PASS, 77 recorded observations | Seven focused baseline cases, 67 directed/generated/fault/capacity cases, and three independent SQL snapshots from the browser run |
| [`compiler.json`](compiler.json) | PASS, 23 cases | Manifest/action/effect rejection, duplicate-binding rejection, all action states, emitted contracts, versions, artifact recheck/incompatibility |
| [`independent_final.json`](independent_final.json) | PASS, 17 independently authored cases | Status fencing, protected scalar revocation, corruption/expiry, size/footprint boundaries, deletion/recreation/current policy |
| [`browser.json`](browser.json) | PASS, five Chrome observations | Double-click fence, withheld response + reload, empty browser storage, explicit reconciliation/ack, logout clearing |
| [`before.json`](before.json) | Expected pre-LP5 limitation | The prior 0.19 candidate mutated once but could neither return a duplicate result nor look one up |

The main deterministic seed is `20261027`. Generated values include Unicode,
quotes, whitespace, length boundaries, changed record order, two servers, and
randomized execute order. Independent SQL reads verify mutation counts and
revisions rather than trusting HTTP status or UI text.

The PostgreSQL proxy is byte-transparent. It observes native protocol query
packets and records only boundary flags. In the pre-COMMIT cut, the HTTP result was
`outcome_unknown`, PostgreSQL did not complete COMMIT, status established
`ActionAborted`, and the row did not change. In the post-COMMIT/pre-ack cut,
PostgreSQL completed the real COMMIT but its acknowledgement was withheld; HTTP
again reported unknown, status returned `ActionSucceeded`, and the row changed
once. No response was fabricated.

Capacity cases clear the unrelated identity-rate table immediately before the
probe and record both the native invocation count and a rate-attempt count of one.
The observed 429 therefore occurs at 16 pending, 128 per user, and 4,096 per app,
not because the fixture accidentally exhausted the general request limiter.

## Confirmed findings and fixes

1. The first candidate omitted the new terminal variants in one action decoder.
   Concurrent execution produced `ActionCorrupt` for one caller. The retained
   [`missing_action_decoder_before.json`](missing_action_decoder_before.json)
   records the failure. The shared decoder now accepts only the complete terminal
   set; the final concurrent and generated campaigns pass.
2. The independent oracle found that a footprint over 64 returned
   `ActionAborted` initially but saved incomplete accesses, so status/retry became
   `ActionCorrupt`. [`independent_failures_before.json`](independent_failures_before.json)
   records this against binary `807a5d…`. A confirmed resource-limit abort now
   discards the partial footprint before sealing its small terminal receipt. Final
   boundaries 61, 62, 63, 64, 65, and 70 all replay consistently; only 61/62 fit
   because the action itself contributes two protected facts.
3. The same oracle found that successfully deleting the declared parent made its
   result permanently 403. Recovery now requires the committed deletion footprint
   and rechecks current policy on sealed tombstone facts. A recreated same-ID row
   is also checked as a current row and retry never deletes it. Revocation still
   denies the result.
4. The compiler-negative campaign found that an unknown action parent resource
   could panic API scope derivation before eligibility ran. The exact retained
   failure is [`compiler_panic_before.json`](compiler_panic_before.json). All map
   accesses at that consumer are now checked and the malformed project returns a
   diagnostic without panicking.
5. The final source adversarial pass found that two action names could bind the
   same declared function while runtime selection was function-based. The compiler
   now requires one unique action binding per declared action function; explicit
   API operation aliases remain supported. The final negative corpus retains the
   duplicate-binding rejection.

## Replay

Prerequisites are Rust 1.96, Python 3, PostgreSQL client/server tools, and local
socket/process permission. The scripts create and remove isolated temporary
PostgreSQL clusters.

```sh
cargo build --all-features
python3 verification/lp5_compiler.py
python3 verification/lp5_actions.py
```

Use `python3 verification/lp5_actions.py --serve` for the disposable browser
fixture at `http://127.0.0.1:39171/`. The committed browser evidence used real
Chrome and a proxy-held real action response.

## Bounded conclusion

LP5's promised reliable-action profile passed the stated compiler, native API,
two-server HTTP/PostgreSQL, fault-boundary, policy, and one-browser schedules.
There are no unresolved confirmed findings in that scope. This is not evidence of
all-browser compatibility, a prolonged leak/resource campaign, arbitrary native
database tamper safety, external-effect exactly-once behavior, or production
deployment readiness. Cross-boundary whole-system hardening belongs to LP6;
cross-browser and prolonged assembled qualification belongs to LP8.
