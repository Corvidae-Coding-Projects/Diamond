# W10 release qualification

Diamond 0.16.0 completes the W1-W10 general-web-application plan within the
documented Linux profile. This record binds the final evidence to these frozen
artifacts:

- executable SHA-256: `2e2c3271fcb693eb6987ee1682f7616becd59eed58bf8141ea1b3d2b947a6d8a`
- library SHA-256: `d6a40dcfdc295292d976922aa6cf5cfe2d4db9659a9425d6fc95377cef330a68`
- package version: `0.16.0`
- toolchain: Rust and Cargo 1.96.0 on x86-64 Linux

The fixed expectations are in [`w10/ORACLES.md`](w10/ORACLES.md), the
machine-checked source and evidence map is in
[`w10/COVERAGE.md`](w10/COVERAGE.md), and exact replay commands are in
[`w10/README.md`](w10/README.md).

## Finishing work

W10 completes the release surface rather than adding another authority-bearing
adapter. It adds:

- semantic `main`, `header`, `section`, `fieldset` and `legend` structure in both
  reference applications, explicit labels and ARIA relationships, native required
  validation, live status regions, keyboard focus indicators and responsive CSS;
- checked support for the documented ARIA attributes while retaining rejection of
  undeclared attributes;
- clean installation and scaffold verification that byte-compares generated
  workspace/catalog sources with the qualified examples;
- full reference-application README journeys and a clean two-server/two-worker
  deployment walkthrough;
- a locked dependency, license and source policy in `deny.toml`;
- a final production-module, CLI, declared-operation and AC-1-through-AC-20 ledger;
- isolated output directories for retained replays and explicit cross-node browser
  fixture commands.

The final checkpoint seal runs format, strict Clippy, build, tests, warning-denied
rustdoc, the native embedding example, checkpoint-five full compatibility and
red-team replays, diagnostics, clean startup/installation, and performance checks.
Its machine-readable receipt is
[`w10/checkpoint_six_results.json`](w10/checkpoint_six_results.json). The final
coverage receipt is [`w10/coverage_results.json`](w10/coverage_results.json).

## Final-artifact replays

All figures below come from retained machine-readable receipts against the frozen
artifact hashes above.

| Area | Result | Observed coverage |
| --- | --- | --- |
| W3 language/frontend | PASS | 185 shared-semantics cases, 30 checker cases, 121 wire cases, 3 retained regressions, 5 native API checks and a 184-case native corpus |
| W5 identities/APIs | PASS | 294 observations and 1,956 HTTP requests across checker, account, credential, API and concurrency paths |
| W6 files/integrations | PASS | 143 observations, 180 HTTP requests and 22 controlled shutdowns |
| W7/W8 jobs/live/deployment | PASS | 77 observations, 651 HTTP requests and 34 controlled shutdowns, including real TLS/WSS through checksum-verified Caddy 2.11.7 |
| W9 hostile/failure replay | PASS | 12 campaigns, 98 observations and 26 controlled shutdowns |

The W9 replay includes malformed transport, health-capacity isolation, storage-root
replacement, artifact disclosure boundaries, TLS/edge behavior, malformed
WebSockets, authorization races, job cancellation/fencing/deduplication, stale
resource holders, explicit recovery cancellation and storage loss. These are
bounded campaigns, not an exhaustive security result.

The source ledger assigns all 68 Rust production modules exactly once, recognizes
all 24 public command/help/version entry points, derives all 34 declared RPC, JSON
API, file, job and event operations from the two manifests, and maps every
acceptance criterion to passing current evidence.

## Browser and accessibility evidence

Real Chrome 155.0.8059.39 exercised freshly copied applications against isolated
PostgreSQL, two application servers and two workers. Repository/static checks were
kept separate from browser observations.

