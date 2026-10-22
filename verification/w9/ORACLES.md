# W9 adversarial and distributed-failure oracle

Written before the W9 campaign runs. Expectations come from
`.design/general-web-apps.md` REQ-1 through REQ-20 and AC-1 through AC-20,
the documented W1-W8 contracts, and the retained real-interface failure
replays. A process staying alive, an HTTP success status, or a Diamond error
message is not sufficient evidence: the campaign reads back authoritative
PostgreSQL rows, object bytes, receiver effects, browser state, event frames,
process state, and resource counts as applicable.

| Case | Hostile action or fault | Precommitted observable invariant |
| --- | --- | --- |
| H1 | Send malformed, ambiguous, oversized, truncated, pipelined, and slow HTTP requests through direct and proxied entry points | Each request is rejected or handled according to one unambiguous boundary; no request splitting, protected action, panic, permit leak, or unrelated-request starvation occurs |
| H2 | Vary methods, paths, percent encodings, Host, Origin, forwarding headers, cookies, authorization headers, JSON shapes, Unicode, and contract versions | Dispatch remains exact; malformed or duplicate authority inputs fail closed; no principal, scope, secret, source, filesystem path, or internal error is disclosed |
| H3 | Exercise raw RPC, JSON API, form, asset, download, upload, WebSocket, health, and worker-facing surfaces with anonymous, disabled, revoked, wrong-workspace, scoped, and owner identities | Every surface reconstructs current identity and scope server-side; equivalent protected objects receive equivalent policy decisions; public surfaces disclose only their declared projection |
| P1 | Race membership removal, account disablement, logout, password change, API-token revocation/expiry, and parent moves against reads, writes, downloads, queued work, and live delivery on two servers | Transactions already authorized under the documented database snapshot may finish; every later operation observes committed revocation, workers recheck current authority, and no event delivery begins from authorization older than five seconds |
| D1 | Seed at least 100,000 records across distinct workspaces; page, count, join, sort, and look up boundary IDs while inspecting plans | Results equal an independent reference projection, remain deterministically ordered and scoped, use bounded/indexed plans for declared access paths, and never decode an unbounded relation into one request |
| D2 | Exhaust pools, stop/restart PostgreSQL, inject acknowledgment loss or connection interruption around transactions, and corrupt supported stored shapes in owned fixtures | Access fails closed without fallback; confirmed abort is distinguishable from unknown outcome; mutations with unknown outcome are not blindly replayed; service recovers after the dependency does |
| F1 | Attack upload names, MIME declarations, lengths, truncation, expiry, handle forgery, concurrent completion, unauthorized download, traversal/symlink-shaped values, and abandoned stages across two servers | Handles remain opaque and principal-bound; pending data stays unreadable; published bytes and metadata agree; at most one final object is referenced; paths stay under the canonical root; bounded cleanup removes only eligible stages |
| O1 | Exercise allowed and denied destinations with redirect chains, address changes, malformed/oversized/compressed/truncated replies, slow headers/body, disconnects, hostile header values, and secret-bearing calls | Policy applies to every resolution/connect/redirect; limits and deadlines release resources; protected values never enter errors/logs/general serialization; uncertain external effects are not described as safely replayable |
| J1 | Kill or pause workers before dispatch, after external effect, before acknowledgment, during database loss, and across lease expiry; run a live stale-holder completion race | Only the current fencing token can commit completion; recovery is at least once with a stable destination key; obsolete completion cannot overwrite the current attempt; retries/dead letters/cancellation are bounded and visible |
| J2 | Exercise explicit recover `resume` and `cancel`, paused queues, old handler versions, duplicate keys, conflicting payloads, changed scopes, disabled identities, and time shifts | Recovery performs only the named policy; cancellation fences pending/running jobs; incompatible versions remain unclaimed; dedup conflicts do not mutate the original job; database time governs durable expiry/lease decisions |
| E1 | Use raw cross-node WebSockets with malformed upgrades, frames, cursors, origins, inbound data, fragmentation, backpressure, revocation, reconnect, and more than 10,000 retained publications | Handshake and frames remain bounded; only authorized typed data is delivered in durable order; automatic retention produces explicit resync for unavailable cursors; slow or stale clients are dropped without starving ordinary HTTP |
| E2 | Keep a real authenticated socket for the documented 30-minute lifetime while mixed traffic and revocation checks run | The socket remains usable within its lifetime, expires at the bound, and releases its dedicated permit; session/policy expiry can close it sooner |
| R1 | Interrupt servers, workers, upload cleanup, backup, restore, and recovery commands at selected points; remove/replace owned object roots; tamper with encrypted artifacts and manifests | Readiness reflects dependencies; partial work is explicit and recoverable; restore never writes before validation and never targets nonempty state; only owned processes/data are cleaned up |
| C1 | Sustain at least 100 concurrent ordinary clients for at least 30 minutes over a 100,000-record database while mixing authenticated reads/writes, API calls, assets, files, events, slow streams, and worker interruption | Record p50/p95/p99 latency, throughput, errors, RSS, database/queue/socket counts, and recovery; explicit quotas bound load, unrelated traffic stays available, and every error is classified rather than discarded |
| B1 | Drive both reference applications in a real browser, then reload, navigate history, sign out/in, revoke access, replace a server, and leave stale tabs/subscriptions active | Compiled Diamond UI stays consistent with authoritative state; stale replies and subscriptions cannot overwrite current state; hostile record text remains text; private display/form state clears on identity loss |
| A1 | Inspect built browser/server artifacts, maps, logs, backups, manifests, and diagnostics using planted canaries | No secret, password, token, server-only source/body, native authority, private row, or unredacted dependency error appears outside its declared boundary |

## Exploration budget and evidence rules

- Systematic generators vary legal values first, then targeted invalid values,
  with recorded seeds and concrete minimized cases for every product failure.
- Every confirmed product failure is retained with its pre-fix output and a
  replay that operates the real public process/network/CLI/browser interface.
- The final replay records executable and library hashes, source input hashes,
  exact case counts, expected process terminations, unexpected exits, resource
  cleanup, and the boundaries that remain untested.
- The campaign may disprove a suspected issue or correct a faulty oracle only
  with a concrete contract and independent observation; expectations are never
  loosened merely to obtain a pass.
- Passing this bounded campaign is W9 evidence, not proof of total security or
  W10 release qualification.
