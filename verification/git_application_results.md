# Diamond Forge application qualification

Date: 2026-10-23

This evidence covers the runnable G1 Diamond Forge application in
`examples/git-browser`. It is a bounded claim about this application slice and
its native Git adapter, not the completion of the broader G2 through G6 product
checkpoints.

## Real-interface evidence

Run the retained application campaign with:

```sh
python3 verification/git_application_redteam.py
```

The final replay passed **103 of 103** checks against a disposable PostgreSQL
cluster, object store, Git root, identity mailbox, Diamond server, and stock Git
working copy. It exercised three accounts, a private organization repository,
live role grant and revocation, cursor pagination over 22 issues, hostile stored
HTML-like text, issue comments, exact repository credentials, smart HTTP clone
and push, protected refs, merge requests, and repository recovery.

The merge campaign rejected author self-approval, duplicate reviewer approval,
an unresolved discussion, a forged candidate object, a replayed merge plan, and
a source ref changed after review. The successful path used an exact
fast-forward compare-and-swap, finalized application state from the
authoritative target ref, emitted a durable user event, reconciled its committed
spool, and ended with strict `git fsck`.

Machine-readable evidence is in `git_application_redteam_results.json`. The
compiler campaign passes **14 of 14** cases. The compatibility Git/OAuth
campaign passes **109 of 109**, and the protected-ref/OpenSSH/sharding campaign
passes **192 of 192**.

A final real Chrome replay passed **15 of 15** browser checks. It exercised
login, reactive organization and repository lists, HEAD/tree/file/history
views, client-side route navigation, issue pagination, hostile text rendering,
merge-request states, durable events, and a 375-pixel responsive viewport with
no horizontal overflow. The hostile title remained a text node and opened no
dialog. The retained browser receipt is
`git_application_browser_results.json`.

## Findings repaired

| Finding | Impact before repair | Final behavior |
| --- | --- | --- |
| Private issue policies trusted the original author after membership revocation | A removed member could retain access to private repository content they had created | Private repository content now requires live organization ownership or membership on every request |
| Repository records used organization scope for account credentials | A credential requested for one repository inherited an organization-wide authorization scope | Repository and child resources use exact repository scopes; credential creation accepts only the selected repository ID |
| A first merge design combined the Git ref update with application database writes | The operation violated Diamond's external-effect transaction rule and could imply false atomicity | Merge preparation is read-only, the Git compare-and-swap is isolated, and retry-safe finalization rereads the authoritative ref |
| Child visibility was accepted from a browser RPC argument | A caller could create an externally readable issue or merge request under a private repository | Child visibility and organization are derived from the authorized repository record |
| Merge finalization checked only whether the target reached the candidate | A direct push could make an unapproved request appear merged | Finalization also requires recorded approval, resolved discussions, and an open request; terminal requests reject further transitions |
| Reconciliation skipped prepared spool files indefinitely | A crash after a ref commit but before the prepared-to-committed rename could lose event delivery and block a later shard promotion | Under the receive fence, reconciliation compares every affected ref, promotes exact committed outcomes, discards exact abandoned outcomes, and rejects ambiguous mixtures |

## Qualified boundaries

The application provides one organization/repository flow, role-based private
access, bounded browsing, a complete issue flow, and an approval/discussion
gated fast-forward merge-request flow. It uses PostgreSQL plus a private Git
root and object directory. The retained transport campaigns cover HTTPS,
OpenSSH, deploy keys, short-lived certificates, protected-ref policy, streaming,
event recovery, and fenced shard promotion.

Live Google and GitHub token exchange still depends on real provider accounts
and deployment callback configuration; the local campaigns cover transaction
state, PKCE, nonce, session/provider binding, duplicate input, cancellation,
replay, admission policy, and cryptographic verifier substitutions. TLS, reverse
proxy configuration, operating-system isolation, PostgreSQL, Git, OpenSSH,
provider availability, and deployment administrators remain trusted boundaries.
