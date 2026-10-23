# Diamond Forge G4 scale, routing, and recovery qualification

The retained real-interface replay is:

```sh
python3 verification/git_scale_recovery_redteam.py
```

It builds a disposable PostgreSQL cluster and private Git, upload, delivery, and
operations roots; starts two real Diamond web processes; drives the checked RPC
surface over HTTP; pushes with stock Git; and runs multiple native operations
workers. The latest receipt in
`git_scale_recovery_redteam_results.json` records the exact passing check count.

The initial G5 extension reuses that real two-node surface to compare an exact
issue-title match with a body-only match, verify surrounding whitespace is
normalized, and prove SQL wildcard punctuation no longer expands into an
unintended corpus-wide result. The current receipt passes all 108 checks.

Covered behavior includes shared sessions and CSRF across nodes, live/readiness
health, administrator-only topology, private-directory markers, strict shard
identifiers, exact placement generations, decoded global indexing, live policy
reauthorization after grant and revocation, exact-object mirrors, stock
`git fsck --strict`, symlink-root rejection, repository-fenced automatic promotion,
post-promotion write isolation and fresh clone routing, stale-CAS
rejection, leased concurrent worker claims, GC grace and holds, recovery catalog
constraints, policy CAS, cross-node rate limiting, redacted metrics/audit, and
coordinated node drain. It also creates a real encrypted snapshot, rejects a wrong
key and tampered ciphertext before writes, restores PostgreSQL plus all four runtime
root classes into isolation, verifies both Git histories and held delivery bytes,
proves old API credentials are revoked, and clones through the restored placement.

The campaign reproduced and fixed real failures in directory modes, generated
node identifiers, PostgreSQL interval binding, Diamond text decoding for search,
local-only Git mirror transport, mirror-only remote configuration, and synchronous
shutdown database access from Tokio. Its conclusions are bounded to the retained
local two-node/two-shard profile; PostgreSQL high availability, off-host retention,
provider snapshots, cross-host network partitions, and production RTO/RPO remain
operator and G6 qualification responsibilities.
