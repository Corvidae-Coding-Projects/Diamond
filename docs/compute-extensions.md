# Compute Extensions

Status: proposal · Draft v0.1 · November 2026

This document proposes a general, bounded compute-extension surface for
Diamond. It is motivated by applications whose pure computation exceeds the
interpreter's evaluation budgets, but it is not specific to any application.
It is intended to be discussed and, if accepted, implemented in slices.

The proposal follows the constraints in [`design.md`](../design.md):

- no ambient authority;
- explicit effects, declared at the module and application boundaries;
- bounded resources and explicit failure;
- "optional WebAssembly for compute" (`design.md` §0.2);
- untrusted native extensions must run behind a process or equivalent sandbox
  boundary (`design.md` §7.5);
- arbitrary native plugins, shell execution, and general-purpose network access
  remain outside the current release (`README.md`, "Boundaries").

---

## 1. Summary

Diamond's interpreter is intentionally bounded: 100,000 fuel, a managed-value
work budget, 1,000-item lists, a call-depth limit, and a request deadline. That
is the right default for application logic, and it should stay the default.

Some legitimate workloads are **pure functions over values** that are too large
or too iterative for the interpreter: simulation ticks, pathfinding over large
grids, deterministic procedural generation, compression, hashing, and numeric
kernels. They need:

- more compute per call than the interpreter's fuel allows;
- no I/O, no ambient authority, and no access to host state;
- deterministic results;
- explicit, checked signatures;
- declared capabilities and observable contracts.

This proposal adds a **compute extension** surface with two tiers:

1. **Host-registered compute operations.** Trusted Rust functions compiled into
   the embedding host and registered by name. They generalize the existing
   trusted host-operation model (`host.postgres_version`, `host.git_*`, …) to
   embedder-provided pure functions.
2. **Sandboxed WebAssembly compute modules.** Portable `.wasm` modules declared
   by the project, executed in a deterministic sandbox with no WASI and no host
   I/O. This is the untrusted tier and the long-term portable path.

Both tiers share one checked call surface, one capability model, one contract
format, and one set of bounds.

---

## 2. Non-goals

- **No arbitrary native plugins.** Diamond does not load `.so`/`.dylib`/`.dll`
  files and does not expose `dlopen`, JIT of arbitrary native code, or a
  general FFI bridge.
- **No ambient I/O.** Compute operations receive values and return values. They
  cannot open files, sockets, databases, or subprocesses. Stateful work belongs
  in the existing database/action/job surfaces.
- **No authority laundering.** A compute operation cannot receive a principal,
  scope, secret, or database handle through its arguments. It is a pure value
  function.
- **No new scheduler.** Compute runs inside the existing admission and request
  deadline model, on the existing bounded blocking pool.
- **No replacement for the interpreter.** Ordinary application logic stays in
  Diamond. Compute extensions are for the narrow, declared cases above.

---

## 3. Design principles

1. **Declared, not inferred.** The project manifest declares every compute
   operation it calls, with argument and result types. Unknown operations fail
   checking.
2. **Capability-gated.** Each operation requires `Compute<name>` (host tier) or
   `Compute<module>` (WASM tier) in `provides`. A project without the grant
   cannot call it.
3. **Pure and deterministic.** Operations are pure functions of their
   arguments. The host tier must not read clocks, randomness, environment, or
   external state; the WASM tier runs without imports and with deterministic
   configuration. Floating-point should be avoided; integer/fixed-point is
   recommended for authoritative results.
4. **Bounded.** Every call has declared input and output limits, and runs under
   the request deadline with periodic deadline checks. Outputs are charged
   against the existing value budget or rejected.
5. **Observable.** Each operation publishes a contract (signature, capability,
   limits, determinism class, failure modes) in the project artifact and emits
   bounded metrics.
6. **Least authority.** Compute operations cannot be granted I/O capabilities,
   and they cannot appear in shared or browser modules.
7. **Fail closed.** Missing registrations, signature mismatches, sandbox
   failures, overruns, and panics are explicit errors; they never degrade into
   executing something else.

---

## 4. Call surface

