# Diamond Forge G2 qualification

Date: 2026-10-23

This evidence covers the runnable G1 and G2 Diamond Forge application in
`examples/git-browser`. It is a bounded claim about the tested application,
native Git/LFS adapters, and disposable environment rather than a production
deployment claim. Later delivery, scale/recovery, and release qualification are
recorded separately.

## Real-interface evidence

Run the retained campaign with:

```sh
python3 verification/git_application_redteam.py
```

The final replay passed **184 of 184** checks against a disposable PostgreSQL
cluster, private object and Git roots, identity mailbox, Diamond server, loopback
webhook receiver, stock Git client, and raw Git LFS protocol client. It exercised
three accounts; live organization, team, and direct grants; immediate
revocation; isolated forks; issues, labels, milestones, assignees and boards;
wikis and snippets; review threads and approval dismissal; fast-forward, squash,
and merge-commit integration; notifications, releases, REST operations,
allowlisted webhooks, audit events, LFS transfer and locks, durable event
recovery, and strict repository fsck.

The hostile cases include cross-repository labels, self and duplicate approval,
unresolved and newly introduced review threads, dismissed approval, changed
refs, forged integration results, receipt-free and raw merge RPCs, webhook host
escape, REST operation escalation, anonymous private LFS access, digest
mismatch, another writer's force-unlock, abandoned event spools, and hostile
stored markup. The successful merge paths bind the merge-request ID, strategy,
source, target, and integrated object to a native receipt before application
state can be finalized.

Machine-readable evidence is in `git_collaboration_redteam_results.json` (also
written to the historical `git_application_redteam_results.json` path). The
compiler campaign, lower-level Git/OAuth campaign, and protected-ref/OpenSSH/
sharding campaign remain separate retained layers.

A final real Chrome replay passed **14 of 14** G2 checks. It signed in, loaded
the authorized organization and private repositories, preserved repository
selection across navigation, created a disposable team through the browser,
rendered the existing fork, and visibly rendered every G2 route and its controls:
collaboration, issues/boards, merge reviews, wiki/snippets, releases/LFS,
notifications/audit, and protected refs/webhooks. The retained receipt is
`git_collaboration_browser_results.json`; the earlier G1 browser receipt remains
`git_application_browser_results.json`.

## Findings repaired during G2

| Finding | Impact before repair | Final behavior |
| --- | --- | --- |
| Generic discussion resolution ignored unresolved review threads | A merge could be marked discussion-clean while a thread remained open | Resolution counts live unresolved threads and fails until all are resolved |
| Native review gates only accumulated positive evidence | Dismissing an approval or opening a new thread did not revoke native authorization | Review gate state is replaceable and revokes immediately |
| Raw merge/finalize primitives accepted caller-supplied state | A client could attempt to bypass the merge-request record or forge finalization after a ref move | Only merge-request-bound integration and exact native-receipt finalization are exposed |
| Fork clones retained origin configuration | A copied repository preserved an unnecessary path to its source | Forks remove the origin and are hardened after a no-hardlink bare clone |
| Notification reads required recipient-only access even for create results | Creating a notification for another user could fail after its write | Read policy permits the recipient or recorded actor while listings remain recipient-scoped |
| Long application/binding/repository IDs became LFS filenames | Valid identifiers could exceed filesystem component limits | The storage namespace uses a fixed SHA-256 digest |
| Reconciliation treated the LFS directory as a Git repository | Recovery could fail once an LFS store existed | The reserved LFS namespace is excluded from repository scans |
| Any repository writer could force-unlock another user's LFS lock | A collaborator could break another user's lock | Unlock remains owner-bound, including force requests |
| LFS actions claimed an unsigned expiry | Clients could receive misleading cache lifetime metadata | Unauthenticated expiry metadata is not advertised |
| UI and host merge-strategy spellings differed | Valid fast-forward requests could miss their native strategy | Checked and native values use the same canonical spelling |

## Qualified boundaries

G2 supplies the everyday collaboration foundation, not CI, a registry, Pages,
global search, multi-shard failover, or the final production-hardening pass.
Live Google and GitHub token exchange still depends on real provider accounts
and callback configuration; the local campaigns cover the surrounding state,
PKCE/nonce/session binding, admission policy, replay, and parser boundaries.

TLS, the reverse proxy, operating-system isolation, PostgreSQL, Git, OpenSSH,
provider availability, webhook destination service, backup system, and deployment
administrators remain trusted deployment boundaries. Passing this corpus is
bounded evidence for the tested Linux/Rust/PostgreSQL profile, not proof that no
security defect exists.
