# Operable deployment and recovery (W8, qualified in W10)

Diamond 0.16.0 provides a current-version Linux profile: two loopback application
servers and two workers, one dedicated PostgreSQL database per application, a
private shared filesystem for attachments, an explicit private account mail spool,
and a maintained HTTPS proxy. It includes deployment validation and encrypted
quiesced snapshots with isolated restore. No infrastructure is deployed by these
commands. Retained W9 adversarial/distributed evidence and the W10 final-artifact
qualification are recorded separately. This profile does not supply remote/TLS
PostgreSQL connections, SMTP delivery, object-store replication, online backups,
or mixed-version upgrades.
The example service units and edge configuration require operator installation;
this work does not install them or open a public listener.

## Build and initialize

```sh
cargo build --locked --release
cargo install --locked --path .
diamond new .diamond/my-catalog --template catalog
# Or --template workspace for membership, issues, comments, files and jobs.
diamond check .diamond/my-catalog/diamond.toml
diamond build .diamond/my-catalog/diamond.toml --out .diamond/release
```

`new` embeds the complete checked template in the executable and refuses an
existing directory. Ordinary application business logic stays in Diamond. Build
loading rechecks source and compares every generated asset; tampered or mismatched
artifacts fail before serving. Source/builds contain no native database strings,
mailbox contents, backup keys or HTTP credentials.

Create a dedicated database/user and private canonical directories using normal
PostgreSQL/filesystem administration. Supply database authority through a named
environment variable, for example `DIAMOND_POSTGRES` with a Unix-socket connection
string. The connection profile still requires a local PostgreSQL Unix socket.
Multiple processes share this database and the same canonical object directory;
application-local copies are not a replicated durable store. The operator must
provide the filesystem's durability and backup destination. Keep runtime data,
credentials, keys and snapshots outside Git; `.gitignore` covers local artifacts.

Supply `DIAMOND_RUNTIME` as native JSON. This is a shape example, not a credential:

```json
{
  "objects": "/var/lib/diamond/objects",
  "http": {
    "inventory": {
      "origin": "https://inventory.example.invalid",
      "network": "public",
      "paths": ["/v1/"],
      "methods": ["GET", "POST"],
      "credential_env": "INVENTORY_TOKEN"
    }
  }
}
```

Omit `objects` for the catalog, which declares no attachment binding. A real
notification destination must honor the stable job-key deduplication contract.
Keep the HTTP token and database/backup secrets in separately protected service
configuration. Do not put real values into source or this example. For ordinary
Internet destinations the adapter validates TLS, destinations and output limits;
loopback fixtures require a separate explicit development grant.

```sh
diamond project-init .diamond/release --postgres-env DIAMOND_POSTGRES
# For the workspace template, bind the private shared object root explicitly:
diamond project-files-init .diamond/release --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
# Provision the first operator with the normal protected password prompt:
diamond project-admin-add .diamond/release --postgres-env DIAMOND_POSTGRES --user alice --email alice@example.invalid
```

Registration remains disabled unless selected in the source manifest. The private
mail spool is a development/operator delivery boundary: W8 does not turn it into
an SMTP service. Do not promise end-user email delivery without a separately
configured delivery adapter and verification.

## Clean reference-application walkthrough

Start with a clean checkout, Rust 1.96 or newer, PostgreSQL client/server tools,
Git, GnuPG and a maintained Caddy executable. The following creates only ignored
local files. A database administrator must first create one empty database and
least-privilege role for each application; substitute its local Unix-socket
connection string without committing it.

```sh
mkdir -p .diamond/install .diamond/runtime/objects .diamond/runtime/mail
chmod 700 .diamond/runtime .diamond/runtime/objects .diamond/runtime/mail
cargo install --locked --path . --root "$PWD/.diamond/install"
export PATH="$PWD/.diamond/install/bin:$PATH"

diamond new .diamond/workspace --template workspace
diamond new .diamond/catalog --template catalog
diamond check .diamond/workspace/diamond.toml
diamond check .diamond/catalog/diamond.toml
diamond build .diamond/workspace/diamond.toml --out .diamond/workspace-build
diamond build .diamond/catalog/diamond.toml --out .diamond/catalog-build
```

Use separate database variables when operating the two apps. This example runtime
grants the workspace only its private object root and grants neither application an
undeclared destination:

