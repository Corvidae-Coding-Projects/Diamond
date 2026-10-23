# Operating Diamond Forge

This guide covers the product-level operations around the runnable Diamond
Forge example. Read `examples/git-browser/README.md` first for initial
PostgreSQL, storage, OAuth, and server setup.

Diamond owns accounts, authorization, repository metadata, job scheduling,
artifact metadata, Pages deployments, and custom-domain mappings. It delegates
Git semantics to the installed `git` executable, isolated job execution to the
configured systemd or Bubblewrap backend, PostgreSQL durability to PostgreSQL,
and public TLS and DNS enforcement to the deployment edge.

## Production process layout

Run these as distinct supervised services:

- two or more `diamond serve` web nodes behind one HTTPS reverse proxy;
- one `project-pages-serve` process on a cookie-free Pages origin;
- a pool of repeatedly invoked `project-delivery-work` workers;
- a pool of repeatedly invoked `project-ops-work` workers;
- scheduled `project-git-reconcile` runs; and
- PostgreSQL with tested continuous archiving and point-in-time recovery.

All nodes use the same PostgreSQL database and declared shared-object, Git,
delivery, and operations stores. Store directories must be canonical, owned by
the service account, mode `0700`, and unavailable to unrelated services. Never
place two writable filesystem stores behind one binding: each repository has
exactly one current shard placement and promotion fences the old generation.

Before admitting traffic, run:

```sh
diamond check examples/git-browser/diamond.toml
diamond project-deploy-check examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES \
  --runtime-env DIAMOND_RUNTIME \
  --origin https://forge.example.com \
  --identity-mailbox /srv/diamond/identity-mail
```

Collect structured service logs and alert on readiness, stale heartbeats, queue
age, failed jobs, Git reconciliation errors, storage integrity errors, and
recovery-catalog failures. Drain a web node from **Scale & recovery** before
replacing it.

## Enrollment and OAuth

Google and GitHub callbacks are:

```text
https://forge.example.com/_diamond/oauth/google/callback
https://forge.example.com/_diamond/oauth/github/callback
```

Keep provider secrets in the environment variables named by `DIAMOND_RUNTIME`;
do not put secrets in `diamond.toml`. Keep the OAuth transaction key independent
from either provider secret and use at least 32 random characters.

The example supports four installation policies:

- `public`: anyone may create a local account or enter through an enabled OAuth
  provider;
- `restricted`: admit configured email domains, Google tenants, GitHub
  organizations, or exact provider identities;
- `invite`: require a currently valid single-purpose invitation; and
- `closed`: only administrators provision users.

Choose the narrowest policy that fits the installation. Provider admission and
local registration are separate decisions. A provider-authenticated address is
not sufficient for a company installation unless the tenant, organization,
domain, or identity rule also matches.

## Import and export with stock Git

Diamond does not invent an archive format. Export all hosted Git refs with a
mirror clone:

```sh
git clone --mirror \
  https://diamond:SOURCE_CREDENTIAL@forge.example.com/_diamond/git/repos/SOURCE_REPOSITORY.git \
  repository-export.git
git -C repository-export.git fsck --strict
git -C repository-export.git show-ref
```

The adapter accepts branches, tags, and Git notes. A mirror clone preserves
annotated tag objects and `refs/notes/*`, as well as Unicode path names and file
bytes. LFS content is stored outside the bare repository, so export it with a
Git LFS client when the repository uses LFS:

```sh
git -C repository-export.git lfs fetch --all
```

To import, create and initialize the destination repository in Diamond, mint a
write credential scoped to it, and push the mirror:

```sh
git -C repository-export.git push --mirror \
  https://diamond:DESTINATION_CREDENTIAL@forge.example.com/_diamond/git/repos/DESTINATION_REPOSITORY.git
```

Protected-ref policy remains authoritative during import. After an import,
clone the destination into a new directory, run `git fsck --strict`, and compare
`git show-ref` output before retiring the source.

## Runner executor profiles

Each delivery binding declares between one and eight named executor profiles
and must include `standard`:

```toml
[delivery.bindings.builds.executors.standard]
memory_bytes = 536870912
max_processes = 128
max_file_bytes = 67108864

[delivery.bindings.builds.executors.small]
memory_bytes = 134217728
max_processes = 32
max_file_bytes = 16777216
```

A repository selects only one declared name in `.diamond-ci.json`:

