# Git browser foundation

This example exercises Diamond's bounded Git adapter. Diamond code owns the
repository record, authorization policy, RPC operations, and reactive interface.
The runtime invokes one fixed Git executable against one private repository root;
Diamond receives only bounded typed results. This slice also exposes exact
protected refs and account-owned Ed25519 SSH keys.

Create a canonical private Git root and supply it in the runtime grant:

```sh
mkdir -m 700 /srv/diamond-git
export DIAMOND_OAUTH_KEY='replace-with-at-least-32-random-characters'
export DIAMOND_GOOGLE_SECRET='Google OAuth client secret'
export DIAMOND_GITHUB_SECRET='GitHub OAuth client secret'
export DIAMOND_RUNTIME='{"git":{"repos":{"root":"/srv/diamond-git","executable":"/usr/bin/git"}},"oauth":{"transaction_key_env":"DIAMOND_OAUTH_KEY","google":{"client_id":"Google OAuth client ID","client_secret_env":"DIAMOND_GOOGLE_SECRET"},"github":{"client_id":"GitHub OAuth client ID","client_secret_env":"DIAMOND_GITHUB_SECRET"}},"http":{}}'
diamond project-init examples/git-browser/diamond.toml --postgres-env DIAMOND_POSTGRES
diamond project-git-init examples/git-browser/diamond.toml --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond serve examples/git-browser/diamond.toml --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --identity-mailbox /srv/diamond-private-mail --origin http://localhost:3000 --listen 127.0.0.1:3000
```

Repository creation is intentionally two-step. First create the authorized
PostgreSQL record, then initialize the corresponding bare repository through the
idempotent Git operation. This prevents a Git filesystem effect from sharing an
application database write transaction.

Register these exact provider callbacks (replace the origin with the deployed
origin):

```text
http://localhost:3000/_diamond/oauth/google/callback
http://localhost:3000/_diamond/oauth/github/callback
```

For a public deployment, use its exact HTTPS origin. OAuth transactions use
PKCE S256, encrypted one-use server-side state, and the anonymous Diamond
session cookie. Provider identities are keyed by stable provider subject, never
joined by email.

After signing in, register and initialize a repository, then create a one-hour
Git credential. Clone the URL without embedding the credential in shell history;
when stock Git prompts, use the fixed username `diamond` and paste the credential
as the password:

```sh
git clone https://example.test/_diamond/git/repos/REPOSITORY_ID.git
```

Credential operations are `git:repos:read` and `git:repos:write`; the required
scope is `{"repository":["REPOSITORY_ID"]}`. Anonymous clones work only when
the Diamond resource policy permits anonymous reads.

Enter an exact ref such as `refs/heads/main` and use **Protect ref** to reject
force-pushes and deletion while allowing authenticated fast-forward updates.
The server function shows the complete `host.git_protect` signature if the
application needs an administrator-only rule or explicitly allows force or
delete. **Remove protection** calls `host.git_unprotect` for the same exact ref.

Paste an OpenSSH Ed25519 `.pub` line into the SSH form. Diamond validates the
wire encoding and returns an opaque key ID; it never accepts or stores a private
key. Configure OpenSSH with `project-git-authorized-key` and the fixed
`project-git-ssh` launcher described in the root README. Stock clients then use:

```sh
git clone ssh://git@example.test/repos/REPOSITORY_ID.git
```
