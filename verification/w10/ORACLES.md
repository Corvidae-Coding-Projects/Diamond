# W10 release qualification oracles

These expectations were fixed before the W10 implementation and final replay.
They apply to one frozen Diamond 0.16.0 executable and library built with Rust
1.96.0. A passing result is bounded evidence for the documented Linux,
loopback-proxy, local Unix-socket PostgreSQL and shared-filesystem profile. It is
not proof of security outside that profile.

## Release and installation

1. `cargo install --locked --path .` succeeds in a fresh temporary root. The
   installed executable reports the exact package version, runs every single-file
   example, and scaffolds plus builds both reference applications without access
   to the source checkout at runtime.
2. The workspace and catalog walkthroughs run from freshly scaffolded sources on
   two application servers and two workers against an isolated PostgreSQL cluster.
   Their ordinary business behavior requires no Rust, JavaScript or SQL edits.
3. Build, format, strict Clippy, tests, warning-denied rustdoc, documentation-link
   audit and all existing compatibility/recovery replays pass without changing the
   frozen executable or verifier inputs.

## Accessibility and browser behavior

4. Typed templates accept the documented semantic grouping and ARIA attributes
   and continue to reject undeclared attributes. Both reference applications have
   one main landmark, a level-one heading, explicit labels for every editable
   control, visible keyboard focus, live status/error feedback and a loaded hashed
   stylesheet on both nodes.
5. In real Chrome, keyboard traversal reaches every enabled interactive control
   in document order, focus remains visible, native required-field validation
   prevents an empty issue submission, and successful asynchronous updates are
   exposed through status or live regions. Browser behavior is paired with a DOM
   receipt and independent server/database observations.
6. Existing W3 lifecycle, stale-reply, navigation, hydration, hostile-text and
   incompatible-bundle checks pass against the frozen final artifact.

## Dependencies and tooling

7. `cargo audit` uses a freshly updated official RustSec database and reports zero
   vulnerabilities in the locked graph. `cargo deny check` passes the committed
   license, advisory, source and ban policy. Git dependencies and wildcard direct
   dependencies are denied; crates.io is the only allowed registry.
8. The dependency receipt records the Cargo.lock hash, tool versions, advisory
   database revision/time, target graph size, allowed licenses and any duplicate
   versions. A clean result is a dated snapshot, not a permanent claim.

## Final evidence and capacity

9. Every AC-1 through AC-20 row maps to a current final-artifact receipt. The
   coverage validator inventories all Rust production modules, CLI commands and
   declared reference-app RPC/API/file/job/event operations and rejects unassigned
   entries.
10. W3, W5, W6, W7/W8 and the independent W9 hostile/failure campaigns identify
    the same final executable hash; native consumers identify the same final
    library hash. Allowed, denied, malformed, concurrent and recovery paths remain
    represented.
11. The sustained capacity replay runs at least 1,800 seconds with 100 clients and
    at least 100,000 records. At least 90% of HTTP responses succeed and successful
    p99 latency stays below three seconds. Planned node/worker failures recover,
    every accepted job reaches a terminal state, and the receipt classifies all
    nonsuccess responses.
12. The final report publishes exact artifact/input hashes, measured results,
    cleanup/process outcomes and untested boundaries. No suite result is described
    as proof of total security or production behavior outside the qualified profile.
