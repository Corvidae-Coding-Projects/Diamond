# Diamond MVP implementation passes

This records the accepted breadth-first plan from this project's conversation.
It is the local interpreted MVP plan, separate from the larger delivery phases
in the language design. Detailed observations are in `CHECKPOINTS.md`.

The next phase has a separate [general web application expansion plan](.design/general-web-apps.md)
with checkpoints W1–W10. These six completed MVP checkpoints retain their original scope.

| Checkpoint | Scope and acceptance | State |
| --- | --- | --- |
| One | Initial compiler/checker/interpreter, authority/policies, SQLite, HTTP/forms/sessions, CLI, and example. The entire project builds; unsupported features fail explicitly. | Complete |
| Two | Connect source through checking, authorized SQLite, HTTP, and the browser. Create/read a post, restart, and retrieve it. | Complete |
| Three | Fill out functions, records, basic variants/matching/results, typed routes/forms/validation/redirects, auth lifecycle, CRUD policies, selections, transactions, and ordinary error handling. The complete message-board journey is written in Diamond. | Complete |
| Four | Review enforcement across every supported path: transitive effects, authority scope, operation policies, update pre/post-state, transaction uncertainty, read-only GET, and restricted-value sinks. Legitimate programs work; prohibited ones fail at the intended boundary. | Complete |
| Five | Exercise varied real CLI/browser/SQLite programs, malformed input, hostile text, unauthorized IDs, expiry, failed transactions, cancellation, and recovery. Preserve failures; every advertised MVP feature has positive and negative evidence. | Complete |
| Six | Finish diagnostic spans/wording, syntax consistency, parser edge cases, startup, docs, installation, organization, measured performance, and final quality checks. A new user can install and understand the example. | Complete |

Security interfaces are present from the first pass and required paths enforce
them as features are connected. Later enforcement and hardening passes do not
make an earlier prototype production-ready. Verification accompanies each pass;
the fifth pass broadens it across the advertised MVP.
