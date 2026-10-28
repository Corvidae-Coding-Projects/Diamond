# Execution profiles, security sinks, and limits

This is part of the [Diamond 0.20.0 contract](language-specification.md). The
matrix describes checked source and runtime boundaries, not deployment readiness.
`S` means single-file `.dia`; `PS` means project server; `SH` means project shared;
`B` means project browser. “Native” means trusted Rust embedding code, outside
untrusted Diamond source. Current delta evidence and limits of review are in the
[LP5 evidence](../verification/lp5/); earlier receipts remain historical.

## Capability and semantics matrix

| Surface | S | PS | SH | B | Enforcement/source |
| --- | --- | --- | --- | --- | --- |
| Checked scalars, records, enums, finite lists/Option/Result, pure utilities | Yes | Yes | Yes | Managed subset | [checker](../src/checker.rs), [standard utilities](../src/standard.rs), [browser lowerer](../src/project/lower.rs) |
| Modules/import/export | No | Server/shared imports | Shared imports only | Browser/shared imports | [project resolution](../src/project/resolve.rs), [manifest checking](../src/project/mod.rs) |
| Mutable local variables/closures/user generics | No | No | No | No; component state is separate | [parser](../src/parser.rs), checker/lowerer |
| Checked nominal ID construction | Literal construction; typed route/form inputs | Alias constructor with runtime validation | Subject to consumer target | Literal construction; typed route/wire inputs | checker `call`, [value validation](../src/value.rs), runtime `__validate` |
| Server Html, sanitized Markdown, checked Url/Response | Yes | Server evaluation where allowed | Cannot export a server-only value into browser usage | No | [security classification](../src/security.rs), [templates](../src/template.rs), lowerer |
| Resource operations and policies | SQLite/native grants | PostgreSQL project adapters; explicit native grants | No | No direct access; checked event RPC only | [authority](../src/authority.rs), [policy](../src/policy.rs), [relational](../src/relational/mod.rs) |
| Explicit transactions | SQLite | Join request transaction | No | No; RPC result is separate from local state | [evaluator](../src/eval.rs), [project storage](../src/project/storage.rs), [relational store](../src/relational/mod.rs) |
| Typed named routes and forms | Existing `route_url`/strict URL-encoded scalar forms | Shared schemas feed checked codecs/public shells | Exported scalar path/query and form schemas | Canonical typed href/outlets with default/optional queries; editable drafts and associated field errors | [reactive schemas](../src/project/schemas.rs), [form checking](../src/project/lower/forms.rs), [scalar codecs](../src/scalar_input.rs), [browser inputs](../src/project/inputs.js) |
| Browser components and CSS/static assets | No | Supplies public build/server dispatch | Supplies pure code/types | Yes | [browser emitter](../src/project/browser.rs), [assets](../src/project/assets.rs) |
| Parameterized UI handlers/component outputs | No reactive API | No UI execution | Safe imported payload types/functions | Exact positional arguments; declared parent handlers; bounded postcommit output delivery | lower/interactions, [browser interactions](../src/project/interactions.js) |
| Managed fields, submit, status | No reactive API | Pure native initial markup | Scalar form schemas | Generated IDs/labels/help/errors, first invalid focus, native keyboard controls, bounded status | [component contract](components.md), lower/controls, render/controls, browser controls |
| Server actions / typed outcomes | Local Action constructors/matching, no dispatcher | Explicit database-only action bindings, account/policy checks | Safe Action payload types/functions | `action.NAME`, automatic pending outcome, typed terminal outcome | [actions](actions.md), [dispatcher](../src/project/actions.rs) |
| Runtime principals/grants/scopes as application values | No | No | No | No | Native authority is separate from [managed types](../src/value.rs) |
| External HTTP/Git/mail/files/jobs/workflows | No project adapter surface | Named, effect-checked, explicitly configured adapters | No | Only supported dedicated calls/event RPC | [integration checker](../src/checker/integrations.rs), project adapter modules |
| Generic wire-safe exports | No project RPC | Explicit manifest exports, recursively safe types | Pure type/function imports | Event-only remote consumption | [RPC](../src/project/rpc.rs), [wire](../src/project/wire.rs), lowerer asynchronous checks |
| Authenticated request SSR/streaming SSR | Legacy server routes under their own auth contract | No private reactive request SSR | No | Pure public build-time/named-path shells + full hydration | [render](../src/project/render.rs), runtime hydration |
| Durable transactional mutation receipts | No dispatcher | CRUD-only action + encrypted receipt commit atomically; current-policy result reconciliation | Pure types/helpers only | Server-reference discovery, explicit outcome check/ack, no browser persistence or mutation replay | [reliable actions](actions.md), receipts/footprint/durable dispatcher |
| Formatter/LSP/event-handler source maps | `diamond fmt`; compiler LSP | Same source tools | Same source tools | Formatter/LSP plus reachable function/component/event maps | [tooling](tooling.md), [browser emitter](../src/project/browser.rs) |