Both tiers use one manifest-declared surface, so application source does not
change when an operation moves from host to WASM.

```diamond
export fn tick(state: Text, days: Int) -> Result<Text, DataError>:
  host.compute("sim", "tick", state, days)
```

`host.compute` is a single checked call:

```text
host.compute<Reply>(module: static Text, export: static Text,
                     arguments...) -> Result<Reply, DataError>
```

- `module` and `export` are **static names**, like HTTP destinations and Git
  bindings. They cannot be computed from user input.
- The checker resolves `(module, export)` from the manifest, checks the
  argument types and result type, and inserts `Effect::Host("Compute<module>")`.
- The runtime dispatches to the registered host operation or the declared WASM
  module. A missing or mismatched registration is `Error::Denied`.

A static call surface avoids metaprogramming, keeps the checker closed, and
preserves the authority model. Applications that need several operations
declare several exports.

---

## 5. Manifest declaration

```toml
[compute.sim]
kind = "host"                       # or "wasm"

[compute.sim.exports.tick]
arguments = ["Text", "Int"]
result = "Text"
deterministic = true
max_input_bytes = 262144
max_output_bytes = 1048576
max_duration_ms = 2000
```

For the WASM tier, the module also declares its artifact:

```toml
[compute.path]
kind = "wasm"
path = "compute/path.wasm"
max_memory_bytes = 16777216
fuel = 10000000

[compute.path.exports.plan]
arguments = ["Int", "Int", "Int"]
result = "Text"
```

The host tier has no `path`: the operation must already be registered in the
embedding binary. At startup, the runtime verifies that every declared host
operation exists and that its registered signature matches the manifest. A
declared-but-unregistered operation fails deployment, not first use.

The manifest is the single source of truth for the checker. The host registry
is the single source of truth for the host implementation. A mismatch is a
deployment error.

---

## 6. Tier 1: host-registered operations

### 6.1 Rust API (proposal)

```rust
pub struct ComputeOperation {
    pub name: &'static str,
    pub arguments: Vec<Type>,
    pub result: Type,
    pub capability: &'static str, // "Compute<name>"
}

pub trait Compute: Send + Sync {
    fn call(&self, args: &[Value]) -> Result<Value>;
}

pub struct ComputeRegistry { /* ... */ }

impl ComputeRegistry {
    pub fn register(&mut self, op: ComputeOperation, f: impl Compute + 'static);
    pub fn get(&self, name: &str) -> Option<&ComputeOperation>;
}
```

The registry is supplied by the embedding host and passed into
`compile_project` / `serve_with_auth` (or a builder). The default registry is
empty, so the feature is inert unless an embedder opts in.

### 6.2 Execution

- The checker/runtime boundary already runs RPC interpretation on the bounded
  blocking pool; a host op runs inline there (no nested blocking, no second
  runtime).
- The caller supplies the request deadline; the op checks
  `budget::remaining()` at entry and at coarse checkpoints.
- Panics are caught (`catch_unwind`) and mapped to `Error::Internal`.
- Inputs are validated against the declared types and byte limits; outputs are
  size-checked and charged.

### 6.3 Trust model

A host-registered operation is compiled into the host binary. It is at the same
trust level as `host.postgres_*` or `host.git_*`: the **embedder** decides what
exists. The project can only call what the embedder registered and the manifest
declared. This is not an arbitrary native plugin mechanism.

---

## 7. Tier 2: sandboxed WebAssembly modules

### 7.1 Runtime

- Use a maintained WASM runtime (for example `wasmtime`) with:
  - no WASI, no host imports, no filesystem/network;
  - a fixed, versioned value ABI (arguments in linear memory, result out);
  - a memory cap and fuel/epoch limit per call;
  - deterministic configuration (no threads, no `memory64`, canonical NaNs,
    no ambient time); integer/fixed-point recommended for authoritative results.
- The module is loaded once per deployment, validated, and cached. A failed
  validation rejects the deployment.
- The sandbox boundary is the WASM instance, not a native shared library.

### 7.2 ABI

