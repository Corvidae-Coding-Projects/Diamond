# LP6: cross-boundary security and runtime hardening

**Status:** Complete, 27 October 2026. See
`verification/lp6/README.md` for the final candidate hashes, retained findings,
real-interface evidence, and bounded coverage gaps.

## Purpose

Qualify the enlarged Diamond 0.20 language as one connected system. LP6 does
not add an application feature. It attacks the compiler and every authoritative
consumer introduced by LP2--LP5, repairs shared enforcement, and establishes
bounded evidence for AC-2, AC-8, and AC-9.

## Security invariant

If an application author declares a type, effect, authority, resource scope, or
budget, Diamond carries and rechecks it at every later boundary. Browser state,
opaque identifiers, durable records, and earlier authorization never become
authority by themselves.

## Covered boundaries

1. Parser, resolver, checker, project recheck, reachability, and lowerers.
2. Server and browser value codecs, route/form codecs, renderers, generated
   controls, component events/outputs, and identity-epoch cancellation.
3. Cookie and attenuated API profiles, RPC/action selection, submission proofs,
   invocation binding, sealed receipts, and relational policy replay.
4. Blob/stream/job handle consumers and delayed callbacks exercised by the
   existing supported adapters.
5. Managed-value depth/weight/work, event fuel/deadline, render nodes/bytes,
   form and request bytes, action receipt/footprint/capacity, request/socket/pool
   admission, and cleanup after overload or cancellation.

## Declared campaign bounds

- Generated semantic corpus: deterministic seed `20261027`, with independently
  computed scalar/composite results and matching Rust/browser categories.
- Compiler hostile corpus: all LP3--LP5 sink families crossed with direct and
  recursively wrapped restricted types; nominal swaps, shadowing, helper effects,
  hidden returns, forged wire shapes, and malformed project declarations.
- Native connected campaign: isolated PostgreSQL cluster, two server processes,
  cookie and API callers, synchronized policy/identity changes, action receipt
  tampering, dependency interruption, and valid follow-up probes.
- Browser lifecycle campaign: real browser create/update/reorder/navigate,
  cancellation, logout/login replacement, unmount, reconnect, and reload with an
  already authenticated session.
- Prolonged campaign: deterministic bounded schedules with sampled Diamond RSS,
  descriptor count, PostgreSQL connection count, browser-owned sockets, and final
  state. Native child processes are sampled separately and excluded from Diamond
  process measurements.

Exact cases and expected observations are frozen in
`verification/lp6/SCENARIOS.md` before execution.

## Repair rule

Every confirmed relevant failure is retained before editing, fixed at the shared
boundary responsible for the invariant, and replayed with adjacent valid cases.
Per-application exceptions, diagnostic suppression, and test-only behavior do
not satisfy LP6.

## Exit gate

LP6 is complete only when the declared AC-2, AC-8, and AC-9 observations pass on
one final source/binary hash, earlier LP2--LP5 affected suites replay, all
confirmed findings have retained before/final evidence, and unexplored or
environment-limited boundaries are named. The conclusion is bounded; it is not
an exhaustive security or allocator-isolation claim.