Shared source is not automatically legal for every target. A pure shared
declaration used by a server may mention a server-only type; making it
browser-reachable must pass browser managed-type/lowering checks. Module target
and reachability both matter. Source effects, manifest budgets, and actual host
grants are independent checks.

## Restricted-value sinks

All entries apply recursively to containing records, enums, lists, Options, and
Results. `type_security` in [security.rs](../src/security.rs) computes
`contains_secret`, `browser_managed`, `client_wire_safe`, and component-message
safety from the complete
checked type. Unknown definitions or analysis deeper than 32 are unsafe. Checked
storage schemas, display whitelists, and individual adapter argument checks impose
additional restrictions; “wire-safe” does not mean “valid in every sink.”

| Value/type family | Browser state / pure execution | Ordinary RPC | HTML/text sink | Application persistence | Allowed dedicated consumer |
| --- | --- | --- | --- | --- | --- |
| Int, Bool, Text, bounded Text, nominal Id | Yes | Yes, exact declared shape | Checked scalar display; explicit conversions where required | Only supported declared storage fields | Checked forms/routes/native codecs; IDs confer no access |
| Decimal, Timestamp, Duration | Yes | Yes, tagged units | Explicit formatting/conversion | Supported declared storage schema | Pure numeric/time utilities |
| Unit and finite safe records/enums/lists/Option/Result | Yes, if every contained type is managed | Yes, if recursively wire-safe; top-level legacy Result has special transport behavior | No implicit compound display; render safe fields/branches | Only supported storage schema, not arbitrary object serialization | Typed constructors, matching, APIs/query plans |
| DataError | Managed local error value | Not a standalone safe field; legacy Result has a redacted codec | Explicit local `error_message`; public detail sanitized | Not a general persistent field | Result matching; fixed public failure codes |
| Action<T,E> | Yes with recursively managed/safe payloads | No generic transport, including nested shapes | Match and display safe fields | No | Dedicated action terminal codec; safe component output/event payloads |
| Secret<binding> | No | No, including project main/public result | No formatting, equality, or rendering | No application record/serialized job payload | Matching server adapter only; declarative injection preferred |
| Blob<binding> | Supported descriptor state/dedicated file controls | No generic RPC | No interpolation/equality/text conversion | No ordinary fields/serialized payload | Nominal file methods and runtime-owned reservation/upload/download boundary, with current resource authorization |
| Stream<channel> | Supported scoped descriptor/watch | No generic RPC | No interpolation/equality/text conversion | No ordinary fields/serialized payload | Declared watch/event boundary, matching channel/payload/current parent policy |
| Html | No | No | Server checked template/sanitized Markdown only | Not an ordinary application field | Server typed HTML renderer |
| Url | No | No | Checked server link/redirect slot | Not an ordinary application field | `route_url` and validated internal static URLs |
| Response | No | No | Not an interpolated value | No | Server response boundary |
| Principal, Scope, HostGrants, repository/host authority | Not managed Diamond values | No | No | No managed serialization | Trusted native caller supplies verified borrowed scope/grants |

`Blob` is a checked source/browser descriptor, not a general native `ValueKind`
containing arbitrary bytes. Files remain adapter-owned. `Stream` construction
validates its descriptor; channel existence/payload and authorization are checked
again at the watch consumer. A matching nominal tag does not establish permission.

