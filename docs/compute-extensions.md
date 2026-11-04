# Compute Extensions

Status: draft RFC · Draft v0.2 · November 2026

This document proposes a general, bounded compute-extension surface for
Diamond. It is motivated by applications whose pure computation exceeds the
interpreter's evaluation budgets, but it is not specific to any application.
It is intended to become a draft RFC and, if accepted, be implemented in
slices.

The proposal follows the constraints in [`design.md`](../design.md):

- no ambient authority;
- explicit effects, declared at the module and application boundaries;
- bounded resources and explicit failure;
- "optional WebAssembly for compute" (`design.md` §0.2);
- untrusted native extensions must run behind a process or equivalent sandbox
  boundary (`design.md` §7.5);
- arbitrary native plugins, shell execution, and general-purpose network access
  remain outside the current release (`README.md`, "Boundaries").

Draft v0.2 responds to review feedback on the first draft. The five required
clarifications are listed as invariants in §13: guarantee classes, capability
granularity, WASM instance lifetime, artifact identity, and resource/ABI
bounds.

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
- no ambient authority and no access to host state;
- explicit, checked signatures;
- declared capabilities and observable contracts;
- an explicit and honest statement of what is *enforced* versus *advisory*.

This proposal adds a **compute-extension** surface with two tiers:

1. **Host-registered compute operations.** Trusted Rust functions compiled into
   the embedding host and registered by name. They generalize the existing
   trusted host-operation model (`host.postgres_version`, `host.git_*`, …) to
   embedder-provided pure functions. Their purity and resource limits are
   **cooperative and advisory**; they are not enforceably isolated.
2. **Sandboxed WebAssembly compute modules.** Portable `.wasm` modules declared
   by the project, executed in a deterministic sandbox with no WASI and no host
   I/O. This is the untrusted tier and the only tier eligible for the
   sandbox-qualified profile.

Both tiers share one checked call surface, one export-specific capability
model, one contract format, and one set of bounds.

---

## 2. Non-goals

- **No arbitrary native plugins.** Diamond does not load `.so`/`.dylib`/`.dll`
  files and does not expose `dlopen`, JIT of arbitrary native code, or a
  general FFI bridge.
- **No ambient I/O.** Compute operations receive values and return values. They
  cannot open files, sockets, databases, or subprocesses. Stateful work belongs
  in the existing database/action/job surfaces.
- **No authority laundering.** A compute operation cannot receive a principal,
  scope, secret, or database handle through its arguments. It is a value
  function.
- **No new scheduler.** Compute runs inside the existing admission and request
  deadline model, with a dedicated allowance described in §10.
- **No replacement for the interpreter.** Ordinary application logic stays in
  Diamond. Compute extensions are for the narrow, declared cases above.

---

## 3. Guarantee classes

The two tiers do **not** carry the same guarantees. The distinction is part of
the contract, not an implementation detail.

| Tier | Trust | Purity | Resource isolation | Qualified profile |
| --- | --- | --- | --- | --- |
| Host-registered | Trusted embedder code | Cooperative only; not enforceable | Advisory: deadline checks, `catch_unwind`, size caps. These cannot stop a blocking loop, excessive allocation, process abort, or ambient filesystem/network access. | **Excluded** from the sandbox-qualified profile unless the embedder wraps the operation in a killable isolation boundary (process or equivalent). |
| Sandboxed WASM | Untrusted project artifact | Enforced by no imports plus deterministic configuration | Enforced by the sandbox: fuel/epoch, linear memory, tables, stack, instances, and host-side copy limits. | Eligible once the sandbox is qualified. |

Consequences:

- A host-registered operation is an **embedder extension**, like existing
  native host operations. The embedder is responsible for its behavior.
- Loop checkpoints and `catch_unwind` are **cooperative defenses**. They are
  useful and required, but they are not an isolation boundary and must not be
  described as one.
- If a host operation needs hard guarantees, the embedder must place it behind
  a killable boundary (a sandboxed subprocess or the WASM tier). The manifest
  cannot opt a native operation into the sandboxed profile.
- The qualified profile may include the WASM tier; it must not claim the host
  tier is sandboxed.

---

## 4. Design principles

1. **Declared, not inferred.** The project manifest declares every compute
   export it calls, with argument and result types.