The workspace journey verified eight named semantic regions, labels for all 22
editable controls, keyboard order and a visible focus outline. Native required
validation blocked and focused an empty issue title. The browser signed in, read
and updated records, posted a comment, rendered hostile script/image-shaped text
as text, observed a completed job and live update, survived a node termination and
restart with state retained, and cleared authenticated UI state after global
logout. A separate round-robin run read on node B, wrote and commented through B,
and observed both changes through A. Database state was read independently.

The catalog journey verified three named regions, labels for all seven editable
controls, keyboard order, focus visibility, anonymous mutation denial, private
author read/edit, literal hostile content, publish/live/public browsing, sign-out
state clearing and a completed notification job. Its receiver independently saw
one deduplicated side effect. A separate round-robin run read and edited through B,
observed the edit and job status through A, and browsed the published page through
B.

The static accessibility receipt also confirms unique IDs, resolved ARIA
relationships, one main and one level-one heading per app, live feedback, one
hashed stylesheet, focus rules and small-screen rules. It records 22 controls and
eight status regions for workspace, and seven controls and five status regions for
catalog. The compiler accepted the declared semantic primitives and rejected an
undeclared `aria-bogus` attribute. This is not a WCAG certification; the untested
assistive-technology and browser combinations are listed below.

## Dependency review

The dated review used `cargo-audit 0.22.2` and `cargo-deny 0.20.2` against official
RustSec commit `640bf5a9f06e66d8721e8772766531fe77c8630d`, dated 22 October 2026. It inspected
270 locked packages and found zero RustSec vulnerabilities. The committed policy
passed advisory, yanked-package, license, source and ban checks, permits only the
crates.io registry, denies unknown Git/registry sources and denies direct wildcard
dependencies. Fifteen duplicate-version groups remain recorded for maintenance;
duplicates are not treated as vulnerabilities by themselves. Repeat this review
after a lockfile or advisory-database change.

## Measured capacity

The sustained local campaign ran for 1,805.096 seconds on an Intel Core Ultra 9
275HX host with 24 logical CPUs and about 31.5 GiB RAM. It used an isolated local
PostgreSQL cluster, an operator-owned shared filesystem, two real application
servers, two real workers, 100 client threads and 100,004 seeded records.

| Measurement | Observed |
| --- | ---: |
| Requests | 90,250 |
| Successful responses | 84,557 (93.692%) |
| Offered rate | 49.997 requests/second |
| Successful p50 | 3.596 ms |
| Successful p95 | 14.570 ms |
| Successful p99 | 16.780 ms |
| Expected connection refusals during node outage | 50 |
| Accepted jobs | 15 |
| Completed/incomplete jobs | 15 / 0 |
| Long-lived WebSocket events | 15 |
| 8 MiB upload begin/put/finish/delete | all HTTP 200 in 12.827 s |
| Node recovery | 3.234 s |
| Worker recovery | 3.004 s |
| Peak node RSS | 53,060 KiB / 34,804 KiB |
| Peak worker RSS | 25,896 KiB / 26,204 KiB |

The database independently reported an index scan for the qualified query. Both
nodes finished ready, every scheduled process exit matched its expected outcome,
no panic or forced cleanup was observed, and the temporary root was removed. These
numbers describe this local synthetic workload and are not a general throughput
promise.

## Qualification boundary

The qualified profile uses Linux x86-64, Rust 1.96, one local Unix-socket
PostgreSQL cluster, loopback Diamond servers behind the tested TLS proxy, local
operator-owned shared storage, synthetic identities/data, and the current artifact
and schema together. The current profile deliberately does not promise mixed
version or old-schema compatibility because there are no deployed users requiring
it.

Public Internet deployment, remote PostgreSQL TLS and network partitions,
replicated/object-store durability, power-loss/fsync behavior, SMTP delivery,
malware scanning, browsers other than the recorded Chrome build, full screen-reader
combinations, hostile kernel/database/storage administrators, and exhaustive
protocol fuzzing remain untested. External delivery remains at least once.
Passing W10 does not prove total security or production behavior outside this
boundary.
