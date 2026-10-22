# Authority-bound function example

This project collects calls that cross from checked Diamond code into a native
runtime adapter. [`server.dia`](server.dia) shows:

- PostgreSQL version and echo calls;
- destination-scoped secret, HTTP GET, and HTTP POST calls;
- job submission, status, cancellation, and worker deduplication keys;
- typed event publication.

[`ui.dia`](ui.dia) shows generated RPC, every account/session operation, file
upload/status/download/delete, and a live `<watch>` subscription.
[`diamond.toml`](diamond.toml) declares each capability, resource, API operation,
file binding, job, event, and identity profile. The declarations are as important
as the call: source code cannot create its own authority.

Check and build without supplying credentials or opening services:

```sh
target/debug/diamond check examples/functions/authority/diamond.toml
target/debug/diamond build examples/functions/authority/diamond.toml \
  --out .diamond/authority-functions
```

Runtime execution needs a disposable PostgreSQL database, private object root,
private account mailbox, and an explicit `inventory` HTTP grant. The complete
[`live-workspace`](../../live-workspace/) walkthrough shows that environment.
Never commit real database strings, passwords, HTTP credentials, or private paths.