2. **Export-specific capabilities.** A grant names an exact export:
   `Compute<module.export>`. Adding a new export to a module never broadens an
   existing grant.
3. **Pure by contract.** Operations are value functions. The host tier's purity
   is trusted/cooperative; the WASM tier's purity is enforced by the sandbox.
4. **Bounded.** Every call has declared input and output limits, a per-export
   duration limit, and a dedicated concurrency allowance. Outputs are charged
   against the value budget or rejected.
5. **Content-addressed artifacts.** A WASM artifact's identity is the digest
   declared in the manifest, not whatever bytes happen to exist at a path at
   startup.
6. **Observable.** Each export publishes a contract (signature, capability,
   limits, determinism class, artifact digest, sandbox version) in the project
   artifact and emits bounded metrics.
7. **Least authority.** Compute operations cannot be granted I/O capabilities,
   and they cannot appear in shared or browser modules.
8. **Fail closed.** Missing registrations, signature mismatches, digest
   mismatches, sandbox failures, overruns, and panics are explicit errors; they
   never degrade into executing something else.

---

## 5. Call surface

Both tiers use one manifest-declared surface, so application source does not
change when an export moves from host to WASM.

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
  argument types and result type, and inserts
  `Effect::Host("Compute<module.export>")`.
- The runtime dispatches to the registered host operation or the declared WASM
  module. A missing or mismatched registration is `Error::Denied`.
- Each export requires its own capability. `Compute<sim.tick>` does not grant
  `Compute<sim.plan>`, even when both live in the same module.

A static call surface avoids metaprogramming, keeps the checker closed, and
preserves the authority model. Applications that need several operations
declare several exports and grant each one explicitly.

---

## 6. Manifest declaration

```toml
provides = ["Compute<sim.tick>"]

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

For the WASM tier, the module also declares its artifact identity and sandbox
limits:

```toml
provides = ["Compute<path.plan>"]

[compute.path]
kind = "wasm"
path = "compute/path.wasm"
sha256 = "9f2c...e41a"              # required content identity
max_memory_bytes = 16777216
max_table_elements = 100000
max_stack_bytes = 1048576
max_instances = 2
fuel = 10000000

[compute.path.exports.plan]
arguments = ["Int", "Int", "Int"]
result = "Text"
```

The host tier has no `path` or digest: the operation must already be registered
in the embedding binary. At startup, the runtime verifies that every declared
host export exists and that its registered signature matches the manifest. A
declared-but-unregistered operation fails deployment, not first use.

The manifest is the single source of truth for the checker. For WASM, the
`sha256` field is the single source of truth for artifact identity. A mismatch
between the manifest and the registered/loaded artifact is a deployment error.

`provides` must list each export separately. Module-level grants that would
cover unknown future exports are not permitted.

---

## 7. Tier 1: host-registered operations

### 7.1 Rust API (proposal)

```rust
pub struct ComputeExport {
    pub module: &'static str,
    pub export: &'static str,
    pub arguments: Vec<Type>,
    pub result: Type,
    pub capability: &'static str, // "Compute<module.export>"
}

pub trait Compute: Send + Sync {
    fn call(&self, args: &[Value]) -> Result<Value>;
}

pub struct ComputeRegistry { /* ... */ }

