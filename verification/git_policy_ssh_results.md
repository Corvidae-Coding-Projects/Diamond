# Protected receive and SSH adapter qualification

## Qualified slice

This evidence covers Diamond 0.16.0's second Git runtime slice:

- `GitAdmin<binding>` with checked `host.git_protect` and
  `host.git_unprotect` operations for exact branch and tag refs;
- one runtime-owned pre-receive controller shared by smart HTTPS and SSH;
- signed, short-lived receive claims bound to the app, binding, repository,
  account, store identity, nonce, and expiry;
- live PostgreSQL account and protection reload inside Git's quarantine phase;
- Ed25519 public-key enrollment, listing, revocation, epoch invalidation, and
  SHA-256 fingerprint lookup;
- a restricted OpenSSH authorized-key line and forced-command adapter accepting
  only upload-pack and receive-pack grammar; and
- fixed 64 KiB pumps, a 1 GiB per-direction ceiling, a 15-minute deadline, and
  one Git process per SSH connection.

The product-facing repository policy, protected-ref controls, and SSH-key UI
are Diamond code in `examples/git-browser`. Git/OpenSSH execution, wire
streaming, key parsing, and receive enforcement stay in the bounded Rust
runtime.

## Final real-interface evidence

The final code was built with `cargo build --locked`. The retained campaign is:

```sh
python3 verification/git_policy_ssh_redteam.py
```

It passed 90 of 90 scenarios using Diamond 0.16.0, Git 2.56.0, OpenSSH 10.2p1,
PostgreSQL 18.6, and Python 3.14.8. It created an isolated PostgreSQL cluster,
Diamond server, repository shard, OpenSSH daemon, host key, account key, and
working clones, then removed all disposable state.

Observed behavior included:

- ordinary HTTPS and SSH clone/push with stock Git;
- exact protected-ref fast-forward success, force/delete rejection, explicitly
  allowed force update, and live administrator-role removal;
- all-or-nothing rejection of a mixed atomic update and a 257-ref transaction;
- independent ref inspection after rejected pushes;
- malformed role/ref, SSH key wire data, fingerprint, key ID, repository path,
  extra argument, archive command, newline, shell metacharacter, and traversal
  probes;
- runtime hook replacement, group-writable launcher, launcher symlink, account
  disablement, and key revocation;
- an actual OpenSSH forced-command shell attempt with no shell output; and
- an 8 MiB incompressible object pushed and cloned through the bounded SSH
  pumps with byte-for-byte equality.

The original Git/OAuth campaign also passed 108 of 108 scenarios on the final
code. Its 24 MiB incompressible smart-HTTP transfer observed the Diamond server
at 50,204 KiB baseline and 50,240 KiB peak resident memory, a 36 KiB sampled
delta. The checked-language boundary corpus passed 12 of 12 cases.

Final supporting checks passed:

- `cargo fmt --all -- --check`
- `cargo check --locked`
- `cargo clippy --locked --all-targets -- -D warnings`
- `cargo test --locked` (6 unit security tests and 38 doctests)
- `cargo audit` (288 dependencies, no reported vulnerabilities)
- `python3 verification/examples.py`
- `git diff --check`

Machine-readable receipts are in
`verification/git_policy_ssh_redteam_results.json`,
`verification/git_foundation_redteam_results.json`, and
`verification/git_compiler_boundaries_results.json`.

## Findings repaired during the campaign

1. The first hook installer copied and rehashed the 183 MiB debug executable,
   causing an ordinary repository initialization to exceed the HTTP client
   deadline. The hook is now a mode-0500 exact-byte launcher containing only
   the fixed canonical Diamond executable path; every use validates its owner,
   mode, file type, path, and complete bounded contents.
2. The first signed-claim validator treated the internal application identity
   as a source identifier. Real application identities can start with a digit,
   so every push failed closed. The validator now uses the exact safe filename
   alphabet and length accepted by the repository layout.
3. The first OpenSSH deployment assumed custom runtime environment variables
   survived into the forced command. Real OpenSSH did not preserve them. The
   authorized-key command now binds the opaque key ID to a canonical,
   process-owned, non-group-writable launcher; secrets stay in deployment-owned
   configuration rather than `authorized_keys`.
4. The first counted SSH proxy withheld binary packet output until flush and
   deadlocked a real clone. Each fixed-size chunk is now written and flushed
   immediately. Stock clone, push, and multi-chunk incompressible transfers
   pass over the final implementation.
5. The account key listing originally showed the stored revocation bit without
   incorporating account-epoch invalidation. It now reports effective
   revocation by joining the current account epoch.

All five reproduced failures or design defects are fixed in the final replay.

## Boundaries not claimed

This slice supports exact refs, `authenticated` and `account_admin` rules,
Ed25519 user keys, upload-pack, and receive-pack. It does not yet implement
teams, deploy keys, SSH certificates, archive-over-SSH, wildcard ref rules,
approval/pipeline/discussion gates, signed-commit policy, secret-push scanning,
the durable post-receive event spool, or shard fencing. Those remain product
and runtime work from later Git-interface checkpoints.

The local qualification used the generated authorized-key output as an
OpenSSH `AuthorizedKeysFile` because an unprivileged disposable test cannot
install the root-owned production `AuthorizedKeysCommand`. The fingerprint
lookup command itself, its forced output, and the resulting real OpenSSH
sessions were exercised independently. Production still trusts Git, OpenSSH,
the operating system, PostgreSQL, the private Git-root owner, the fixed SSH
launcher owner, and deployment administrators.
