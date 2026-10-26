# Declarative secrets

Diamond binds deployment secrets to the native adapter that consumes them. New
application code does not request a secret, retain a handle, pass it through
helpers, or remember to redact it.

Declare the consumer, kind, and exact named audience:

```toml
[secrets.inventory_bearer]
adapter = "http"
binding = "inventory"
kind = "bearer"
audience = "inventory"
```

The authenticated HTTP operation then contains only application data:

```diamond
export fn lookup(path: Text) -> Result<Text, DataError>:
  host.http_get<Text>("inventory", path)

export fn publish(path: Text, body: Text) -> Result<Text, DataError>:
  host.http_post<Text, Text>("inventory", path, body)
```

Omitting the declaration makes these automatic-authentication signatures fail at
compile time. The declaration must match a static `Http<inventory>` capability.
At server and worker startup it must match an `inventory` runtime destination with
a `credential_env`. The runtime reads that environment variable into zeroizing
memory, creates an opaque generation, and injects it only as the adapter's Bearer
credential. Neither the environment-variable name nor value enters source or a
build artifact.

OAuth clients use the same audit model:

```toml
[secrets.github_oauth_client]
adapter = "oauth"
binding = "github"
kind = "oauth_client"
audience = "github"
```

An OAuth declaration must name a provider in `[identity.oauth]` and a matching
runtime client. The supported pairs are deliberately closed: `http`/`bearer` and
`oauth`/`oauth_client`. An adapter/binding may have exactly one declaration, and
audience must equal the binding. This prevents a credential configured for one
destination from being reused for another.

`contract.json` contains the declaration's logical name, adapter, binding, kind,
and audience so operators can audit required secrets. It excludes deployment
paths and environment names. Missing environment values and runtime mismatches
fail with a generic redacted configuration error. Restart servers and workers to
capture a rotated value; application source and artifacts do not change.

`Secret<"name">` remains an opaque internal type for the checked low-level HTTP
signature. It has no text conversion, equality, logging, persistence, browser, or
generic wire representation. New code should use declarative injection above.