The special legacy Result codec sanitizes its implicit DataError while checking
the success type recursively. `Result<Secret<...>, DataError>` is still unsafe.
Declared application Result checks both success and error types recursively.
The inferred `Never`/`Success` cases provide no authority or inhabitable bypass.

Relevant consuming boundaries are [checker](../src/checker.rs),
[integration checker](../src/checker/integrations.rs),
[project server/main checks](../src/project/mod.rs),
[wire codecs](../src/project/wire.rs),
[browser lowering](../src/project/lower.rs),
[files](../src/project/files.rs), [events](../src/project/events.rs),
[jobs](../src/project/jobs.rs), [workflows](../src/project/workflows.rs),
and [secret adapters](../src/project/secrets.rs). New constructs must reuse the
complete-type classification and validate again at their native consumer.

## Resource budgets

These are current boundary limits, not promises about exact resident memory.
One MiB is 1,048,576 bytes. The narrowest applicable boundary wins: a 1 MiB text
value does not imply a 1 MiB ordinary RPC body is accepted.

| Boundary | Current limit / failure rule | Owning source |
| --- | --- | --- |
| Individual source / project manifest | 1 MiB UTF-8 | [lexer](../src/lexer.rs), project artifact input |
| Project inputs | 8 MiB aggregate; 64 modules; 4,096 declarations/components | [project](../src/project/mod.rs) |
| Lexing / parsing | 200,000-token check after each source line (final Dedent/End tokens appended separately); indentation stack ceiling 64 including base; expression recursion 64; type recursion limit 32 | [lexer](../src/lexer.rs), [parser](../src/parser.rs) |
| Project component definitions / RPC+action declarations / style+asset declarations | 64 / 128 combined / 64 | project validation/lowerer/assets |
| One component | 128 total properties+state+derived; 128 events; 256 steps/event | parser `component` |
| Pure execution | 100,000 fuel; call-depth ceiling 64 | [evaluator](../src/eval.rs), [browser runtime](../src/project/runtime.js) |
| Text / lists | 1 MiB UTF-8 per Text; 1,000 list items | value/evaluator/standard/wire/browser |
| Native managed values | Depth ceiling 64; 4 MiB weighted single value | [value](../src/value.rs) |
| Browser managed values / wire / security analysis | Depth ceiling 32; browser 4 MiB weighted single value | runtime/wire/security |
| Materialization work | 64 MiB weighted cumulative work in an evaluation accounting phase | evaluator/runtime; not allocator measurement |
| Rendering | 1,024 component instances; 8,192 node/work units including conservative managed-control expansion; 8,192 UTF-8 bytes per identity path; server page 8 MiB | render/runtime/controls |
| Managed accessible text | Nonblank labels: 1–256 Unicode scalars; status: at most 512; generated ID prefixes reserved | render/controls, browser controls |
| Markdown | 256 KiB input; 1 MiB rendered output | [Markdown](../src/rich_text.rs) |
| Build output | 8 MiB browser JS; 16 MiB individual serialized artifacts | browser/artifact |
| Browser asynchronous event / output chain | 30 seconds; 32 calls (two per action) / 64 messages and 30 seconds | runtime dispatch/interactions |
| Named reactive URL | 2,048 UTF-8 bytes; 8 path parameters; 16 segments; 16 queries; literal namespace prefix; route Text maximum 256 scalars | schemas/routes |
| Schema form draft | 16 KiB UTF-8 per Text draft; native maxlength at most 8,192 UTF-16 units or lower generated field bound; no browser draft persistence | inputs/runtime/lower forms |
| Reactive schemas | 64 route schemas; 64 form schemas; 32 fields/form | schemas |
| Single-attempt action proof | Five-minute lifetime; 16 unused/user, 4,096 total/application across nodes | actions/tickets; shared PostgreSQL control state |
| Transactional action receipt | 24-hour lifetime; 16 unacknowledged and 128 total/user/application; 4,096/application; 64 protected CRUD facts; 256 KiB plaintext | actions/receipts + footprint; shared PostgreSQL control state |
| Ordinary connected control/RPC body | 16 KiB with 3-second body deadline | [connected transport](../src/project/connected.rs) |
| Legacy URL-encoded forms | 64 KiB body; 16 KiB UTF-8 per scalar field; additional declared field bounds | [forms](../src/forms.rs) / shared scalar input |
| Private file streaming | At most 8 MiB/file, or lower declared binding; 30-second stream deadline | files/connected transport |
| Relational operation page | Limit 1–100; offset at most 1,000; declared indexed paths | [relational configuration/query](../src/relational/config.rs) |
| Legacy SQLite selection | Limit 1–100; offset at most 10,000 | checker/policy |
| Relational pool / connected execution | Defaults: 4 connections; 16 admitted + 128 waiting requests; 4 blocking slots; checked `[runtime]` overrides | [runtime capacity](deployment.md#bounded-runtime-capacity), [pool](../src/relational/pool.rs) |
| Relational timing | Acquire 5s; statement 5s; lock 2s; request connection lifetime 10s | pool/[host runtime](../src/host.rs) |

Fuel counts and accounting reset points differ across evaluators; browser render
and asynchronous work use separate phases. The limits do not establish identical
instruction costs, one aggregate browser-event memory meter, or process isolation.
Some native operations allocate before their postconstruction weight check;
LP6 includes allocation/cleanup stress rather than treating this as allocator
isolation. Dedicated adapters also impose their own declared quotas/deadlines;
consult their checked contracts and focused guides for adapter-specific limits.

## Differences and drift register

| ID | Classification | Current decision / next owner |
| --- | --- | --- |
| D01 | Intentional profile difference | LP3 completes finite scalar reactive path/query/forms with checked defaults/Option. Legacy URL-encoded forms reuse native scalar codecs but retain their separate transport/CSRF boundary. |
| D02 | Documentation drift corrected in LP2 | Dynamic text-to-ID construction is project-server-only. Browser/single-file constructors require literals; checked route/wire inputs carry typed IDs. Nominality and consuming policy remain required. |
| D03 | Intentional profile difference | Server-only Html/Url/Response and native depth 64 do not become browser/wire values (depth 32). Do not widen the client boundary to make a shared helper compile. |
| D04 | Known current limitation | Direct minimum-Int literal token rejects; expression construction works. LP2 preserves this explicit limitation rather than claiming full signed literal syntax. |
| D05 | Documentation drift, corrected entry points | W1 guide's 64 fields/events and old rendering behavior are historical. Current parser permits 128 aggregate fields and 128 events. W1/W2/W3 now identify their snapshot status and link here. |
| D06 | Documentation drift, corrected entry points | W3's language 0.9.0/wire 2 and “W4–W10 remain” text describe that checkpoint, not current 0.20.0/wire 7. Its evidence remains historical. |
| D07 | Documentation drift, corrected | Single-file guide omitted Decimal/Timestamp/Duration and blurred named record vs positional function arguments; updated to current behavior. |
| D08 | Corrected in LP7 | The formatter and bounded stdio LSP use the real parser/checker. Browser maps cover reachable pure functions, components, and event/action handlers; unreachable server/private source stays absent. |
| D09 | Intentional boundary | Local-state preservation is not server or full-DOM transactionality. Transactional actions now reconcile bounded PostgreSQL CRUD through sealed receipts; external effects and `replay = "never"` do not gain a distributed exactly-once promise. |
| D10 | Documentation drift, corrected | Shared static internal URL validator permits simple fragments for links and redirects; reactive route declarations still forbid fragment/query paths. |
| D11 | Documentation drift, corrected | The redacted wire failure codes also include `RetryableAbort`; W3's current-reference section now lists it. |
| D12 | Qualified bounded profile | LP6 reran remote-adapter, multi-server authority, cancellation, and prolonged cleanup campaigns. LP8 replayed them on one final candidate and ran actual Chromium, Firefox, and WebKit journeys. Actual speech-output AT remained unavailable; Chrome's accessibility tree and generated relationships were exercised and the gap is explicit in the LP8 ledger. |

LP2 reproduced and fixed cross-site document navigation rejection and transparent
browser POST retry at the new action boundary. See its ledger for replay evidence.
These bounded checks are not a whole-codebase security review or proof that no
defects remain.