```sh
export DIAMOND_WORKSPACE_POSTGRES='host=/run/postgresql dbname=diamond_workspace user=diamond_workspace'
export DIAMOND_CATALOG_POSTGRES='host=/run/postgresql dbname=diamond_catalog user=diamond_catalog'
export DIAMOND_WORKSPACE_RUNTIME="{\"objects\":\"$PWD/.diamond/runtime/objects\",\"http\":{}}"
export DIAMOND_CATALOG_RUNTIME='{"http":{}}'

diamond project-init .diamond/workspace-build --postgres-env DIAMOND_WORKSPACE_POSTGRES
diamond project-files-init .diamond/workspace-build --postgres-env DIAMOND_WORKSPACE_POSTGRES --runtime-env DIAMOND_WORKSPACE_RUNTIME
diamond project-admin-add .diamond/workspace-build --postgres-env DIAMOND_WORKSPACE_POSTGRES --user alice --email alice@example.invalid

diamond project-init .diamond/catalog-build --postgres-env DIAMOND_CATALOG_POSTGRES
diamond project-admin-add .diamond/catalog-build --postgres-env DIAMOND_CATALOG_POSTGRES --user author --email author@example.invalid
```

Run each application separately. For either build, set `DIAMOND_POSTGRES` and
`DIAMOND_RUNTIME` to that application's values, choose two unused loopback ports,
and run the deployment check. Then start two server commands and two worker
commands under a supervisor:

```sh
export BUILD=.diamond/workspace-build
export DIAMOND_POSTGRES="$DIAMOND_WORKSPACE_POSTGRES"
export DIAMOND_RUNTIME="$DIAMOND_WORKSPACE_RUNTIME"

diamond project-deploy-check "$BUILD" --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --origin https://app.example.invalid --identity-mailbox "$PWD/.diamond/runtime/mail"
diamond serve "$BUILD" --listen 127.0.0.1:3001 --origin https://app.example.invalid --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --identity-mailbox "$PWD/.diamond/runtime/mail"
diamond serve "$BUILD" --listen 127.0.0.1:3002 --origin https://app.example.invalid --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --identity-mailbox "$PWD/.diamond/runtime/mail"
diamond project-work "$BUILD" --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-work "$BUILD" --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

For the catalog, stop those processes, set `BUILD=.diamond/catalog-build`, map
`DIAMOND_POSTGRES` and `DIAMOND_RUNTIME` to the catalog variables, choose fresh
ports, and run the same validation/server/worker sequence.

Point the Caddy template at the two ports, browse only through its canonical HTTPS
origin, and follow the journey in the scaffolded application README. Verify both
backends' readiness before routing traffic. Stop all four actors before changing
the current schema or taking/restoring a snapshot. The retained W10 verifier runs
this topology with disposable databases and fixtures; these written commands do
not create or deploy public infrastructure.

## Validate and operate

```sh
diamond project-deploy-check .diamond/release --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --origin https://app.example.com --identity-mailbox /var/lib/diamond/mail

diamond serve .diamond/release --listen 127.0.0.1:3001 --origin https://app.example.com --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --identity-mailbox /var/lib/diamond/mail
diamond serve .diamond/release --listen 127.0.0.1:3002 --origin https://app.example.com --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --identity-mailbox /var/lib/diamond/mail
# Run in two separate supervised worker processes:
diamond project-work .diamond/release --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

Deployment validation enumerates requested capabilities, checks the exact public
origin, native destination grants, mail spool permissions, shared-object identity,
and current database/job schema. It opens no listener and deploys nothing. HTTPS
origins must be canonical and contain no credentials, path, query or fragment.
Development `http://localhost:PORT` and loopback alternatives remain explicit.