A small, versioned binary ABI keeps payloads compact and avoids embedding a
JSON parser in the sandbox:

```text
request:  magic u32, version u16, argument count u16,
          repeated { type u8, length u32, bytes }
response: status u8, length u32, bytes
```

Types are the wire-safe subset already used by RPC (Int, Bool, Text, Result,
records encoded deterministically). The ABI version is part of the contract.

### 7.3 Authority

A WASM module is data, not authority. It receives only the argument bytes and
returns only result bytes. It cannot import `env`, open files, or reach the
network. The `Compute<module>` capability gates the call.

---

## 8. Checker and runtime integration

| Layer | Change |
| --- | --- |
| Manifest | Add `[compute.<name>]` with `kind`, `path` (WASM), exports, limits |
| Checker | Resolve `host.compute<Reply>(module, export, ...)`, check static names, argument and result types, insert `Effect::Host("Compute<module>")` |
| Capability validation | Add `Compute<...>` to `capability_effect`; keep host capabilities server-module-only |
| Runtime grants | Carry the `ComputeRegistry` and the loaded WASM modules |
| Interpreter | Dispatch `host.compute` to the registry or sandbox |
| Artifact | Publish compute contracts (module, export, signature, limits, determinism, sandbox version) |
| Deployment | Verify declared host ops exist and signatures match; validate WASM modules and hashes at startup |

The call is server-module-only, like the other host operations. Shared and
browser modules cannot call it.

---

## 9. Resource and security model

- **Inputs:** declared per-export byte/count limits; rejected before execution.
- **Outputs:** declared byte limit and value-budget charging.
- **CPU:** the request deadline plus per-export `max_duration_ms`; periodic
  checks in host code; fuel/epoch limits in WASM.
- **Memory:** declared `max_memory_bytes`; bounded host buffers; no unbounded
  allocation from input sizes.
- **Isolation:** host ops share the process (trusted); WASM ops are sandboxed.
- **No I/O:** neither tier receives host handles. Stateful work uses the
  existing database/action surfaces.
- **Panics/traps:** caught and mapped to explicit errors.
- **Observability:** op name, export, input/output bytes, duration, fuel/memory
  use, and failure class; never payloads.
- **Kill switch:** a deployment can disable a compute module or the whole
  surface.

---

## 10. Testing and qualification

- **Checker tests:** undeclared module/export, non-static names, wrong arity,
  wrong types, missing capability, shared/browser misuse.
- **Runtime tests:** missing registration, signature mismatch, output limits,
  deadline overrun, panic, WASM trap, invalid module hash.
- **Determinism tests:** same inputs → same bytes across runs and platforms;
  assert a digest.
- **Sandbox tests:** WASM cannot import `env`/WASI, cannot allocate beyond the
  cap, and cannot exceed fuel.
- **Differential tests:** compare a host op against a reference implementation
  during migration.
- **Artifact tests:** contracts appear, hashes match, deployment rejects
  mismatches.
- **Example:** a small pure module in `examples/` (for example a deterministic
  hash or a tiny path search) exercised by `verification/examples.py`.

---

## 11. Rollout

1. **Proposal** (this document): agree on the call surface, manifest shape,
   trust tiers, and non-goals.
2. **Host tier:** registry, manifest parsing, checker, runtime dispatch,
   capability, artifact, tests. No WASM dependency.
3. **WASM tier:** sandbox, ABI, module validation, artifact hashes, tests.
4. **Qualification:** extend the profile and release evidence.

Each slice is additive and inert without a declared compute module, so existing
projects and the qualified profile are unchanged.

---

## 12. Open questions

- Should the call surface be `host.compute(module, export, ...)` or a
  namespace such as `host.compute.module_export(...)`? Static names favor the
  former.
- Should the host tier be considered part of the qualified profile, or an
  embedder-only extension outside it?
- Is `wasmtime` the right runtime, and which deterministic configuration is
  acceptable for the supported profile?
- Should compute results be cacheable by content hash (a pure-function memo)?
- How should large results be streamed or chunked for the host tier?
- What is the exact wire-safe type subset for the WASM ABI?
