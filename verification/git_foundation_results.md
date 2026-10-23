# Git and OAuth foundation qualification

Date: 2026-10-22

This campaign qualifies the bounded Git and OAuth foundation added for Git-backed
Diamond applications. It is evidence for the paths named below, not a claim that
the complete Git platform design is implemented or that the system is free of
all defects.

## Tested profile

- Diamond 0.16.0 development tree
- Git 2.56.0 at a fixed canonical executable path
- PostgreSQL 18.6 in a disposable local cluster
- Python 3.14.8 test driver
- one Diamond server on loopback with a process-owned mode-0700 Git root
- stock Git smart HTTP clients using anonymous, read-only, write, revoked,
  wrong-repository, and wrong-user credentials

The replayable drivers and sanitized results are:

- [`git_foundation_redteam.py`](git_foundation_redteam.py)
- [`git_foundation_redteam_results.json`](git_foundation_redteam_results.json)
- [`git_compiler_boundaries.py`](git_compiler_boundaries.py)
- [`git_compiler_boundaries_results.json`](git_compiler_boundaries_results.json)

## Final evidence

The final live campaign passed 109 of 109 scenarios. It created public and
private repositories through the actual Diamond application, used stock Git for
clone, shallow and filtered clone, push, atomic branch/tag push, push options,
and `fsck`, and compared Diamond's head, tree, blob, history, and diff views with
repository state. A 24 MiB incompressible pack was pushed and cloned through the
streaming path; final sampled server RSS rose from 75,072 KiB to 75,648 KiB.
The meaningful result is bounded streaming rather than the exact change from one
sample run.

The campaign also exercised:

- public, private, cross-repository, read-only, revoked, and non-owner policy
  decisions;
- hostile repository hooks and `uploadpack.packObjectsHook` configuration;
- traversal, encoded traversal, service, revision, path, binding, Basic-auth,
  Git-protocol, ambient-cookie, and discovery-body probes;
- oversized blob previews, repository symlink replacement, interrupted request
  cleanup, and post-attack `git fsck`;
- OAuth PKCE and nonce construction, no-store responses, digested state,
  encrypted transaction secrets, browser-session and provider binding,
  duplicate fields, cancellation, replay, cross-site start, and unknown
  providers;
- absence of raw credentials and OAuth state in the retained server log and
  credential table.

The compiler campaign passed 14 of 14 cases. It proved that the real checker
accepts the reference project and rejects dynamic bindings, dynamic Git
operations, non-text repository IDs, browser-side Git authority, undeclared Git
write, review, and administration capabilities, malformed merge objects,
functions that combine Git writes with database writes, and malformed OAuth
admission policies.

The Rust verifier tests use a synthetic RSA key to accept a valid Google ID
token and reject issuer, audience, authorized-party, nonce, timestamp,
verified-email, key, signature, and algorithm substitutions. Separate tests
exercise OAuth envelope key/context binding and tamper detection and admission
rule combinations.

## Findings fixed

| Finding | Impact before the fix | Final behavior |
| --- | --- | --- |
| Generic API authentication advertised Bearer on Git routes | Stock Git could not negotiate the required Basic credential | Git routes return a Git-specific Basic challenge |
| New bare repositories retained Git's old default HEAD | A successful first push to `main` could clone without checking out content | Repositories initialize with `main` and the populated clone is verified |
| Git `Option` values used ordinary JSON | Typed head/tree results failed Diamond wire decoding | Git values use Diamond's tagged wire representation |
| History records included an extra newline frame | Valid multi-commit history could be rejected | History framing matches the strict parser |
| Git used the generic 12-request subject limit | Ordinary clone and push sequences were rate-limited | Git has bounded, Git-specific shared limits |
| A concealed private repository returned 403 before authentication | Stock Git never offered URL credentials for a private clone | Missing credentials receive 401; presented insufficient credentials remain 403 |
| History fields used a byte Git allows in author and subject text | A crafted commit could make the repository history view unavailable | NUL framing preserves hostile separator text; the live regression passes |
| Early transport/header failure did not explicitly join all child work | A malformed or canceled request could outlive the response path briefly | Input/error tasks are aborted and the Git child is killed and waited |
| The Git-create checker skipped the repository argument's type | A hostile program could defer that rejection to runtime | Both create arguments are statically required to be `Text`; the compiler corpus retains the rejection |
| Git could read `$HOME/.gitconfig` from the store root | A same-account store administrator could add process hooks despite the cleared environment | Both command paths set `GIT_CONFIG_GLOBAL=/dev/null`; a live global `packObjectsHook` is ignored |
| The first hook-disable path was a writable empty directory | A same-account store administrator could populate that directory | `core.hooksPath` is pinned to `/dev/null` and exact repository configuration is revalidated before use |
| The first JWT backend pulled `rsa` with RUSTSEC-2023-0071 | The advisory concerns private-key operations, which Diamond did not use, but left an unfixed advisory in the graph | JWT verification uses the AWS-LC backend; a refreshed `cargo audit` reports no vulnerabilities |

Source review also tightened the exact repository-local Git configuration
allowlist, rejected discovery requests with bodies, continuously reapplied
restricted OAuth membership rules at login, and verified Git store marker and
directory ownership and modes. The hostile configuration, disconnect, policy,
and filesystem cases are included in the retained replay.

## Remaining boundaries

No real Google or GitHub account credentials were used, so live provider token
exchange, Google key rotation over the public network, GitHub verified-email and
organization APIs, and the final browser redirect remain unqualified. The
cryptographic verifier and all local transaction/state paths were exercised,
but a deployment still needs a provider-backed acceptance run.

The broader retained campaigns now cover SSH Git, protected-ref enforcement,
the G1 issue and merge-request application, and fenced storage-shard promotion.
LFS, global search, CI, registries, Pages, and the remaining product checkpoints
in `.design/git-web-interface.md` are outside this foundation campaign. TLS
termination and proxy behavior were not part of this loopback campaign. The
fixed Git executable, operating system, PostgreSQL, TLS roots, Google, GitHub,
and administrators of the private Git root remain trusted boundaries.