impl ComputeRegistry {
    pub fn register(&mut self, spec: ComputeExport, f: impl Compute + 'static);
    pub fn get(&self, module: &str, export: &str) -> Option<&ComputeExport>;
}
```

The registry is supplied by the embedding host and passed into
`compile_project` / `serve_with_auth` (or a builder). The default registry is
empty, so the feature is inert unless an embedder opts in.

### 7.2 Execution

- The checker/runtime boundary already runs RPC interpretation on the bounded
  blocking pool; a host op runs inline there (no nested blocking, no second
  runtime).
- The caller supplies the request deadline; the op checks
  `budget::remaining()` at entry and at coarse checkpoints.
- Panics are caught (`catch_unwind`) and mapped to `Error::Internal`.
- Inputs are validated against the declared types and byte limits; outputs are
  size-checked and charged.
- These are **cooperative** controls. A blocking loop or unbounded allocation
  in trusted host code is outside what this tier can enforce.

### 7.3 Trust model

A host-registered operation is compiled into the host binary. It is at the same
trust level as `host.postgres_*` or `host.git_*`: the **embedder** decides what
exists. The project can only call what the embedder registered and the manifest
declared. This is not an arbitrary native plugin mechanism.

The host tier is therefore:

- **trusted**: the embedder is responsible for correctness and resource use;
- **advisory**: deadline checks, size caps, and panic containment are best-effort;
- **outside the sandbox-qualified profile** unless the embedder supplies a
  killable isolation boundary.

---

## 8. Tier 2: sandboxed WebAssembly modules

### 8.1 Runtime

- Use a maintained WASM runtime (for example `wasmtime`) with:
  - no WASI, no host imports, no filesystem/network;
  - a fixed, versioned value ABI (arguments in linear memory, result out);
  - a memory cap, table cap, stack cap, instance cap, and fuel/epoch limit per
    call;
  - deterministic configuration (no threads, no `memory64`, canonical NaNs,
    no ambient time); integer/fixed-point recommended for authoritative results.
- The module is validated and compiled once; only **validated compiled code**
  is cached. Source bytes are not re-read or re-hashed at call time.

### 8.2 Instance lifetime

- Caching a compiled module is permitted; reusing mutable runtime state is not.
- Each invocation receives a **fresh `Store`/`Instance`**, or an equivalently
  proven reset that reinitializes linear memory and globals.
- A pool of instances is acceptable only if the reset is demonstrated (fresh
  memory, cleared globals, no retained host handles) and covered by tests that
  attempt to observe state across calls.
- The sandbox must not expose shared mutable memory or globals between requests.

### 8.3 Artifact identity

- The manifest declares `sha256` (and may declare `size`) for each WASM
  artifact.
- At startup the loader resolves the path once, opens it with symlink-safe
  semantics inside the project root, reads a bounded number of bytes, verifies
  the size and digest, and compiles/caches from **those verified bytes**.
- The loader must not hash whatever file happens to exist at the path at
  startup and call that identity. The manifest digest is the identity; a
  mismatch is a deployment failure.
- The compiled artifact is tied to the verified digest; changing the digest
  requires a new manifest and a new deployment.

### 8.4 ABI

A small, versioned binary ABI keeps payloads compact and avoids embedding a
JSON parser in the sandbox:

```text
request:  magic u32, version u16, argument count u16,
          repeated { type u8, length u32, bytes }
response: status u8, length u32, bytes
```

Before the ABI is normative it must define:

- the memory export and the allocation protocol (`alloc`/`dealloc`, or a fixed
  input/output region);
- pointer/length ownership and lifetime (who writes, who reads, who frees);
- canonical nested encoding (deterministic field order, length prefixes, no
  unspecified padding);
- explicit endianness and alignment;
- bounds checks **before** any allocation sized from an untrusted length;
- per-call limits for linear memory, tables, stack, instances, and host-side
  copies, not only linear memory.

The ABI version and the sandbox configuration are part of the export contract
and the project artifact.

---

## 9. Checker and runtime integration

| Layer | Change |
| --- | --- |
| Manifest | Add `[compute.<name>]` with `kind`, `path` and `sha256` (WASM), exports, limits |
| Checker | Resolve `host.compute<Reply>(module, export, ...)`, check static names, argument and result types, insert `Effect::Host("Compute<module.export>")` |
| Capability validation | Add `Compute<module.export>` to `capability_effect`; keep host capabilities server-module-only |
| Runtime grants | Carry the `ComputeRegistry` and the verified/compiled WASM modules |
| Interpreter | Dispatch `host.compute` to the registry or sandbox |
| Artifact | Publish compute contracts (module, export, signature, limits, determinism, digest, sandbox version) |
| Deployment | Verify declared host exports exist; load and digest-verify WASM bytes; validate modules before serving |

The call is server-module-only, like the other host operations. Shared and
browser modules cannot call it.

---

## 10. Resource and security model

- **Inputs:** declared per-export byte/count limits; rejected before execution.
- **Outputs:** declared byte limit and value-budget charging.
- **CPU:** the request deadline plus per-export `max_duration_ms`; cooperative
  checks in host code; fuel/epoch enforcement in WASM.
- **Memory:** declared `max_memory_bytes`; bounded host buffers; no allocation
  sized from untrusted lengths without a cap.
- **Concurrency:** a dedicated compute admission allowance (permits and queue)
  separate from ordinary request capacity, so long kernels cannot starve
  normal requests. The allowance is configurable and observable.
- **Isolation:** host ops share the process (trusted, advisory); WASM ops are
  sandboxed (enforced).
- **No I/O:** neither tier receives host handles. Stateful work uses the
  existing database/action surfaces.
- **Panics/traps:** caught and mapped to explicit errors.
- **Observability:** module, export, input/output bytes, duration, fuel/memory
  use, sandbox version, artifact digest, and failure class; never payloads.
- **Kill switch:** a deployment can disable a compute module or the whole
  surface.

---

## 11. Testing and qualification

- **Checker tests:** undeclared module/export, non-static names, wrong arity,
  wrong types, missing export-specific capability, shared/browser misuse.
- **Runtime tests:** missing registration, signature mismatch, output limits,
  deadline overrun, panic, WASM trap, digest mismatch, size mismatch.
- **Determinism tests:** same inputs → same bytes across runs and platforms;
  assert a digest.
- **Sandbox tests:** WASM cannot import `env`/WASI, cannot allocate beyond the
  cap, cannot exceed fuel, and cannot observe state from a previous call.
- **Artifact tests:** contracts appear, digests match, deployment rejects
  mismatch, symlink/path escapes are rejected.
- **Guarantee-class tests:** the host tier is documented and tested as
  advisory; the WASM tier is tested against its enforced limits.
- **Differential tests:** compare a host op against a reference implementation
  during migration.
- **Example:** a small pure module in `examples/` (for example a deterministic
  hash or a tiny path search) exercised by `verification/examples.py`.

---

## 12. Rollout

1. **RFC** (this document): agree on the call surface, manifest shape, guarantee
   classes, and the invariants in §13.
2. **Host tier:** registry, manifest parsing, checker, runtime dispatch,
   export-specific capability, artifact, tests. Explicitly outside the
   sandbox-qualified profile.
3. **WASM tier:** sandbox, ABI, instance lifetime, artifact digest, module
   validation, concurrency allowance, tests.
4. **Qualification:** extend the profile and release evidence for the WASM tier.

Each slice is additive and inert without a declared compute export, so existing
projects and the qualified profile are unchanged.

---

## 13. Required decisions and invariants

These are gates for the implementation slices. They are normative for this
draft.

1. **Guarantee classes.** Host-registered operations are trusted embedder
   extensions. Their purity and resource limits are cooperative/advisory;
   deadline checks and `catch_unwind` cannot stop a blocking loop, excessive
   allocation, process abort, or ambient I/O. The host tier is excluded from
   the sandbox-qualified profile unless the embedder supplies a killable
   isolation boundary. The WASM tier is the enforced, sandbox-qualified path.
2. **Capability granularity.** Capabilities are export-specific:
   `Compute<module.export>`. Adding an export must never broaden an existing
   grant. The checker and artifact must enforce this.
3. **Instance lifetime.** Only validated compiled code may be cached. Each
   invocation gets a fresh `Store`/`Instance` or an equivalently proven reset;
   mutable memory and globals cannot leak between requests.
4. **Artifact identity.** The manifest carries the expected digest for each
   WASM artifact. Startup loads exact bounded, symlink-safe bytes once, verifies
   the digest, and compiles/caches from those bytes. The path is not the
   identity.
5. **Resource isolation and ABI.** Compute has a dedicated concurrency/admission
   allowance separate from ordinary request capacity. The ABI defines memory
   export/allocation, pointer-length ownership, canonical nested encoding,
   bounds checks before allocation, and per-call limits for tables, stack,
   instances, and host-side copies, not only linear memory.

---

## 14. Open questions

- Should the call surface be `host.compute(module, export, ...)` or a
  namespace such as `host.compute.module_export(...)`? Static names favor the
  former.
- Which WASM runtime and deterministic configuration are acceptable for the
  supported profile?
- Should compute results be cacheable by content hash (a pure-function memo)?
- How should large results be streamed or chunked for the host tier?
- What is the exact wire-safe type subset for the WASM ABI, and which encoding
  is canonical?
- What is the default compute admission allowance, and how is it configured per
  deployment?
