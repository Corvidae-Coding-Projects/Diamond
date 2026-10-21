# Red-team findings

The campaign is limited by `verification/red_team.md`. Findings are classified
before implementation as required by the repair discipline.

| Finding | Pre-fix evidence | Category | Required repair |
| --- | --- | --- | --- |
| CLI argument panic and terminal control injection through untrusted command, option, and path text | `regressions/red_team_cli_terminal_before.json` contains an exit-101 invalid-UTF-8 panic plus literal ESC and LF bytes emitted by the frozen checkpoint-six binary | A: shared-boundary defect | Parse OS arguments without panic, reject non-UTF-8 explicitly, and route every user-controlled CLI label and path used in status or error output through one terminal-safe renderer. Preserve authored program output as program output. |
| Nested dependency errors bypass per-field terminal sanitization | `regressions/red_team_nested_error_terminal_before.json` contains literal ESC and LF bytes copied by SQLite from a hostile database path | A: shared-boundary defect | Sanitize the completely formatted non-diagnostic error chain at the final stderr boundary, covering dependency and operating-system messages as well as local contexts. |
| Anonymous browser-session exhaustion denies all later safe routes | `regressions/red_team_anonymous_capacity_before.json` shows request 4,097 and a subsequent health request both return 413 | A: shared session-lifecycle defect | Keep the registry bounded while allowing a new anonymous browser session to replace the oldest anonymous session. Never evict an authenticated user session, and rotate a successful login from its exact prior session without needing an extra capacity slot. |
| Account database replacement between metadata validation and SQLite open | `regressions/red_team_account_replacement_before.json` shows `user-add` inserting into a second private regular database renamed over the checked path | A: shared account-file invariant defect | Anchor a no-follow file handle, require private regular single-link metadata, make SQLite reject symlinks, and compare the path's device/inode to the anchor immediately after open and before schema reads or writes. |
| Maximum legal linear call graph exhausts the checker CPU budget | `regressions/red_team_call_graph_before.json` records a 4,095-declaration, 116 KiB source killed at the 10-second CPU limit | A: shared compiler-analysis defect | Replace repeated whole-program snapshots with monotone work-queue propagation over reverse call edges, retaining recursive-cycle and nested-transaction enforcement. |
| Corrupted account hash creates a username-dependent login response oracle | `regressions/red_team_corrupt_hash_oracle_before.json` shows the affected existing user returns 400 while a nonexistent user returns 401 | A: shared account-provider defect | Validate every bounded account row and its exact Argon2id parameters when opening the provider, before accepting requests, so per-request login failure remains uniform. |
| CLI reads arbitrarily beyond the compiler's source ceiling | `regressions/red_team_source_read_before.json` shows a rejected 64 MiB sparse file driving about 69 MiB child RSS before the 1 MiB limit error | A: shared input-boundary defect | Publish one source-byte ceiling and cap the CLI read to ceiling plus one byte before UTF-8 decoding and compilation. |
| FIFO source path blocks before any compiler bound | `regressions/red_team_source_fifo_before.json` shows `diamond check` still blocked with no output at the one-second deadline | A: shared input-boundary defect | Open Unix source paths nonblocking, require the opened object to be a regular file, then apply the bounded reader. |
| FIFO account path blocks authentication-provider startup | `regressions/red_team_account_fifo_before.json` shows server startup still blocked with no output at the one-second deadline | A: shared account-file invariant defect | Add nonblocking semantics to the anchored no-follow account-file open so existing regular-file validation can reject special files immediately. |

No category B local-instance workaround or category C suppression is accepted for
these defects.

## Repair status

All nine rows were repaired as Category A shared-boundary changes. The exact
exploits and their sibling clusters pass on final executable
`51b5829a10ebad5500ca78b83d8f39a4a61d97c685a54646d97c083cdd70fa3f`.
Two complete fresh-seed saturation passes found no new unique failure after the
last repair; see `red_team_pass_one.json`, `red_team_pass_two.json`, and
`red_team_results.md`.
