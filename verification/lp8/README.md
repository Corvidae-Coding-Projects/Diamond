# LP8 assembled-language qualification ledger

**Result:** Complete for the bounded language profile on 27 October 2026.

The qualified executable is `target/debug/diamond` with SHA-256
`28a8f27b6db02a04301ffa458f03df38e6c37fad16aaa6b6929adcd448a51e30`.
Every machine-readable receipt below either names that exact hash or is a
source-only ledger that links to one that does. The complete `Cargo.toml`,
`Cargo.lock`, and `src/**` hash manifest is in [language.json](language.json).
This freezes evidence identity; it does not turn the uncommitted working tree
into a release artifact.

## Acceptance coverage

| Gate | Current-candidate evidence |
| --- | --- |
| AC-1 specification and profile matrix | `docs/language-specification.md`, `docs/language-profiles.md`, this source ledger, and the complete source manifest in `language.json` |
| AC-2 effects, grants, restrictions, authority | `lp2-spine.json`, `lp3-inputs.json`, `lp4-components.json`, `lp5-actions.json`, `secure-l1.json` through `secure-l7.json`, `auth-policy-races.json` |
| AC-3 typed routes and direct navigation | `lp3-inputs.json`, `browser-engines.json`, retained LP3 compiler report, real application journeys |
| AC-4 shared form decoding and validation | `lp3-inputs.json`, `browser-engines.json`, retained LP3 compiler report |
| AC-5 keyed components and typed output | `lp4-components.json`, retained LP4 compiler report, `browser-engines.json` |
| AC-6 typed failures and uncertainty | `lp5-actions.json`, retained LP5 compiler report, `running-cancel.json`, application journeys |
| AC-7 durable action identity | `lp5-actions.json`, its PostgreSQL before-commit/ack-loss campaign, and `prolonged.json` |
| AC-8 fresh authority and revocation | `auth-policy-races.json`, `files.json`, `secure-l4.json` through `secure-l7.json` |
| AC-9 budgets, cleanup, recovery | `lp6-limits.json`, `prolonged.json`, `health-starvation.json`, `websocket-malformed.json`, `performance.json` |
| AC-10 tools and diagnostics | `../lp7/tools.json`, `../lp7/browser.json`, `../lp7/findings-original.json` |
| AC-11 assembled independent qualification | `language.json`, `semantics-native.json`, `native-runtime.json`, LP1-LP7 replays, and Rust quality gates below |
| AC-12 real applications and current guides | `browser-applications.json`, Forge and live-workspace fixture receipts, README and current language guides |

## Source-to-guarantee map

| ID | Source and callers covered | Guarantee and recovery evidence |
| --- | --- | --- |
| C01 | `lexer`, `parser`, `syntax`, `checker`; CLI, project compiler, LSP | Valid/hostile generated programs, compiler replays, diagnostic recovery |
| C02 | `resolve`, `project`, `artifact`; build/load/native callers | Two identical builds, rechecked artifacts, external Rust consumer |
| C03 | `eval`, `standard`, `value`; CLI and server evaluation | 185-case independent semantics oracle and 96 generated executions |
| C04 | browser lowering/runtime equivalents of shared operations | Chromium, Firefox, and WebKit semantic and recovery comparisons |
| C05 | templates and render tree | LP1 contracts, LP4 component fixture, keyboard and narrow-layout reflow |
| C06 | `authority`, `security`, effect checker | secure L1-L7, compiler rejection, authority-race replay |
| C07 | `action`, relational request transaction | action fault campaign distinguishes rollback, commit, ack loss, uncertainty |
| C08 | durable receipts, tickets, recovery JS | duplicate/retry/cancel campaigns and valid post-failure recovery |
| C09 | policy and relational restrictions | authorization races, file ownership, application PostgreSQL readback |
| C10 | wire, RPC, API and HTTP boundaries | strict decoding campaigns, malformed WebSockets, LP1-LP7 replays |
| C11 | forms, scalar input, controls | raw-input corpus, browser validation/error relationships, server recheck |
| C12 | lower/render/runtime/interactions | keyed component, identity, callback, focus, keyboard, and reflow evidence |
| C13 | connected runtime and identity epochs | logout/login application replays, races, live reconnect, delayed work |
| C14 | schema routes and typed navigation | deep-link/direct-request corpus and application route journeys |
| C15 | browser emitter and maps | LP7 handler/component mappings, public labels, private-source absence |
| C16 | deterministic project artifact writer | complete two-build artifact hashes in `language.json` |
| C17 | execution/request/value/render/lifetime budgets | limits, prolonged, files, starvation, malformed transport, performance |
| C18 | scenario and evidence ledgers | frozen seeds, drivers, source hashes, original findings and cleanup receipts |

