# LP8 predefined assembled-candidate scenarios

| ID | Action | Expected observation |
| --- | --- | --- |
| A1 | Build twice from the unchanged candidate and hash compiler, library, contract, JS, map, HTML, and source manifest. | Rebuilds are deterministic and every final observation names the same source/binary/artifact identity. |
| A2 | Run Forge and live-workspace ordinary create/edit/read journeys through a real browser and independently query PostgreSQL; reconnect a fresh client/process. | Visible and persisted state agree before and after reconnect; typed forms/components/actions require no application security workaround. |
| A3 | Exercise deep links, invalid fields, denied identity, uncertain/reconciled action, live event/job, logout/login, and valid recovery. | Each public category is distinct, rejected operations leave no unintended state, stale work cannot cross identity/component boundaries, and valid follow-up succeeds. |
| A4 | Exercise actual Chromium, Firefox, and WebKit at desktop and narrow viewport, plus keyboard focus and generated field/error/status relationships. | Supported engines preserve core semantics and keyboard usability; labels/descriptions/errors/status relationships are exposed. Any unavailable engine/AT is recorded as blocked coverage, not a product pass. |
| A5 | Replay retained LP1--LP7 failures and execute a fresh deterministic corpus of valid and hostile programs through check/run/build/wire/action paths. | Retained failures stay fixed, valid generated programs execute to the independent oracle, hostile cases reject without crash/effect, and final valid recovery passes. |
| A6 | Compile and run a native Rust consumer against the final library API. | Only checked programs/projects execute; value and diagnostic results match the documented contract. |
