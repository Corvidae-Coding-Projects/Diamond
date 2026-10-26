# Declarative mail

Diamond mail is a transactional, policy-aware output primitive. Application code
chooses a declared template, an existing account username, a plain-text body, and
an idempotency key. It cannot construct headers, supply an address, open SMTP, or
read delivery credentials.

Declare the capability and binding:

```toml
provides = ["Mail<document_mail>", "DbRead<Documents>", "DbWrite<Documents>"]

[mail]
profile = "mail-v1"

[mail.bindings.document_mail]
resource = "Documents"
max_attempts = 3

[mail.bindings.document_mail.templates.notice]
subject = "Document notification"
```

The server submits and controls mail with fixed host operations:

```diamond
export fn notify(parent: Text, recipient: Text, body: Text, key: Text) -> Result<Text, DataError>:
  host.mail_submit("document_mail", parent, recipient, "notice", body, key)

export fn status(id: Text) -> Result<Text, DataError>:
  host.mail_status("document_mail", id)

export fn cancel(id: Text) -> Result<Text, DataError>:
  host.mail_cancel("document_mail", id)

export fn suppress(value: Bool) -> Result<Text, DataError>:
  host.mail_suppress("document_mail", value)
```

`recipient` is an account username. At delivery, the runtime resolves that
account's current verified address and rechecks that the recipient may read the
parent resource. It also rechecks the submitting account, its write policy, any
original API restrictions, and the recipient's current suppression setting.
Possessing a mail ID grants no authority; only the submitting principal may read
or cancel it while still authorized for the parent.

Bodies are UTF-8 plain text, at most 16 KiB. NUL, CR, and controls other than LF
and TAB are rejected. Subjects are static manifest text. Consequently strings
such as `Bcc: ...` or `<script>` are message content, never a header or markup.
Dynamic HTML and arbitrary links are not part of this profile.

The initial adapter writes immutable JSON envelopes to a private deployment-owned
directory. Create an empty canonical mode-0700 directory, inject its absolute
path and the deployment sender address, then bind it explicitly:

```sh
export DIAMOND_RUNTIME='{
  "http": {},
  "mail": {
    "document_mail": {
      "root": "/srv/diamond/private-mail/document-mail",
      "from": "noreply@example.invalid"
    }
  }
}'

diamond project-mail-init diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
diamond project-work diamond.toml \
  --postgres-env DIAMOND_POSTGRES --runtime-env DIAMOND_RUNTIME
```

Initialization writes a mode-0600 opaque store marker and binds its identity to
the application and mail binding in PostgreSQL. A missing, copied, symlinked,
world-readable, or replaced root fails closed. Each delivered file is mode 0600,
named by the runtime-generated delivery ID, and byte-verified on replay.

Submission commits with application database changes. A repeated identical
submission by the same owner returns the same ID. Reusing its key with changed
parent, recipient, template, body, code version, or API scope returns a conflict.
Workers use leases and fences, retry bounded failures with the same immutable ID,
and expose only `pending`, `running`, `delivered`, `dead`, `cancelled`, or
`suppressed` plus a redacted outcome. This is at-least-once processing with an
idempotent adapter, not a claim of distributed exactly-once delivery.

The runtime stores at most 10,000 outbox rows per application, permits 1..8
configured attempts, and bounds a store to 20,000 entries. Operators must archive
terminal records deliberately; silent retention cleanup would break deduplication.