The principal runtime callers covered are CLI check/run/build/serve/fmt/LSP,
the public Rust library, generated browser clients, HTTP/RPC/WebSocket clients,
PostgreSQL request transactions, the file adapter, and the durable worker.
Recovery coverage includes malformed input followed by valid input, failed
component update followed by success, transaction acknowledgement loss,
cancellation, dependency failure, identity replacement, reconnect, and clean
fixture shutdown.

## Behavioral receipts

- `language.json`: deterministic build; seed 20261027; 96 valid generated
  executions, 64 hostile programs, and an external public-API consumer.
- `semantics-native.json`: 185 independent Decimal, Unicode, sequence, time, and
  boundary expectations.
- `lp1-replay.json`, `lp2-spine.json`, `lp3-inputs.json`,
  `lp4-components.json`, `lp5-actions.json`, and `lp6-limits.json`: exact-candidate
  replay of LP1-LP6. The corresponding LP2-LP5 compiler reports are under their
  checkpoint directories; LP7 is `../lp7/tools.json`.
- `secure-l1.json` through `secure-l7.json`: 92 retained security-language
  observations. `files.json` adds 65 file-boundary observations.
- `auth-policy-races.json`, `health-starvation.json`, `running-cancel.json`,
  `websocket-malformed.json`, and `prolonged.json`: concurrency, cancellation,
  transport, lifetime, cleanup, and valid-recovery sequences.
- `browser-engines.json`: actual Chromium 156.0.8078.47, Firefox 142.0.1,
  and WebKit 26.0; ten semantic/accessibility/layout checks per engine.
- `browser-applications.json`: actual Chrome Forge and non-Git journeys, with
  independent PostgreSQL readback, a separate worker, logout/login, and cleanup.
- `accessibility-static.json` and `accessibility.json`: generated semantics,
  relationships, keyboard behavior, and the explicitly blocked AT combination.
- `startup-installation.json`: 20 clean-install, CLI identity/help, scaffold,
  native-library, startup, request, and cleanup observations with no failures.
- `performance.json`: 40-iteration hello and message-board samples plus an
  82,892-byte/4,000-declaration source; observed p95 checks were 2.060 ms and
  3.487 ms, with 22,780 KiB maximum RSS in the message-board process sample.

All temporary application servers and PostgreSQL clusters report expected exit
and no cleanup errors. The final application worker was separately started,
completed one durable job in one attempt, and was stopped before fixture cleanup.

## Rust quality record

The final source passed:

```sh
cargo build --all-features
cargo fmt --all --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-features
RUSTDOCFLAGS="-D warnings" cargo doc --all-features --no-deps
cargo check --all-features
```

Rust 1.96.0 compiled the all-feature candidate. Unit tests: 11 passed. Doctests:
39 passed. The crate retains `unsafe_code = "forbid"`; Miri is not applicable
because no unsafe code exists. The public `format_source` API is additive within
the pre-1.0 crate, and this checkpoint does not raise the Rust-version contract.

## Replay

The ordinary Python drivers accept `--binary target/debug/diamond` and the
report paths recorded above. The cross-engine command is:

```sh
PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=debian13-x64 \
PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 \
DIAMOND_LP8_WEBKIT_BUNDLE="$HOME/.cache/ms-playwright/webkit-2215/minibrowser-wpe" \
DIAMOND_LP8_WEBKIT_DEPS="$PWD/.diamond/lp8-webkit-deps/usr/lib/x86_64-linux-gnu" \
node verification/lp8_browser_engines.js
```

WebKit uses locally extracted official Debian `libicu76` and `libwoff1`
compatibility packages; their hashes are recorded in `browser-engines.json`.
They are ignored workspace fixtures and do not alter the host package set.

## Findings and bounded conclusion

The product defects and two faulty test expectations discovered during LP7/LP8
are retained in [findings-original.json](findings-original.json); every confirmed
in-scope product defect is fixed and replayed. The campaign did not find an open
in-scope defect on the qualified candidate.

This is bounded evidence, not proof of total correctness, security, WCAG
conformance, or deployment readiness. Orca is installed but cannot expose a
usable speech/braille application surface in this automation context, so no
screen-reader pass is claimed. External Google/GitHub OAuth, real SMTP, public
Git remotes, production proxies, and deployment infrastructure are outside LP8.
The deterministic generated corpus is broad but not exhaustive fuzzing.