```json
{
  "version": 1,
  "jobs": [
    {
      "name": "test",
      "executor": "small",
      "script": "./ci/test.sh",
      "timeout_seconds": 300
    }
  ]
}
```

Unknown profiles reject the pipeline before a runner starts. The runtime
applies the profile's memory, process, file-size, and wall-time limits to the
configured isolation backend. Isolation failure still fails closed. Public,
untrusted jobs require a disposable VM or equivalently reviewed sandbox outside
Diamond's process.

## Artifact and deployment lifecycle

Generic artifact downloads support `HEAD`, one HTTP byte range, `ETag`, and
`If-None-Match`. Multiple ranges are intentionally unsupported. A repository
writer may delete artifact metadata from **CI & delivery**; authorization is
rechecked before bytes stream, so the download route is fenced immediately.
Physical content-addressed bytes are removed later by held, grace-bounded
garbage collection.

Pages deployments are immutable. Activating an older retained deployment is an
atomic rollback. Stopping an environment changes its durable state without
deleting its deployment history or artifacts.

## Pages custom domains

Only a Diamond account administrator may add or remove a custom-domain mapping.
Diamond accepts canonical lowercase DNS hosts, routes the exact `Host` to the
selected site, and stops routing it immediately after removal. A mapping is not
proof of DNS ownership and does not issue a certificate.

Use this deployment sequence:

1. Verify control of the requested domain outside Diamond.
2. Configure the reverse proxy or CDN to route that host to the separate Pages
   listener.
3. Obtain and renew its certificate through the edge's maintained ACME client.
4. Add the mapping from **CI & delivery** as a Diamond administrator.
5. Verify HTTPS, the expected site, isolation headers, and the absence of
   Diamond session cookies before publishing DNS broadly.

The edge must reject unknown hosts and must not route control-plane session
cookies to Pages. Removing a mapping in Diamond does not remove public DNS or a
certificate; retire those separately at the edge.

## Notifications and search

Each user controls notifications per repository. They may disable a repository
or request unread-only results. Preferences affect the user's web feed; they do
not change authorization or reveal an event the user can no longer read.

Global search normalizes surrounding whitespace, treats punctuation literally,
and ranks exact titles and title terms above body-only matches. Results are
reauthorized against current repository policy when read, so revocation takes
effect without waiting for reindexing. Search indexes are rebuildable
projections; PostgreSQL and Git remain authoritative.

## Backup and isolated restore

Create an encrypted snapshot into a new private directory:

```sh
diamond project-backup examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES \
  --runtime-env DIAMOND_RUNTIME \
  --out /srv/diamond/backups/2026-10-23 \
  --backup-key-env DIAMOND_BACKUP_KEY
```

The snapshot covers PostgreSQL and every declared shared-object, Git, delivery,
and operations root. CI workspaces and rebuildable search projections are not
recovery authorities. Copy encrypted backup artifacts off-host under a separate
credential and retention policy.

Test restoration only into an empty database and empty canonical roots:

```sh
diamond project-restore examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_RESTORE_POSTGRES \
  --runtime-env DIAMOND_RESTORE_RUNTIME \
  --from /srv/diamond/backups/2026-10-23 \
  --backup-key-env DIAMOND_BACKUP_KEY
```

Restore verifies ciphertext and per-file hashes before import, repairs
root-relative Git hook paths, revokes restored sessions and API credentials, and
validates routed Git access. Keep restored services isolated until independent
counts, refs, objects, permissions, artifacts, Pages output, and audit continuity
match the recovery plan.

## Incident and maintenance checklist

When a repository update has an uncertain outcome, do not blindly replay it.
Inspect the durable push event and run:

```sh
diamond project-git-reconcile examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

For an uncertain job outcome, inspect immutable pipeline and fencing state
before retrying. Revoking an account, repository grant, API credential, deploy
key, or SSH certificate principal takes precedence over cached UI state.

Before upgrades or storage work:

1. record a fresh encrypted backup and pass an isolated restore;
2. drain affected web or worker nodes;
3. confirm the current repository placement and generation;
4. preserve the event spool and all declared roots;
5. apply the change to one node or shard;
6. replay health, clone/fetch, push policy, artifact, Pages, and job checks; and
7. return capacity only after readiness and reconciliation are clean.

The retained qualification commands and bounded coverage are documented in
`verification/git_*_results.md`. Passing them is evidence for the tested
profile, not a substitute for validating an installation's reverse proxy, DNS,
TLS, PostgreSQL, runner images, operating system, and backup custody.
