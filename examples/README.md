# Example progression

Run these examples in order when learning the interpreted MVP:

1. `hello.dia` — annotated functions, text concatenation, and a pure `main`.
   Run with `diamond run examples/hello.dia`.
2. `language.dia` — enums, payload matching, Result/Option recovery, lists, and
   bounded iteration. It needs no host authority.
3. `web.dia` — public/private routes, typed paths, forms, and escaped HTML. Run
   with `diamond run examples/web.dia`; private routes deny access without auth.
4. `storage.dia` — a policy-controlled SQLite resource and pure CLI evaluation.
   Supply `--database PATH`; the source declaration alone grants no access.
5. `message_board.dia` — the complete browser journey: local accounts, sessions,
   typed forms, CRUD policies, selections, transactions, editing, publishing,
   deletion, and redirects. Follow the
   [README walkthrough](../README.md#try-the-authenticated-message-board).
6. `embedding.rs` — a native Rust consumer of compilation, evaluation, sessions,
   CSRF binding, revocation, and expiry. Run with
   `cargo run --locked --example embedding`.
7. `foundations/diamond.toml` — a three-module W1 project with a shared scalar
   function, a generated reactive browser component, and explicitly granted
   read-only PostgreSQL host calls. Follow the [project guide](../docs/projects.md).
8. `project_embedding.rs` — a real native consumer of project checking, artifact
   builds/loading, and explicit host grants. Run with
   `cargo run --locked --example project_embedding`; set
   `DIAMOND_EXAMPLE_POSTGRES` only for an explicit local PostgreSQL grant.
9. `workspace/diamond.toml` — a W2 tracker with Diamond-owned browser inputs,
   explicit generated RPC, PostgreSQL CRUD and ownership policies, and shared
   sessions across two servers. Follow the [connected guide](../docs/connected-apps.md).
10. `connected_embedding.rs` — explicit PostgreSQL initialization and account
    provisioning through the public Rust API. Supply `DIAMOND_EXAMPLE_POSTGRES`,
    `DIAMOND_EXAMPLE_USER` and `DIAMOND_EXAMPLE_PASSWORD` for a disposable database
    and run `cargo run --locked --example connected_embedding`.

Use `diamond check FILE` first when reading or changing any `.dia` example. It
does not open a database, start a listener, or execute `main`.
