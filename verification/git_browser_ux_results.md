# Git browser UX qualification

Date: 2026-10-25

## Scope

This pass covers the example Git browser's anonymous entry, signed-in workspace
selection, repository creation/opening, issue selection, labels, board columns,
and the Diamond runtime support needed for native dynamic select options.

The interaction design follows recognition over recall, explicit labels and
instructions, visible status, strong keyboard focus, and comfortably sized
controls. Reference material:

- <https://www.nngroup.com/articles/recognition-and-recall/>
- <https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions>
- <https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html>
- <https://www.w3.org/WAI/WCAG22/Understanding/focus-appearance>
- <https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum>

## Real-interface replay

The project was served against a disposable PostgreSQL cluster and isolated Git,
object, delivery, operations, and identity-mailbox roots. Chrome exercised the
actual generated application and a retained dynamic-select regression fixture.

Observed results:

- Anonymous users received distinct sign-in and account-creation regions with
  Google, GitHub, and local-account paths.
- A synthetic administrator signed in, created an organization and repository,
  initialized Git storage, and reopened the workspace by organization and
  repository name rather than by copying identifiers.
- Creating an issue, label, and board column immediately populated native select
  controls; prerequisite-dependent actions became enabled only when valid.
- A board card was added and moved by selecting its issue, with no board-card ID
  entry required.
- Selecting an issue did not expose an empty detail editor; details appeared only
  after the explicit Open action.
- A one-row result page kept Next page disabled.
- Dynamic select hydration retained four direct option children, no
  `diamond-fragment` element, `data-ready="true"`, and no visible runtime error.
- Repeated alpha, beta, and Unicode `snow 雪` selections remained reactive and
  produced the exact selected value.

## Automated gates

```text
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-features --lib --bins
TMPDIR=target/test-tmp cargo test --all-features --doc -j 1
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --all-features
cargo run --quiet -- check examples/git-browser/diamond.toml
cargo run --quiet -- check verification/regressions/w3_dynamic_select/diamond.toml
git diff --check
```

All gates passed. The first unconstrained documentation-test run crashed
`rust-lld` with a bus error inside the shared `/tmp` environment; the exact
tests passed when serialized with `TMPDIR` on the workspace filesystem.

## Boundaries

The replay did not complete real third-party Google or GitHub authorization,
exercise every administrative route, upload a file, or perform a destructive
production action. Those integrations and authorization boundaries retain their
existing qualification; this pass specifically validates the redesigned
navigation and common Git-workspace journey.
