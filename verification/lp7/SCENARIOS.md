# LP7 predefined observable scenarios

These expectations were written before LP7 implementation and are independent of
the tool outputs later observed.

| ID | Action | Expected observation |
| --- | --- | --- |
| F1 | Format a valid deliberately uneven program containing comments, escapes, Unicode, and HTML; format the result again. | First output parses/checks with the same location-independent syntax, comment/string/template contents are preserved, second output is byte-identical. |
| F2 | Run `fmt --check` before and after formatting, then format a malformed file. | Unformatted valid input returns nonzero without writing; formatted input passes; malformed input returns its compiler diagnostic and remains byte-identical. |
| D1 | Check invalid single-file and project-module programs with Unicode before the error. | Stable code, exact module/file line and Unicode-scalar column, source caret, and useful remedy are present; no source secret value appears. |
| E1 | Initialize an LSP client, open a valid document, request hover/definition/references/completion, then send a malformed changed version. | Capabilities are advertised; symbol/type/location results refer to the current compiler-parsed document; only the new version receives an accurate diagnostic. |
| E2 | Send an oversized or malformed LSP message and cancel/close a document. | Work fails within the declared bound, no stale diagnostic is published after close, and the server exits coherently. |
| B1 | Trigger a real browser event arithmetic failure. | Visible safe error names the reachable `Component.event`; emitted map contains that event/component location and no server-only canary/private source. |
| C1 | Check every current example and inspect current documentation links/claims. | Examples use supported 0.20.0 syntax and check successfully; current guides name the canonical specification and implemented tooling. |
