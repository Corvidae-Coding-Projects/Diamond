# Diamond Forge

Diamond Forge is a working Git collaboration application written in Diamond. It
is the walking repository slice of the Git web-interface design: accounts,
Google and GitHub login, configurable enrollment, organizations, repository
permissions, HTTPS and SSH Git transport, repository browsing, issues,
attachments, merge requests, protected refs, deploy keys, and durable push
events.

Diamond uses the installed `git` executable for Git semantics. The application
never receives a shell command, filesystem path, process environment, or raw
database connection. Its `.dia` code declares the records, policies, RPCs, and
interface; the bounded Rust host adapter performs Git operations.

## Run it locally

You need Diamond, Git, PostgreSQL, and an empty private directory for Git and
uploaded files. Create the PostgreSQL database and role using your normal
administration process, then set these values:

```sh
mkdir -p .diamond/forge/git .diamond/forge/objects .diamond/forge/mail
chmod 700 .diamond/forge .diamond/forge/git .diamond/forge/objects .diamond/forge/mail

export DIAMOND_POSTGRES='host=/run/postgresql dbname=diamond_forge user=diamond_forge'
export DIAMOND_OAUTH_KEY='replace-with-at-least-32-random-characters'
export DIAMOND_GOOGLE_SECRET='replace-with-the-provider-secret'
export DIAMOND_GITHUB_SECRET='replace-with-the-provider-secret'
export DIAMOND_RUNTIME="{\"objects\":\"$PWD/.diamond/forge/objects\",\"git\":{\"repos\":{\"root\":\"$PWD/.diamond/forge/git\",\"executable\":\"/usr/bin/git\"}},\"oauth\":{\"transaction_key_env\":\"DIAMOND_OAUTH_KEY\",\"google\":{\"client_id\":\"replace-with-client-id\",\"client_secret_env\":\"DIAMOND_GOOGLE_SECRET\"},\"github\":{\"client_id\":\"replace-with-client-id\",\"client_secret_env\":\"DIAMOND_GITHUB_SECRET\"}},\"http\":{}}"

diamond project-init examples/git-browser/diamond.toml --postgres-env DIAMOND_POSTGRES
diamond project-files-init examples/git-browser/diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-git-init examples/git-browser/diamond.toml \
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

The issues route supports creation, editing, closure, comments, cursor-based
pagination, and files up to 8 MiB. Private issue access follows current
organization membership; revocation takes effect on the next request.

The merge-request route records the exact source and target objects when the
request opens. The author cannot approve their own request. Approval and
discussion state create a short-lived gate for the exact candidate object. A
merge succeeds only as a fast-forward compare-and-swap when both refs still
match the reviewed objects. If the response is lost after Git commits, use
**Finalize committed merge**; it reads the authoritative target ref before
changing application state.

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

The retained application attack replay is
`python3 verification/git_application_redteam.py`. The broader transport and SSH
campaigns are `verification/git_foundation_redteam.py` and
`verification/git_policy_ssh_redteam.py`. They create disposable databases and
repositories and never target a production deployment.

SSH deployment, protected-ref semantics, shard promotion, and the complete
runtime command reference are documented in the root `README.md`.
