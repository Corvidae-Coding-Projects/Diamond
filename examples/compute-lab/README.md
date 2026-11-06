# ComputeLab

The smallest project that declares and calls host-tier compute exports. It is
checked, built, and reloaded by `verification/examples.py`, so a manifest or
checker change that breaks the compute surface fails the verification run.

## What this project shows

- `[compute.sim]` declares a host module. Host modules carry no `path` and no
  digest: the kernel must already be registered in the embedding binary.
- Each export declares its exact signature and its own capability:
  `Compute<sim.tick>` does not grant `Compute<sim.plan>`.
- Signatures use the bounded wire-safe subset: `Int`, `Bool`, `Text`,
  `Decimal`, and nested `List<T>` / `Option<T>`.
- `deterministic = true` is required, and each export declares cooperative
  limits: `max_input_bytes`, `max_output_bytes`, and `max_duration_ms`.
- Server code calls one static surface:
  `host.compute<Reply>(module, export, arguments...) -> Result<Reply, DataError>`.
- `[rpc] tick = "server.tick"` binds the same function to RPC, so the
  export-specific capability and the checker's host-operation allowlist are
  exercised through the normal operation contract.

Only server modules may call it. Shared and browser modules are rejected by the
checker, and the capability must appear in both the module and application
`provides` lists.

## Registering the kernels

Diamond never loads native code, and there is no plugin mechanism. Kernels are
ordinary Rust functions compiled into the embedding binary and registered on the
explicit runtime grants before serving:

```rust
let mut grants = diamond::RuntimeGrants::default();
grants.register_compute("sim", "tick", &["Text", "Int"], "Text", Tick)?;
grants.register_compute("sim", "plan", &["Int", "Int"], "List<Int>", Plan)?;
project.serve_with_runtime(address, origin, configuration, mailbox, grants)?;
```

A declared export that is not registered fails when the server starts, and a
registered signature that differs from the manifest fails with it, so a mismatch
is a deployment error rather than a first-use failure. Kernels receive a
`ComputeContext` (module, export, remaining deadline) and return a classified
`ComputeError`, so failures map onto `DataError` classes instead of raw strings.
They must be pure and deterministic, must not perform I/O, and must not retain
state between calls; stateful work belongs to the database, job, and action
surfaces. `CheckedProject::validate_compute` performs the startup check and
`CheckedProject::evaluate_with` runs the same entry in-process.

See [`examples/compute_embedding.rs`](../compute_embedding.rs) for a complete
registration and evaluation loop.

## Check and build it

```sh
target/debug/diamond check examples/compute-lab/diamond.toml
target/debug/diamond build examples/compute-lab/diamond.toml \
  --out .diamond/compute-lab
```

Build output must be a new directory. The [compute extensions
document](../../docs/compute-extensions.md) defines the call surface, manifest
shape, guarantee classes, and the boundaries between the trusted host tier and
the sandboxed WebAssembly tier.
