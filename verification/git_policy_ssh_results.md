# Git runtime v2 adversarial qualification

## Qualified slice

This evidence covers Diamond 0.16.0's bounded `git-v2` runtime adapter:

- smart HTTPS and forced-command OpenSSH clone, fetch, push, and archive using
  the installed Git executable;
- exact and terminal-prefix wildcard branch/tag rules with force, deletion,
  live role, approval, pipeline, discussion, signed-commit, and secret policies;
- account SSH keys, repository-scoped read/write deploy keys, and expiring SSH
  certificate principals with live disablement and revocation;
- signed, short-lived receive claims bound to the application, binding,
  repository, actor type, actor ID, store identity, generation, nonce, and
  expiry;
- exact runtime-owned pre-receive and post-receive hooks, validated Git
  quarantine paths, durable private event spooling, bounded event delivery, and
  reconciliation; and
- serialized receive/cutover fencing, exact repository/ref inventory checks,
  generation compare-and-swap, and immediate rejection of the old shard after
  promotion.

Product policy, RPCs, and the reactive controls are Diamond code in
`examples/git-browser`. Git/OpenSSH execution, streaming, key parsing,
quarantine validation, receive enforcement, and shard fencing stay in the
bounded Rust runtime.

## Real-interface evidence

Run the retained campaign with:

```sh
python3 verification/git_policy_ssh_redteam.py
```

The final replay passed **196 of 196** scenarios using Diamond 0.16.0, Git
2.56.0, OpenSSH 10.2p1, PostgreSQL 18.6, and Python 3.14.8. It created an
isolated PostgreSQL cluster, Diamond web server, two Git shard roots, OpenSSH
daemon, host and CA keys, account/deploy/certificate identities, and real
working clones, then removed the disposable state.

The replay exercised:

- stock Git clone, push, and remote archive over real OpenSSH plus smart HTTPS;
- an 8 MiB incompressible SSH transfer, four concurrent byte-exact archives of
  that object, and a 64 MiB live receive used during a fenced shard cutover;
- protected fast-forward, force/delete rejection, atomic rejection, a 257-ref
  bound, live administrator demotion, and exact hook tamper rejection;
- wildcard boundary behavior, signed commit success, unsigned commit rejection,
  secret rejection, and attempts to reuse an already reachable unsigned or
  secret-bearing commit through a newly protected ref;
- missing, wrong-object, insufficient-approval, failed-pipeline, and complete
  candidate gates;
- read-only and write deploy keys, a short-lived OpenSSH certificate, live
  revocation, account disablement, key revocation, shell/command injection,
  traversal, malformed key, unsafe launcher, and unknown identity probes;
- durable user and deploy push events, acknowledgment, local spool cleanup, and
  exact ref-inventory reconciliation;
- a paused `git-receive-pack` process proving promotion waits for the active
  receive, rejection of the target that missed that push, successful retry
  after exact synchronization, stale-generation rejection, old-shard fencing,
  strict `git fsck`, and a generation-two push through the promoted shard.

Machine-readable results are retained in
`verification/git_policy_ssh_redteam_results.json`. The earlier Git/OAuth and
compiler campaigns remain in `verification/git_foundation_redteam_results.json`
and `verification/git_compiler_boundaries_results.json`.

## Findings repaired

1. A receive fence originally owned a synchronous PostgreSQL runtime that could
   be dropped from a Tokio worker and panic. The advisory-lock connection now
   lives on a dedicated thread and is joined when the transport releases it.
2. The policy subprocess cleared Git's quarantine environment, so valid signed
   commits were invisible and content checks could not examine newly received
   objects. The hook now preserves only object paths that canonicalize inside
   the exact managed bare repository.
3. A commit could be pushed to an unprotected ref and reused as a new protected
   ref without signature or secret validation. New protected refs now inspect
   their bounded history regardless of reachability from other refs.
4. The event-list limit was serialized as a PostgreSQL `int4` for a `LIMIT`
   parameter inferred as `int8`, returning an unavailable error. The adapter
   now sends the checked `i64` wire value directly.
5. Replaying committed spool files in random nonce order could roll the stored
   ref inventory backward. Receives are serialized per binding, and
   reconciliation takes the same exclusive fence and replaces the inventory
   from the authoritative source shard.
6. Promotion initially checked only store identity and generation, so a stale
   replica could be activated. It now compares the exact registered repository
   set and every branch/tag object and rejects unfinished receive state before
   the generation changes.

## Bounded claims

The qualified adapter provides one writer at a time per Git binding. This is a
deliberate correctness and memory bound for the first release; separate bindings
can proceed independently. New protected refs inspect at most 512 commits,
secret scanning inspects at most 256 blobs, 4 MiB per blob, and 32 MiB total,
and oversized histories fail closed. The connected HTTP gateway separately
admits at most eight live smart-Git transports and returns explicit overload
when that budget is exhausted.

The secret scanner recognizes the explicitly implemented high-confidence token
and private-key forms. It is not a general secret-classification engine. Signed
commit policy currently trusts active account Ed25519 keys and verified account
emails. The Diamond application implements organizations, role-based membership,
teams, issues, a bounded merge-request flow, LFS, CI, artifacts, OCI pull, and
Pages above this adapter. Their separate retained evidence is aggregated in
`git_release_qualification_results.md`.

The local OpenSSH test uses generated `AuthorizedKeysFile` and
`AuthorizedPrincipalsFile` data because it does not install root-owned
production commands. The lookup commands, forced options, stock OpenSSH
sessions, trusted CA, and live adapter rechecks were exercised. Production
still trusts Git, OpenSSH, the operating system, PostgreSQL, the private shard
owner, the fixed SSH launcher owner, the SSH CA operator, and deployment
administrators.
