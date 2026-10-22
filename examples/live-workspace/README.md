# Live workspace

This application keeps its business logic in Diamond: accounts, workspace
membership, projects, issues, comments, private attachments, a durable background
edit and typed live activity. The Rust runtime supplies reusable checked adapters;
the generated browser runtime comes from the Diamond build.

From a clean checkout, install the exact locked release and scaffold a private
copy without editing Rust, JavaScript or SQL:

```sh
cargo install --locked --path . --root "$PWD/.diamond/install"
export PATH="$PWD/.diamond/install/bin:$PATH"
diamond new .diamond/live-workspace --template workspace
diamond check .diamond/live-workspace/diamond.toml
diamond build .diamond/live-workspace/diamond.toml --out .diamond/live-workspace-build
```

The connected journey needs a disposable local PostgreSQL database reached over
one Unix socket, a private shared-filesystem root, a private mail directory and
the exact public origin. Initialize them with the commands in the
[deployment guide](../../docs/deployment.md#clean-reference-application-walkthrough),
then start two `diamond serve` processes and two `diamond project-work`
processes. Put an HTTPS proxy in front of the two loopback servers before exposing
the application beyond the local machine.

In the browser, sign in, create a workspace and project, create an issue, add a
comment and attachment, queue the summary job and watch the live status. Open a
second private window through the same proxy to confirm that state and sessions do
not depend on one application process. The file limit is 8 MiB. External delivery
is at least once and requires receiver deduplication.

See the [jobs/live guide](../../docs/jobs-and-live-apps.md),
[files/integrations guide](../../docs/files-and-integrations.md) and
[release qualification](../../docs/release-qualification.md) for contracts and
measured boundaries.
