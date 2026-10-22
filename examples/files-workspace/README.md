# Files workspace

A Diamond accounts/workspace application with private issue attachments and a named
inventory HTTP integration. It reuses the relational account example and adds
Diamond upload/status/download/delete events and typed server HTTP handlers.

Follow [the W6 guide](../../docs/files-and-integrations.md) to prepare a real shared
private directory, initialize PostgreSQL and its store binding, provision a local
administrator and supply native runtime grants. `Http<inventory>` needs a separately
granted destination even when you only use attachments. For isolated tests choose
an explicit loopback fixture; normal external destinations require verified HTTPS.

Sign in, create a workspace/project/issue, choose one file, then click Upload
attachment. Its ID and verified metadata appear after publication. Download uses the
browser's native attachment handling. Files belong to the issue's policy, and are
visible from either application server sharing the same database and object mount.

No fixture secret, database string, file-store path or runtime authority is stored
in the example. This is a development demonstration, not production deployment.
