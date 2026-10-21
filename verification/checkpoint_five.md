# Checkpoint-five observable contract

This pass broadens the supported interpreted MVP's real-interface evidence. It
does not implement the excluded production, concurrency, interop, generic-enum,
or general-query profiles. Existing checkpoint-four guarantees remain required.

Expectations are defined before exploration:

- Legal bounded programs preserve arithmetic, short-circuit, immutable record,
  enum/Option/Result, matching, iteration and typed-output semantics. Structured
  malformed sources and exceeded supported limits return diagnostics/errors,
  without a panic, abort, uncontrolled stack failure or a hung process.
- Generated board actions use distinct accounts, values, IDs and action order.
  Only owners mutate; published records can be read publicly. Validation, CSRF,
  invalid/foreign IDs and expired/revoked sessions cannot change storage.
  Exact state and ordinary read/list results agree after each accepted mutation,
  rejected mutation, logout and restart. Boundaries include Unicode scalar lengths
  and literal markup/SQL-looking text.
- HTTP malformed syntax/encodings/duplicate framing/oversize input fails safely;
  partial requests and disconnected clients release the bounded single server.
  No second pipelined request or partial body causes an unintended mutation. A
  well-framed bounded body rejected for Host or Origin receives a complete error
  response without dispatch. Whole and fragmented bodies through 1 KiB over the
  65,536-byte limit receive a complete 413 after a bounded drain; larger or
  incomplete inputs release within the request deadline without promising a
  complete response. A healthy request still works after each rejection or
  connection failure.
- Native session lifetimes, capacity, invalid identities/tokens, CSRF binding,
  rotation, actual expiry, revocation and capacity reclamation are exercised.
  Process-local lifetimes use monotonic elapsed time: forward and backward
  wall-clock changes neither prematurely expire nor revive a session or CSRF.
  Account provisioning remains create-only with private files and bounded hashes.
- Evaluation resource errors after an explicit transaction's first write roll
  back that write; an ordinary subsequent operation remains usable. Hostile input
  exploration runs in disposable child processes with wall/CPU/address-space/core
  limits, keeping the host outside an exhaustion experiment.
- Actual process cancellation before the SQLite DELETE-journal commit point
  recovers the pre-transaction state; cancellation after it preserves the complete
  committed state. No partial explicit transaction is accepted. Fresh Diamond
  processes and independent SQLite reads check recovery and integrity. This is
  process-crash evidence, not a power-loss or physical-durability claim.
- Losing a client connection does not establish that a submitted write aborted.
  Its controlled schedule may commit once; it cannot produce partial state or an
  automatic replay. The client must inspect state rather than blindly retry.
- Existing quality gates, earlier real-interface replays, native consumers and a
  current actual-browser failure/retry/logout journey pass on the final artifact.

Record concrete original failures before repairs, preserve minimal replay inputs,
and expand the generator dimension that exposed each defect. Report actual seeds,
budgets, cases, artifact fingerprints, coverage gaps and cleanup. Checkpoint six's
syntax/diagnostic/install/performance polish remains separate.
