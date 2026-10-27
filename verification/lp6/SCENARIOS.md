# LP6 predefined adversarial scenarios

These expectations were written before the LP6 campaign. They refine the LP1
LP6 table without depending on implementation internals discovered by a test.
All fixtures are disposable and all generated campaigns record their seed.

## A. Compiler and sink confinement (AC-2)

| ID | Scenario | Expected observation |
| --- | --- | --- |
| A01 | Put `Secret`, `Blob`, `Stream`, and authorization-bearing values directly in route parameters, form fields, component properties/outputs, event arguments, action inputs/results, durable receipts, rendered values, and public functions. | Checking fails with a bounded diagnostic and no artifact, public bytes, receipt, or mutation. |
| A02 | Repeat A01 through records, enums, `List`, `Option`, and `Result`, including multiple wrapper levels and an otherwise valid sibling field. | Recursive restricted-value analysis rejects every unsafe container; no shallow-type bypass exists. |
| A03 | Forge record/enum/handle tags in raw wire values, swap nominal user/workspace/issue IDs, add/duplicate/omit fields, use unknown variants, and send malformed UTF-8/JSON. | The authoritative codec or typed consumer rejects before application effects; no protected bytes or panic. |
| A04 | Hide database/native/action effects behind one and several helpers, shadow functions/types/fields with similar names, return a restricted value through a nominal-looking helper, and call an action through an undeclared raw RPC/API alias. | Transitive effects/grants and canonical declaration identity remain enforced; raw aliases cannot bypass the checked dispatcher. |
| A05 | Corrupt or structurally alter persisted action bindings, envelopes, outcomes, access footprints, tags, resource names, and parent IDs. | Authentication/shape/binding checks fail closed as a stable public category; no panic, mutation replay, or protected disclosure. |

## B. Current authority and delayed work (AC-8)

| ID | Scenario | Expected observation |
| --- | --- | --- |
| B01 | Commit an action, then revoke membership, disable the account, revoke/attenuate the API credential, log out, or replace browser identity before receipt status/result read. | Every later consumer rechecks current authority and denies protected status/result data. |
| B02 | Synchronize parent reassignment/deletion/recreation against action execution and result read. | The documented transaction order determines the result; an old receipt or same textual ID never authorizes a replacement resource. |
| B03 | Hold a component output, RPC response, watch event, and delayed job across unmount and logout/login replacement. | Stale work cannot update the replacement component or identity, and protected payloads are discarded. |
| B04 | Begin under a broad API credential, attenuate/revoke it before consumption, then retry with the old opaque identity from cookie and token profiles. | Current endpoint, principal, epoch, restrictions, parent, and policy binding all remain necessary; the identifier alone grants nothing. |
| B05 | Reload with an already-authenticated session and run the root mount path. | Identity establishment cannot strand the component half-mounted; private work starts only in the fresh epoch and the usable application renders once. |

## C. Shared semantics and bounded failures (AC-9)

| ID | Scenario | Expected observation |
| --- | --- | --- |
| C01 | Compare negative integer division/remainder, overflow, Unicode byte/scalar behavior, equality, short circuiting, enums, `Option`, `Result`, records, lists, validation, and error categories in Rust and generated browser execution. | Both profiles match the independently computed expected value/category for supported common semantics. |
| C02 | Exercise native managed-value depth at 64/65, browser/wire/security depth at 32/33, value weight below/above 4 MiB, cumulative copy/work below/above 64 MiB, and event fuel/deadline. | Each profile accepts/rejects at its documented boundary; above-limit work fails explicitly before uncontrolled expansion and a small follow-up succeeds. |
| C03 | Render below/at/above 8,192 nodes, identity length 8,192 bytes, and the 8 MiB rendered/project byte boundary in server and browser paths. | Matching bounded rejection occurs without partial state replacement; a small render immediately after succeeds. |
| C04 | Send form/action/request bodies below/at/above their declared byte/field bounds, receipt footprints around 64 facts, sealed receipt sizes around 256 KiB, and pending capacities around 16/128/4,096. | Over-limit work is rejected before mutation or expensive unbounded allocation; confirmed abort/recovery categories remain consistent. |
| C05 | Saturate 16 connected requests, 8 Git transports, 32 sockets, the bounded PostgreSQL pool, and slow/failing dependencies; cancel at request, database, and response phases. | Excess work receives explicit overload/failure; permits/leases/connections return; valid follow-up work succeeds. |

## D. Prolonged resource ownership (AC-9)

| ID | Scenario | Expected observation |
| --- | --- | --- |
| D01 | Run at least 1,000 deterministic create/update/reorder/navigate/unmount operations with cancellations and periodic invalid inputs. | No monotonic unbounded Diamond-frame/task/subscription growth; final visible and persisted state matches the oracle. |
| D02 | Run at least 100 connect/reconnect/logout/login/watch cycles, including failed handshakes and delayed messages. | Owned browser sockets/subscriptions close on disposal and identity change; server socket permits recover. |
| D03 | Run repeated action prepare/execute/status/ack, failed dependency, and pool-contention cycles while sampling the Diamond PID, native child PIDs, descriptors, RSS, and PostgreSQL sessions. | Diamond resources settle within a recorded bounded envelope after warm-up, child use is reported separately, and final valid SQL/HTTP operations succeed. |
| D04 | Stop the connected server during owned work and restart against the same isolated state. | Graceful shutdown bounds waits, releases pool/sockets/tasks, preserves only durable committed outcomes, and restart serves valid work. |

## Required replay and evidence

The final candidate replays LP2 compiler/spine, LP3 compiler/input, LP4 compiler/
component/example, and LP5 compiler/action campaigns that overlap changed code.
The LP6 ledger records source and binary hashes, exact commands, observations,
minimized before-fix cases, final replays, resource samples, child-process
separation, and explicitly unexercised boundaries.
