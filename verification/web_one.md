# W1 acceptance contract

This is the observable contract for the first general-web expansion checkpoint.
It was written before implementation and does not claim later release criteria.

- A bounded TOML project manifest names server/browser/shared modules, explicit
  imports, exports, module effect budgets, and an application effect budget.
  Project checking opens no database and starts no listener. It rejects cycles,
  ambiguous imports, missing/private exports, invalid targets, root escapes,
  oversized/special source files, and browser access to server modules/authority.
- A real multi-file example uses a shared scalar function from a Diamond browser
  component and from its server entry. The first component subset has typed
  scalar state, named click events, atomic assignments, and typed HTML text.
  Clicking its actual button changes the displayed value. Hostile text remains
  text. Generated arithmetic preserves checked signed-64-bit integer behavior.
- A build produces a checked server-program artifact, hashed browser module,
  page, typed module/operation contract and input/artifact hashes. Repeated builds
  have identical contents. Only browser-reachable code enters the browser asset.
  Loading an artifact verifies its versions, contents and source contract again;
  artifacts cannot manufacture a checked program or grant host access.
- Registered host operations declare arguments, result, effect, output/deadline
  limits, failure and transaction/cancellation behavior. PostgreSQL provides
  read-only version/parameter-round-trip operations in W1; application CRUD,
  schema migrations and shared sessions remain later work. A real isolated
  PostgreSQL server must execute the operation. Both source budgets and explicit
  runtime host grants are required. No arbitrary SQL or process execution is
  exposed. The initial no-TLS connection profile is restricted to Unix sockets.
- The project server uses a maintained HTTP stack, serves compiled assets, and
  handles concurrent requests with bounded evaluation capacity. It remains a
  loopback development server. Mutations/RPC/login are not implied. A slow
  incomplete connection must not stop an unrelated client from loading the page.
- Replay meaningful allowed/denied cases through the real CLI/library/server,
  verify source rejection and absence of database effects, operate a browser,
  inspect PostgreSQL results independently, and preserve failures. Existing
  examples and checkpoint-six/red-team compatibility campaigns remain valid.

Scope: typed scalar functions/components are the initial project-language subset;
the legacy single-file language and SQLite runtime retain their full current
surface. Project CRUD, general component lifecycle/hydration, production serving,
shared accounts, full language lowering and the rest of W2–W10 are not W1 claims.
