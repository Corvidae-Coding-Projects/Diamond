# Diamond authorized red-team contract

This is an authorized assessment of the local Diamond project. Targets are the
checked interpreter, CLI, native API, disposable SQLite files, and disposable
loopback servers created by the harness. No external host, account, service, or
user data is in scope. Experiments must remain bounded by child-process time,
memory, file-size, request-count, and temporary-directory limits.

## Security objectives

1. Source that fails checking cannot execute, open files, bind listeners, or
   partially initialize persistent state. Malformed source and adversarial size,
   nesting, Unicode, template, alias, call-graph, and declaration combinations
   fail without panic, abort, unbounded work, or misleading success.
2. Checked code cannot obtain database, identity, request, session, policy, or
   transaction authority it was not granted. Name shadowing, helper calls,
   recursion, Result recovery, transactions, route methods, typed forms, stale
   IDs, and malformed stored rows do not bypass the enforced boundary.
3. Typed HTML, redirects, route URLs, cookies, diagnostics, and CLI errors do not
   create markup, header, URL, log, or terminal-control injection from attacker
   input. Authentication rotates sessions, binds CSRF, rejects cookie confusion,
   and does not disclose whether a tested username exists through response shape.
4. The HTTP adapter rejects ambiguous framing, request targets, headers, hosts,
   origins, methods, versions, duplicated security fields, and incomplete input.
   Slow or oversized clients remain within the declared five-second and bounded
   allocation behavior. One connection cannot smuggle a second request or alter
   a response after a rejected request.
5. Account and application databases cannot be confused through ordinary path,
   hard-link, or symlink aliases at startup. Account creation preserves its
   private regular-file contract and refuses replacement. Incompatible schemas,
   triggers, malformed rows, busy files, interrupted operations, and hostile
   path types fail without widening authority or silently losing prior data.
6. Public/native APIs preserve token redaction, constant-time secret comparison,
   expiry, capacity, and secure-random failure behavior within the boundary a
   local harness can observe. Locked dependencies have no known current advisory
   in the authoritative RustSec database at qualification time.

## Attack campaign and oracles

- Drive the actual `diamond` executable, native consumer, TCP listener, browser
  protocol, filesystem, and SQLite format. Python models and direct SQLite reads
  are independent oracles; repository unit tests are not substitutes.
- Preserve each confirmed failure before editing under
  `verification/regressions/red_team_*`, including exact source/request bytes,
  artifact hash, exit/status/output, storage before/after, and the violated
  contract. Driver mistakes and environmental denials are recorded separately.
- Classify every confirmed Rust fix before editing: Category A changes the shared
  invariant, Category B corrects one isolated implementation, and Category C is
  reserved for a contract-required narrow annotation. No lint suppression,
  weakened assertion, test exclusion, or silent compatibility break is a fix.
- After a repair, replay the exact exploit first, its sibling cluster second, the
  full offensive campaign third, and `checkpoint_six.py --full` on the final
  fingerprinted state.

## Saturation rule

Discovery continues through all six objective groups and then repeats with fresh
source/workflow/transport seeds. After the last repair, completion requires two
consecutive complete fresh-seed passes with no new unique confirmed finding, all
saved exploits passing as regressions, and every applicable Rust and prior
checkpoint gate passing on one unchanged executable. This establishes bounded
negative evidence for the tested surfaces. It cannot prove absence of defects,
audit SQLite/native dependencies beyond available tooling, establish OS kernel
or physical-durability behavior, or turn the loopback development adapter into a
production server.
