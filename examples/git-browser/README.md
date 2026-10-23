# Diamond Forge

Diamond Forge is a working Git collaboration application written in Diamond. It
is the runnable G3 slice of the Git web-interface design: accounts, Google and
GitHub login, configurable enrollment, organizations, teams, direct repository
permissions, forks, HTTPS and SSH Git transport, repository browsing, issues,
boards, wikis, snippets, review threads, three merge strategies, notifications,
releases, Git LFS, REST operations, bounded webhooks, audit events, protected
refs, deploy keys, durable push events, isolated CI jobs, caches, generic
artifacts, OCI pulls, environments, and static Pages.

Diamond uses the installed `git` executable for Git semantics. The application
never receives a shell command, filesystem path, process environment, or raw
database connection. Its `.dia` code declares the records, policies, RPCs, and
interface; the bounded Rust host adapter performs Git operations.

## Run it locally

You need Diamond, Git, PostgreSQL, and an empty private directory for Git and
uploaded files. CI additionally needs an owner-private delivery directory,
`prlimit`, and either Bubblewrap or a per-user systemd service manager. Create
the PostgreSQL database and role using your normal administration process, then
set these values:

```sh
mkdir -p .diamond/forge/git .diamond/forge/objects .diamond/forge/delivery .diamond/forge/mail
chmod 700 .diamond/forge .diamond/forge/git .diamond/forge/objects .diamond/forge/delivery .diamond/forge/mail

export DIAMOND_POSTGRES='host=/run/postgresql dbname=diamond_forge user=diamond_forge'
export DIAMOND_OAUTH_KEY='replace-with-at-least-32-random-characters'
export DIAMOND_GOOGLE_SECRET='replace-with-the-provider-secret'
export DIAMOND_GITHUB_SECRET='replace-with-the-provider-secret'
export DIAMOND_WEBHOOK_SECRET='replace-with-the-webhook-bearer-secret'
export DIAMOND_RUNTIME="{\"objects\":\"$PWD/.diamond/forge/objects\",\"git\":{\"repos\":{\"root\":\"$PWD/.diamond/forge/git\",\"executable\":\"/usr/bin/git\"}},\"delivery\":{\"builds\":{\"root\":\"$PWD/.diamond/forge/delivery\",\"sandbox\":\"/usr/bin/systemd-run\",\"limit\":\"/usr/bin/prlimit\"}},\"oauth\":{\"transaction_key_env\":\"DIAMOND_OAUTH_KEY\",\"google\":{\"client_id\":\"replace-with-client-id\",\"client_secret_env\":\"DIAMOND_GOOGLE_SECRET\"},\"github\":{\"client_id\":\"replace-with-client-id\",\"client_secret_env\":\"DIAMOND_GITHUB_SECRET\"}},\"http\":{\"webhook\":{\"origin\":\"http://127.0.0.1:4000\",\"network\":\"loopback\",\"paths\":[\"/hooks/\"],\"methods\":[\"POST\"],\"credential_env\":\"DIAMOND_WEBHOOK_SECRET\",\"timeout_ms\":3000,\"max_response_bytes\":65536,\"max_redirects\":0}}}"

diamond project-init examples/git-browser/diamond.toml --postgres-env DIAMOND_POSTGRES
diamond project-files-init examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-git-init examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-delivery-init examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-admin-add examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --user alice --email alice@example.test
diamond serve examples/git-browser/diamond.toml --listen 127.0.0.1:3000 \
  --origin http://127.0.0.1:3000 --postgres-env DIAMOND_POSTGRES \
  --runtime-env DIAMOND_RUNTIME --identity-mailbox .diamond/forge/mail
```

Open `http://127.0.0.1:3000/`, sign in as the administrator, create an
organization, create repository metadata, and click **Initialize Git**. The two
steps are separate because PostgreSQL and a Git ref update cannot share an
atomic transaction.

For Google and GitHub, register these provider callbacks, replacing the local
origin with the public HTTPS origin in production:

```text
http://127.0.0.1:3000/_diamond/oauth/google/callback
http://127.0.0.1:3000/_diamond/oauth/github/callback
```

`diamond.toml` currently allows public local registration and public provider
admission. Set enrollment to `closed` or configure OAuth admission as
`restricted`, `invite`, or `closed` for an internal installation. Restricted
admission supports email domains, Google tenants, GitHub organizations, and
named provider identities.

## Use Git over HTTPS

Select a repository and click **Create one-hour Git credential**. Copy the
credential immediately. Stock Git uses the fixed username `diamond` and the
credential as its password:

```sh
git clone https://forge.example.test/_diamond/git/repos/REPOSITORY_ID.git
```

The credential is scoped to that exact repository and the read/write Git
operations. A repository credential does not grant organization-wide access.
Public repositories allow anonymous fetch only when the Diamond resource policy
does too.

## Use the application

The repository route lists organizations and repositories and provides bounded
HEAD, tree, blob, history, and diff views. Copy the displayed opaque IDs into
the selection fields when moving between routes.

The collaboration route manages repository-scoped team and direct grants, with
immediate revocation, and creates a fork by copying the authorized bare
repository without filesystem hard links. The issues route supports creation,
editing, closure and reopening, comments, labels, milestones, assignees,
cursor-based pagination, boards, and files up to 8 MiB. Private access follows
the current organization, team, or direct grant on every request.

