# W1 completion evidence — 21 October 2026

W1 is complete in Diamond 0.7.0. Its pre-edit [acceptance contract](web_one.md)
and [project guide](../docs/projects.md) define this increment. W2–W10 and the
overall general-web release acceptance criteria remain unfinished.

## Result and scope

The three-module Foundations example checks/builds deterministically, emits
typed scalar browser IR and direct JavaScript, updates state in a real browser,
and executes explicitly granted, read-only PostgreSQL operations. Server/browser
imports, exported names, application/module effects, and runtime grants are
separate checked boundaries. The new loopback asset server uses Axum/Hyper and
Tokio. The existing single-file interpreter and native APIs remain available.

The final debug executable SHA-256 is
`399a7b34c016bbed78c7870b6ca7f6ff1ff0300b48f3f2790e1f2c4281ff3ff0`.
Receipts bind their observations to the executable actually exercised, including
the independent verifier's earlier frozen builds where stated. No test uses a
production database or remote attack target.

## Real-interface observations

| Evidence | Observation |
| --- | --- |
| [CLI/HTTP/PostgreSQL campaign](web_one_results.json) | Deterministic four-file builds; rechecked loading; 58 HTTP requests, including 48 concurrent requests while a separate client holds incomplete headers; server source/contract stay unavailable over HTTP. |
| Same campaign, isolated PostgreSQL 18.6 | 11 CLI invocations; version compared with independent `psql`; six bound text/Unicode/injection payloads; denied missing grants; rejected/redacted TCP configuration; zero public tables. Pausing the private PostgreSQL process returned a connection deadline in 5.003 seconds; resuming it allowed recovery. |
| [Actual Chrome interaction](web_one_browser_results.json) | Counter 2→5→8→7; generated 33-state semantic fixture compared to independent expected values and 33 native interpreter runs; large integers, negative division, Unicode length/ordering, short circuit, reserved JavaScript names and literal hostile text. Failed overflow preserved count 2; the next valid click showed 5 and cleared the alert. No browser console errors/warnings observed. |
| [Module/artifact exploration](web_one_modules_results.json) | 106 CLI invocations plus five independent artifact/reachability checks and the seven-case saved-failure replay. Both sides of source, manifest, aggregate byte, module-count/name-length and declaration-count limits; exports, targets, effects, ambiguity, invalid UTF-8, source/artifact special files, tampering and version rejection. |
| [Fresh-seed red-team compatibility](web_one_red_team.json) | Seed `0xD1A07129`; language, compiler enforcement, transport, policy, forms, account/session abuse and dependency-advisory replay passed against this executable. Includes 8,194 anonymous requests and subsequent valid login/session checks. |
| [Earlier full compatibility pass up to the help assertion](web_one_compatibility_initial.json) | Formatting, locked build, strict all-target/all-feature Clippy, 15 doctests, warning-denied rustdoc, native embedding, complete checkpoint-five replay, and diagnostic exploration passed. The aggregate receipt records FAIL because the installed-help assertion still expected the old checkpoint-six label. |
| [Targeted finishing pass](web_one_compatibility_results.json) | Rechecks diagnostics, clean release installation/startup and measured fresh-process performance after updating only the obsolete help-label/version expectation. Does not repeat the already-passing checkpoint-five campaign. Child receipts are retained in [web_one_compatibility](web_one_compatibility). |

The project embedding consumer also ran against the actual private PostgreSQL
server. Replays create and clean up their own temporary stores/listeners. The
browser screenshot below is an actual capture after clicking Add three.

![Foundation component showing Count 5 after a real click](web_one_browser.jpg)

## Findings repaired

The independent verifier found an internal module/function qualification
collision and a visibility bypass through guessed compiler-generated names.
Both were reproduced before repair. Qualification now includes the module name's
length, and source references/bindings cannot enter the reserved compiler
namespace. Seven [retained fixtures](regressions/w1_resolution/README.md) verify
both mangling formats and legitimate underscore-containing names. Module source
mapping also now wraps resolver failures with the original path/line.

## Inspection and release checks

Source inspection covered the new project graph/resolver, typed lowering,
JavaScript emission, artifact writer/rechecker, HTTP adapter and host connection
driver, and their callers in parser/template/checker/evaluator/CLI/public API.
Compatibility checking exercised the changed host-effect handling against the
existing language, SQLite policy/transaction and route paths. The separate
independent verifier concentrated on module/visibility/target and artifact/file
boundaries. This is a coverage record for those boundaries, not a whole-project
security proof.

Rust 1.96 remains the declared MSRV; the package version rises from 0.6.0 to
0.7.0 for the new public project API and `Error::Source` variant. The existing
native examples still build/run. New public methods have doctest examples;
15 doctests pass in total. No authored unsafe construct or lint suppression was
added, so an authored-unsafe Miri pass is inapplicable. Clippy and rustdoc deny
warnings. Locked dependency metadata fits the MSRV. Cargo audit found no listed
vulnerabilities among 147 dependencies against the locally available 1,348-entry
advisory database; that is a snapshot, not a fresh-feed or total-security claim.

## Explicit remaining limits

W1 implements scalar state/functions and one component with full-tree updates.
It does not supply project CRUD, shared identities, generated RPC, general
records/enums in browser code, CSS, keyed updates, hydration, lifecycle, or
weighted aggregate browser memory accounting. Those remain subsequent work.
PostgreSQL grants are local Unix-socket-only, read-only, and synchronous at the
embedding boundary; production TLS, pools, migrations and distributed operation
are unimplemented. The HTTP skeleton has bounded active handlers, not a completed
production connection/body/deadline/quota contract. Artifact rechecking/hashes
do not establish publisher authenticity or protect against arbitrary native
host/filesystem tampering. No cross-platform, multi-node, deployment, physical
durability or broad production-readiness claim follows from this checkpoint.

## Replay

```sh
cargo build --locked
python3 verification/web_one.py
python3 verification/web_one_modules.py
python3 verification/red_team.py --seed 0xD1A07129
python3 verification/checkpoint_six.py --output-dir verification/web_one_compatibility --report verification/web_one_compatibility_results.json
```

The first command needing sockets requires the environment to permit loopback
and Unix sockets. The PostgreSQL campaign uses installed `initdb`, `pg_ctl` and
`psql`; Chrome interaction is recorded separately and must be operated through
the browser interface. `--browser-fixture NEW_DIRECTORY` generates the same
seeded semantic project for repeating that journey.