Use [the Caddy template](../deployment/Caddyfile) for edge TLS and round-robin
upstreams. It preserves canonical Host including a nondefault port and removes
Forwarded/X-Forwarded-* headers; Diamond never derives trust from them. It hides
operational endpoints from the public edge and probes readiness internally with
an explicit canonical Host. The tested configuration follows the
[official reverse-proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
and [placeholder definitions](https://caddyserver.com/docs/caddyfile/concepts).
The verifier used official Caddy 2.11.7, its published SHA-512 checksum, a disposable
CA, strict native TLS hostname/CA validation, and WSS. It installed no system trust.
For a real site, configure DNS, certificates, edge/network access and account/mail
handling separately; a local fixture is not evidence of a deployed public site.

All Diamond listeners remain loopback-only; the trusted proxy shares that host or
network namespace. UNIX socket permissions protect PostgreSQL. Loopback peers and
native operators belong to the trusted computing base. Do not expose origin ports
or the database directly. Runtime credentials need least-privilege database roles;
this profile does not claim malicious native-admin confinement.

[Server](../deployment/diamond-server@.service) and
[worker](../deployment/diamond-worker@.service) unit templates use explicit paths,
a dedicated user, a private environment file, restricted write locations and
SIGINT shutdown. Copy/build artifacts and private directories before installing
units. Review PostgreSQL socket visibility when adapting systemd isolation. The
unit templates have been syntax-checked where available; local process tests
establish runtime behavior, not successful installation of these units on a host.

Internal operational endpoints, requiring canonical Host:

| Endpoint | Observation |
| --- | --- |
| `/_diamond/health/live` | Process is serving. |
| `/_diamond/health/ready` | Not draining; current schema and configured object store are available. |
| `/_diamond/health/metrics` | Request count, active ordinary requests, active sockets and draining flag; no identities, bodies or secrets. |

Health endpoints use two dedicated admission permits. Ordinary request or stream
backpressure therefore does not make a live node appear dead, while health traffic
itself remains bounded; excess simultaneous probes receive the normal capacity
response.

Servers emit redacted structured `request_complete` events with request class and
status. Workers emit redacted failure classes. Application payloads, cookies,
CSRF/API tokens, HTTP secrets and database strings are excluded. The supervisor
must rotate/retain these logs and protect operational endpoints. Durable job state
and fenced attempts remain inspectable through authorized status or native
administration; these counters are not a complete security audit trail.

SIGINT stops accepting new work, drains bounded requests/worker attempts, closes
subscriptions and waits for owned work before releasing stores. Route ordinary
traffic to the surviving instance during replacement, then replace it with the
same current artifact and verify readiness. To change application/schema code,
stop all servers/workers, perform explicit supported migrations, validate, then
restart one coordinated current release. The application advisory lock prevents
snapshot/recovery operations while its Diamond actors run. Native DB operators
must not write behind that boundary. There is no old/new schema coexistence or
zero-downtime version-compatibility promise.

## Snapshot and restore

Backup uses `pg_dump`, `pg_restore` and `/usr/bin/gpg`. The passphrase comes from a
separate named environment variable and is passed to GnuPG over stdin. Use at
least 24 bytes, protect its source, and retain it separately from snapshots.
Snapshots contain an encrypted database dump, one streamed encrypted runtime bundle
covering every declared shared-object, Git, delivery, and operations root, and an
encrypted manifest with component-safe paths, modes, plaintext lengths/hashes, and
the exact build contract.
No key or database configuration is written into that manifest. Snapshot directories
are newly created mode 0700; output files are private. The AES256 GnuPG files include
integrity protection. Never decrypt into a public/static/served directory.

1. Remove edge traffic, stop both servers and both workers, and ensure no native
   administrator is mutating application records or shared files.
2. Run the backup command with a new destination:

   ```sh
   diamond project-backup .diamond/release --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME --out /private/backups/new-snapshot --backup-key-env DIAMOND_BACKUP_PASSPHRASE
   ```

3. Independently copy the encrypted snapshot off-host, retain its matching checked
   build, and verify ciphertext checksums at the destination. Scheduling, storage
   retention and actual off-host transfer are operator responsibilities; no off-host
   upload was performed during local checkpoint verification.
4. Restore into a separate empty database and empty canonical 0700 roots for every
   configured shared-object, Git, delivery, and operations binding, with isolated
   environment variables and no production routing:

   ```sh
   diamond project-restore .diamond/release --postgres-env DIAMOND_RESTORE_POSTGRES --runtime-env DIAMOND_RESTORE_RUNTIME --from /private/backups/new-snapshot --backup-key-env DIAMOND_BACKUP_PASSPHRASE
   ```

5. Compare independently recorded records/accounts, Git refs, promoted placement,
   artifacts, Pages files, operations state, and attachment bytes/hashes. Restore
   rewrites fixed Git hook paths for the isolated roots and runs `git fsck` before
   adapter validation.
   Every restored session, API credential and one-use account challenge is revoked;
   passwords/accounts remain for fresh sign-in. Mail spool messages are not restored.
   Jobs are paused in the snapshot itself and remain inert if restoration is
   interrupted after database import. Never expose a partially restored target.
6. After checking destination deduplication/reconciliation, explicitly choose:

   ```sh
   diamond project-recover .diamond/release --postgres-env DIAMOND_RESTORE_POSTGRES --policy resume
   # Or --policy cancel to fence/cancel pending and running jobs before unpausing.
   ```

Recovery requires stopped actors. `resume` allows at-least-once redelivery; it does
not rewrite incompatible handler versions. Start workers only after the chosen
policy and application verification. Wrong keys/tampered ciphertext are rejected
before database import; nonempty database/runtime-root targets are refused. Failed
snapshots may leave a partial new backup directory; absence of a verified encrypted
manifest means it is incomplete. A failed/interrupted snapshot may leave jobs
paused for safety; inspect state and choose recovery explicitly.

This is an offline snapshot: the data-loss window is changes since its recorded
quiesced snapshot, and recovery time includes decryption/import/reconciliation and
operator steps. The [checkpoint evidence](../verification/web_seven_eight_results.md)
records measured local drill durations and observations without promising a
production RTO/RPO. The G4 filesystem-root and promoted-shard drill is recorded in
[`verification/git_scale_recovery_results.md`](../verification/git_scale_recovery_results.md).
Native operator authority and encrypted off-host retention
remain explicit trust/deployment responsibilities.