The merge-request route records the exact source and target objects when the
request opens. The author cannot approve their own request. Approvals can be
dismissed; every review thread must be resolved. Both changes immediately
recompute the native gate for the exact candidate object. Fast-forward, squash,
and merge-commit strategies recheck both refs, native gate state, and protected
ref policy. The Git adapter writes a receipt bound to the merge-request ID,
strategy, source, target, and result. A separate finalization RPC accepts only
that receipt, so a raw ref move cannot forge application merge state.

Wiki pages retain revisions; snippets are repository-scoped. The releases route
requires a tag or ref that resolves to a commit and accepts release assets up to
8 MiB. Stock Git LFS clients use the clone URL's standard `info/lfs` endpoint:

```sh
git lfs install
git lfs track '*.bin'
git add .gitattributes path/to/file.bin
git commit -m 'Track large object'
git push
```

LFS objects are streamed with declared-size and SHA-256 verification. Private
uploads/downloads need the same repository-scoped Git credential; locks are
owner-bound, including force-unlock attempts.

## Run CI and publish artifacts

Commit a strict `.diamond-ci.json` file. Jobs are ordered; `needs` may name only
an earlier job. Every path is relative to the immutable checkout, and unknown
fields or path traversal reject the pipeline before it runs.

```json
{
  "version": 1,
  "jobs": [
    {
      "name": "publish",
      "script": "mkdir -p out site\nprintf 'release\\n' > out/app.txt\nprintf '<h1>Hello</h1>\\n' > site/index.html\nprintf 'layer\\n' > image.tar",
      "timeout_seconds": 60,
      "artifact": {"name": "release", "path": "out/app.txt"},
      "cache": {"key": "main", "path": "cache.bin"},
      "pages": {"site": "docs", "path": "site"},
      "oci": {"name": "example", "tag": "latest", "path": "image.tar"},
      "environment": "preview"
    }
  ]
}
```

Push the commit, open **CI & delivery**, select the repository, and schedule its
ref with a unique event ID. Run workers under a supervisor; each command claims
at most one job, which makes process supervision and rolling replacement
straightforward:

```sh
diamond project-delivery-work examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

The systemd backend uses a transient chroot-style service with read-only system
executables, no host filesystem view, no network, a private device view, and
memory/process/deadline limits. `/usr/bin/bwrap` is also supported when the host
allows unprivileged namespaces. Diamond fails the job if the configured backend
cannot establish isolation; it never falls back to running repository code on
the host.

Generic artifacts and OCI layers stream through the control-plane listener with
the same repository-scoped credential used by Git:

```sh
curl -u diamond:dia_REPLACE_ME \
  http://127.0.0.1:3000/_diamond/delivery/builds/REPOSITORY_ID/artifacts/ARTIFACT_ID

podman pull 127.0.0.1:3000/builds/REPOSITORY_ID/example:latest
```

The OCI pull API is rooted at `/v2/builds/REPOSITORY_ID/IMAGE`. This G3 adapter
supports pull, manifest lookup by tag or digest, blobs, and tag listing; it does
not yet claim the full OCI Distribution conformance surface.

Pages uses a separate listener and never accepts Diamond cookies or control-plane
authorization headers:

```sh
diamond project-pages-serve examples/git-browser/diamond.toml \
  --listen 127.0.0.1:3001 --binding builds \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

The `docs` site above is available at `http://127.0.0.1:3001/docs/`. Put the
Pages listener on a distinct public origin in production.

## Use the REST API and webhooks

Create a short-lived token whose operation allowlist and organization or
repository scopes cover only what the client needs. Calls use the manifest's
versioned operation names:

```sh
curl -sS http://127.0.0.1:3000/api/v1/collaboration_dashboard \
  -H 'Authorization: Bearer dia_REPLACE_ME' \
  -H 'Content-Type: application/json' \
  -H 'X-Diamond-API-Version: 1' \
  --data '{"arguments":["ORGANIZATION_ID","REPOSITORY_ID"]}'
```

A token cannot invoke an operation omitted from its allowlist or escape its
scopes. `GET /api/v1/_contract` describes the exposed wire contract. Webhook
records store only a path below the administrator-bound `webhook` origin; an
application user cannot supply a new host, network class, method, redirect
policy, or bearer secret. Notifications and the administrative audit feed are
available on the Activity route.

The settings route manages exact or terminal-wildcard protected refs, read-only
deploy keys, and pending durable push events. Run `project-git-reconcile` under
supervision to recover committed event spool files and rebuild the ref inventory:

```sh
diamond project-git-reconcile examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

## Read the code

- `shared.dia` defines the bounded values exchanged between server and browser.
- `server.dia` defines data records, live authorization policies, Git adapter
  calls, issues, reviews, and the three-stage merge flow.
- `ui.dia` defines reactive state, events, routes, forms, file operations, and
  accessible status output.
- `diamond.toml` binds modules, least-authority capabilities, PostgreSQL
  resources, APIs, files, events, identity, and Git.
- `git.css` supplies the responsive visual layout.

The retained G2 attack replay is
`python3 verification/git_application_redteam.py`. It exercises the real HTTP,
REST, stock Git, LFS, webhook, PostgreSQL, and recovery interfaces and writes
`verification/git_collaboration_redteam_results.json`. The broader transport
and SSH campaigns are `verification/git_foundation_redteam.py` and
`verification/git_policy_ssh_redteam.py`. They create disposable databases and
repositories and never target a production deployment.

SSH deployment, protected-ref semantics, shard promotion, and the complete
runtime command reference are documented in the root `README.md`.
