# Publishing catalog

This application keeps private drafts, author-only edits, public reads and JSON
APIs, durable deduplicated notifications, and live article activity in Diamond.
The Rust runtime supplies reusable checked adapters; the generated browser runtime
comes from the Diamond build.

From a clean checkout, install the exact locked release and scaffold a private
copy without editing Rust, JavaScript or SQL:

```sh
cargo install --locked --path . --root "$PWD/.diamond/install"
export PATH="$PWD/.diamond/install/bin:$PATH"
diamond new .diamond/catalog --template catalog
diamond check .diamond/catalog/diamond.toml
diamond build .diamond/catalog/diamond.toml --out .diamond/catalog-build
```

Follow the
[clean reference-application walkthrough](../../docs/deployment.md#clean-reference-application-walkthrough)
to create the disposable PostgreSQL/runtime configuration and run two servers plus
two workers. In the browser, sign in as the provisioned author, create and edit a
draft, publish it, wait for the notification job, then sign out and browse the
published article by author. Repeat reads through each backend to verify that the
result is shared rather than process-local.

The notification contract is at least once with a stable deduplication key. Public
reads remain policy checked. See the [jobs/live guide](../../docs/jobs-and-live-apps.md)
and [release qualification](../../docs/release-qualification.md) for the exact
tested profile and remaining boundaries.
