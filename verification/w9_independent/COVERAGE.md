# Independent W9 adversarial coverage

## Executables and outcome

- Frozen baseline: `/tmp/diamond-w9-baseline-b60441f`, SHA-256
  `3d58bff2d28ce47bad5196cc22f3b9bbcd6acae39ee6680bd7195dfc9328a32d`.
- Definitive frozen candidate: `/tmp/diamond-w9-final-4281f59a`,
  Diamond 0.15.0, SHA-256
  `4281f59a9eebda6db8dfa9169a88b6b568898474d194ce599ff1c7a75017a57d`.
- Final aggregate: **PASS**, 8 campaigns and 39/39 independent observations.
- Traffic and process evidence: 100 helper HTTP requests, 12 hostile raw HTTP
  requests, 12 raw WebSocket handshakes, 5 upgraded hostile-frame scenarios,
  and 21 process shutdown receipts. Twenty shutdowns were graceful; the one
  SIGKILL was the intentional pre-snapshot worker fault and matched its expected
  exit. There were no panics, forced cleanup kills, cleanup errors, or retained
  temporary roots.

`results.json` validates that every final receipt has the exact candidate hash,
all observations pass, every temporary root was removed, and all process exits
match their declared schedule.

## Scenario coverage

| Campaign | Final observations | Real behavior exercised |
| --- | ---: | --- |
| `edge_campaign.py` | 19 | Duplicate/conflicting authority and framing headers; absolute, encoded, and double-slash paths; transfer coding; truncated slow body; cookie/bearer/CSRF/profile confusion; strict JSON; event retention at 10,000 with old/current/ahead cursors; live PostgreSQL loss and recovery across two nodes |
| `websocket_malformed.py` | 3 | Twelve hostile upgrade forms, five upgraded malformed-frame schedules, no unauthorized publication, fresh ping/pong and typed event afterward |
| `auth_policy_races.py` | 4 | Cross-node logout-all, account disable, live-socket closure, session/API denial, queued scoped-job dead-lettering, and non-resurrection after re-enable |
| `running_cancel.py` | 2 | Cancellation of a receiver-held running job from another node, fence advance, and stale completion rejection |
| `stale_holder.py` | 2 | Real 30-second database lease expiry under SIGSTOP, second-worker takeover, fence 2 completion, and one receiver-side deduplicated effect |
| `recover_cancel.py` | 2 | Encrypted PostgreSQL/object snapshot, one killed running worker, pending and running restore rows, explicit cancel policy, fence advances, unpause, two inert workers, and fresh-login visibility |
| `job_quota_dedup.py` | 3 | Exact and conflicting stable-key retries at 10,000 job rows, unchanged capacity, and post-edge server health |
| `storage_loss.py` | 4 | Real cross-node upload/finalize/download, same-length corruption, missing blob, live object-root replacement, no fallback reservation, exact restoration, and recovery without node restart |

Direct SQL is limited to controlled fixture population, database-clock state
observation, and the 10,000-row/event boundary setup. Product operations under
test traverse Diamond's real HTTP, RPC, API, WebSocket, CLI, worker, PostgreSQL,
shared-filesystem, backup, and recovery interfaces.

## Definitive commands

Each command used the same immutable final executable:

```text
python3 verification/w9_independent/running_cancel.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/running_cancel_final.json
python3 verification/w9_independent/edge_campaign.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/edge_campaign_final.json
python3 verification/w9_independent/recover_cancel.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/recover_cancel_final.json
python3 verification/w9_independent/job_quota_dedup.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/job_quota_dedup_final.json
python3 verification/w9_independent/websocket_malformed.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/websocket_malformed_final.json
python3 verification/w9_independent/auth_policy_races.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/auth_policy_races_final.json
python3 verification/w9_independent/storage_loss.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/storage_loss_final.json
python3 verification/w9_independent/stale_holder.py --binary /tmp/diamond-w9-final-4281f59a --report verification/w9_independent/stale_holder_final.json
python3 verification/w9_independent/aggregate.py
```

Baseline reproductions used the same scripts with
`--binary /tmp/diamond-w9-baseline-b60441f`. The two confirmed failing receipts
are `running_cancel_baseline.json` and `job_quota_dedup_baseline.json`.

## Boundaries and trust gaps

This is bounded evidence, not proof of exhaustive security. The campaign used
synthetic identities and records, loopback networking, a private Unix-socket
PostgreSQL cluster, local shared storage, handcrafted HTTP/WebSocket peers, and
a controlled deduplicating receiver. It does not cover a public reverse proxy,
remote PostgreSQL with TLS and network faults, hostile DNS infrastructure,
kernel or power-loss durability, distributed filesystem semantics, malware
scanning, third-party browser behavior, or exhaustive protocol fuzzing.

The primary W9 campaign owns the separate sustained 30-minute capacity/SLO run
and the final real-Chrome stale-client/reference-application replay. Their
receipts should be assessed alongside this independent report before the whole
checkpoint is declared complete.
