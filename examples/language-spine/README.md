# Language spine example

This small Diamond app connects a named issue route, typed title form,
row-specific event, child completion output, and protected database action.
Read [the syntax guide](../../docs/language-spine.md), then inspect:

- [shared.dia](shared.dia): nominal schemas, shared validation, typed outcomes;
- [server.dia](server.dia): membership policies and transactional rename;
- [ui.dia](ui.dia): typed route/form/event/output and action state;
- [diamond.toml](diamond.toml): explicit effects, bindings, storage, accounts, API.

Check and build without a database:

```sh
cargo build --locked
target/debug/diamond check examples/language-spine/diamond.toml
target/debug/diamond build examples/language-spine/diamond.toml \
  --out .diamond/language-spine-build
```

For a disposable runnable demonstration, install PostgreSQL client/server tools
(`initdb`, `pg_ctl`, `psql`) and run from the repository root:

```sh
python3 verification/lp2_spine.py --serve \
  --report .diamond/language-spine-demo.json
```

The driver starts an isolated PostgreSQL cluster, initializes three synthetic
accounts and issues, checks the two-server workflow, then serves the actual
Diamond app at http://127.0.0.1:39171/. Sign in as `alice` with the generated test
password printed by the driver. Click **Load alpha issues**, **Edit alpha-b**,
enter a title, and save. The parent receives a typed completion and the saved
revision increases once. **Open alpha-b** follows the typed route; reload reads
the saved value. `reject` deliberately attempts an update then rejects, proving
the request transaction rolls back. Reader and Bob retain separate policies.

Type `observe` in the driver's terminal for independent SQL readback. Type `stop`
to stop only its owned processes and remove its disposable database. This helper
is a qualification/demo fixture; it is not an application deployment command.
The example has no public account enrollment or issue-creation UI. Its initial
rows are seeded by the fixture so the language slice stays small.
