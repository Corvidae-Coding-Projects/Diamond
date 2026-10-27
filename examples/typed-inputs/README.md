# Typed inputs example

This app extends the small language spine with canonical queries, every scalar
URL kind, editable invalid drafts, generated field errors, and typed Text/Int/
Bool/ID/enum/optional form submission. See the [guide](../../docs/typed-inputs.md).

```sh
cargo build --locked
target/debug/diamond check examples/typed-inputs/diamond.toml
target/debug/diamond build examples/typed-inputs/diamond.toml \
  --out .diamond/typed-inputs-build
```

With PostgreSQL tools installed (`initdb`, `pg_ctl`, `psql`), start the isolated
real-service demo from the repository root:

```sh
python3 verification/lp3_inputs.py --serve --report .diamond/typed-inputs-demo.json
```

Open http://127.0.0.1:39171/. The driver prints the generated synthetic password.
Sign in as `alice`, load alpha issues, and open alpha-b. Its link includes page 2
and Unicode search text. Try `-` or overflow in Estimate, an empty title, and an
overlong title, then correct them. The ordinary edits remain in the form; one
successful save updates the intended row. Try parent `beta` for a distinct server
denial. **Default view**, **Page two**, reload, and history select typed queries.
**Scalar routes** and **Optional form** demonstrate the remaining scalar codecs.

Type `observe` in the driver's terminal for independent SQL. Type `stop` to stop
only owned processes and remove its disposable cluster. The driver seeds the
initial accounts/rows and performs the backend campaign before serving; this is
a qualification/demo fixture, not a production enrollment or deployment command.

Source: [shared schemas/helpers](shared.dia), [policies/action](server.dia),
[browser components](ui.dia), [explicit bindings](diamond.toml).
