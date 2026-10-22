# Diamond 0.16.0 release qualification

Diamond 0.16.0 is the W10 release candidate for the defined general-web-application
scope. Qualification covers Linux on x86-64, Rust 1.96.0, an HTTPS reverse proxy,
two loopback application servers, two workers, one local Unix-socket PostgreSQL
cluster and one operator-provided shared filesystem. It does not turn those local
adapters into a managed public service.

## What is release-qualified

- Multi-file Diamond applications build deterministic versioned server, browser,
  contract, stylesheet, source-map and asset artifacts.
- Reactive Diamond components cover server rendering, hydration, local/derived
  state, keyed children, forms, navigation, RPC and live subscriptions.
- The connected profile supplies shared accounts and sessions, scoped roles,
  relational PostgreSQL operations, APIs, private files, destination-scoped HTTP,
  durable jobs and typed events across multiple processes.
- Deployment validation, health/readiness, draining, audit/metrics, encrypted
  quiesced backup and isolated restore are exercised through real interfaces.
- The workspace and catalog applications are installable scaffolds whose business
  handlers and browser components are Diamond source.

The [W10 coverage ledger](../verification/w10/COVERAGE.md) maps every acceptance
criterion, production module group, command and reference-application operation to
retained evidence. The [final report](../verification/web_ten_results.md) records
the exact executable/library/input hashes, current dependency review, browser
observations and measured capacity.

## Dependency and compatibility policy

`Cargo.lock` is part of the release. `cargo audit` is run against a freshly fetched
official RustSec database, and the committed `deny.toml` restricts registries,
git sources, wildcard versions and licenses for the qualified Linux target.
Duplicate transitive versions are reported for review rather than treated as a
vulnerability by themselves. These results are dated snapshots; repeat both tools
when the lockfile or release date changes.

The current 0.16.0 artifact and schema are qualified together. There are no users
requiring old/new application coexistence, so mixed executable/schema deployment
and automatic upgrade compatibility are outside this release. Stop servers and
workers for migrations, backfills, backup/restore and artifact rotation as the
deployment guide specifies. The single-file SQLite profile remains supported and
has a separate bounded storage contract.

## Accessibility profile

Generated pages include a language, viewport, document title, hashed stylesheet,
one application mount and an alert region. Typed templates provide semantic
landmarks, headings, sections, forms, fieldsets, legends, labels, buttons, links,
status/alert roles, live regions, ARIA relationships, autocomplete hints and native
required-field validation. Both reference stylesheets provide a three-pixel visible
focus indicator and small-screen layout rules.

W10 inspects labels, ID relationships, landmarks, heading structure, focus order,
native validation, status feedback and stylesheet loading in real Chrome. This is
not a WCAG conformance certification: screen-reader combinations, zoom/reflow
beyond the recorded viewport, forced colors, reduced motion, localization and
other browser engines remain untested.

## Measured boundary

The final sustained campaign publishes its own numbers rather than treating a
benchmark as a universal capacity claim. Its fixed floor is 100 clients, 100,000
records and 30 minutes, with at least 90 percent successful responses and
successful p99 below three seconds while one server and one worker are restarted.
Operators must measure their own hardware, proxy, database, storage and workload.

Public ingress, remote PostgreSQL TLS and network partitions, replicated object
storage, fsync/power-loss behavior, SMTP, malware scanning, other browser engines,
kernel compromise, hostile database/storage administrators and exhaustive protocol
fuzzing remain outside the qualified evidence. External delivery stays at least
once. A shared-filesystem marker is an operator trust binding, not cryptographic
storage attestation.
