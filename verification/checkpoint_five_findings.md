# Checkpoint-five findings and fix decisions

Original 0.4.0 executable SHA-256:
`c24890807ddfa962e37d44dfbd031d9166182ea40b5b4fcf56b45e6d45995b95`.
Source, manifests and that executable were frozen before any production changes.
Every experiment uses our interpreter and disposable local data; child address
space, CPU, core-dump and wall-time bounds protect the surrounding host.

## Managed record expansion

**Disposition:** confirmed reliability/resource-bound defect; no authority escape
or exact process-memory-isolation claim. **Category A:** introduce one private
managed-value budget and apply it across runtime cloning/materialization sites.

A legal recursive record program checks successfully but repeatedly doubles
owned record subtrees. In two independent fresh baseline CLI processes bounded
to 512 MiB address space, 5 CPU seconds and no core dump, it exits by SIGABRT (-6)
with an allocation failure rather than an ordinary Diamond error. Both raw
receipts and the source are preserved under `regressions/cp5_language/`; the
resource campaign additionally minimizes and expands related cases.

The existing step/call/text/list controls do not count deep record copies, scope
copies or all materialized database fields. The coordinated repair uses a private
value-depth/weighted-size and cumulative value-work budget. It preserves public
native signatures, the existing Error::Limit path, required transaction rollback,
and the Rust 1.96 baseline. Pattern destructuring consumes its evaluated value;
necessary copies use the shared budget. Database materialization must check bounds
before allocating/accumulating large field and result values.

This adds explicit prototype limits. It does not promise exact live-heap accounting,
account for every native/SQLite allocation or replace process isolation. Policy
temporaries and trusted native public Value cloning remain within the documented
native boundary. No suppression, allocator abort handler or weakened oracle is a fix.

## Invalid supported ID form fields

**Disposition:** confirmed input/Result classification defect. **Category B:**
retain the existing scalar decoder and its shared type validation, correct its
Id arm instead of treating invalid values as an unsupported type.

The real HTTP typed scalar form accepts an Id field, but supplying
`reference=invalid/name` returns 501 `Unsupported in this prototype`. It leaves
storage unchanged, so this is not an authorization bypass. The invalid-value guard
falls through to Unsupported, which also bypasses the application's normal
`match request.form` Err validation path. The correct expectation is an ordinary
typed input failure (HTTP 400), including through Option<Id>. The transport driver
retains these concrete requests and the validation-page case.

The repair constructs the supported Id value and lets existing Value::fits enforce
its shape, just like the other supported scalar fields. Valid Id inputs, data
policies and public APIs retain their behavior. This addresses the failure class,
not diagnostic wording reserved for checkpoint six.

## Evidence boundaries

Three further real-interface failures were confirmed before the final build:

- **Wall-clock session resurrection, Category A:** the existing process-local
  lifetime is governed by SystemTime across issuance, validity, CSRF and capacity
  cleanup. An isolated clock wrapper proves that an expired 80 ms session and CSRF
  become valid after a backward wall-clock jump; a forward jump expires a live
  session immediately. All deadline sites now share Instant, matching elapsed
  duration semantics without changing public signatures or host time. Raw
  pre-repair observations are in `regressions/cp5_session_clock_before.json`.
  They identify an intermediate pre-clock-repair library fingerprint; auth.rs
  was unchanged from the original baseline at that point. The actual final
  consumer verifies active forward/backward faults and required validity/CSRF.
- **Rejected-Origin response reset, Category B:** ten ordinary well-framed
  POSTs with a foreign Origin produced six 404 responses and four connection
  resets while exact storage remained unchanged. Host/Origin rejection ran before
  the bounded request body was consumed; closing with unread incoming bytes
  could erase the intended rejection response. Syntax/framing checks remain
  early, while Host/Origin checks now run after the complete bounded body and
  before any dispatch or database action, under the same five-second deadline.
  The transport replay retains normal HTTP-client and fragmented-body cases.
- **Near-limit body response reset, same Category B failure class:** the first
  final aggregate run found that a 65,537-byte body could likewise close with
  unread input before its 413 reached an ordinary client. Ten replays on that
  exact binary produced eight complete 413 responses and two resets; fragmented
  near-limit senders deterministically saw a broken pipe. The reader now drains
  at most 1 KiB beyond the 65,536-byte dispatch limit, under the existing
  deadline, before rejecting. This does not allocate an accepted request body or
  dispatch it. Arbitrarily large or incomplete bodies remain bounded and do not
  promise a complete response. The exact failed artifact and requests are saved
  in `regressions/cp5_transport/oversize_body_reset.json`.

These repairs preserve the existing Error variants, native signatures,
dependencies and MSRV; no lint annotations or unsafe Rust are added. Platform
monotonic-clock behavior remains an OS dependency; suspend-time semantics are not
qualified by the child-local wall-clock experiment.

The preliminary CLI probe accidentally ran a route-bearing program as if it were
a pure main function. That correctly launched the server and reached the probe's
wall timeout; it is a driver scenario error, not a product hang. An empty-list
generator initially attempted arithmetic on an unconstrained Never element;
the corrected case calls an explicitly typed Int helper. Neither erroneous
expectation is counted as a product failure or weakened to manufacture a pass.

The process-kill cancellation matrix already passed on the original executable:
20 controlled CLI/HTTP cases at the actual journal deletion boundary, including
lost client connections. The existing transaction guard is retained. The final
0.5.0 executable passes the same matrix and all 22 fingerprinted checkpoint
commands; [checkpoint_five_results.json](checkpoint_five_results.json) records
the complete command output, inputs and final artifact hashes.
