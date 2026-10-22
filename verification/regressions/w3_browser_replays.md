# W3 actual-browser regressions

Before receipts are in the adjacent `w3_*` directories. The final observed
after receipt is [web_three_browser_results.json](../web_three_browser_results.json).
It identifies the executable and actual served assets. This is actual Chrome
evidence; checking the generated JavaScript alone is not a replay.

Build with `cargo build --locked`, then run
`python3 verification/web_three.py --serve-fixture /tmp/diamond-w3-replay`.
Open http://127.0.0.1:39171/ and http://127.0.0.1:39171/qualification in Chrome.
Use only these owned loopback fixtures. The fixture provisions synthetic users;
no production data or credentials are needed. Type `stop` afterward.

| Finding | Actual replay and required observation |
| --- | --- |
| `w3_child_props` | Sign in as alice with the fixture password, create an issue with a nonempty title, then Refresh list. One typed card appears, draft equals title, mount count is 1; no `Cannot access n before initialization` error. A read refresh can replay an existing committed row without duplicate writes. |
| `w3_text_nodes` | Reload `/`; the initial account/conditional whitespace must hydrate with `#diamond-app[data-ready="true"]`. Current user and Refresh list remain operable. Adjacent emitted text fragments must agree with HTML's coalesced text nodes. |
| `w3_control_hydration` | Reload with empty account fields. Hydration accepts native mutable control values/attributes and binds them once. The before receipt also records the independent adjacent-text failure, so it does not assign all initial failures to one cause. |
| `w3_text_replace` | On `/qualification`, Run independent corpus. All 184 labeled values, including empty-needle Unicode/empty-input cases, must match the independent expected.json fingerprint. The original three-file project in that directory is a smaller replay. |
| `w3_html_normalization` | Reload `/qualification`. It must hydrate, then both textContent and title on `#normalization` have code points `[13,10,65,13,66,0]`. Before repair the browser dropped literal NUL in text, replaced NUL in attributes and normalized CR; hydration failed. |
| Key path collision | Bump a/nested, Bump a/1/first, Reverse key paths. Counters are independent and retain values; the exact oracle is in `../w3_independent/key_paths/expected.json`. |
| `w3_event_blocks` | `diamond check` the adjacent saved case and the independent event-composition project; multiline Match/For assignments must parse through their dedents. Operate the 15 Composition controls after fixture sign-in and compare the independent action table. |

For delayed responses, Load parent response with `slow-old`; terminal `status`
must report it held. Change Request value, issue a new request, then terminal
`release slow-old`. The newer answer survives. Repeat Load child response with
`props-old`, Change child property, `release props-old`; its old answer is ignored.
Prepare unmount request, Load child response, Mount/unmount child,
`release unmount-old`, then remount: the new instance starts with no answer and
mount count 1. These gates hold completed real responses; they do not synthesize
handler answers. Perform generation-changing actions before the 30-second event
deadline; the proxy releases its hold after 180 seconds as a cleanup bound.

Terminal `fault contract` and `fault structure` each alter only the completed
real HTML response. Reload: both must yield data-ready false and the reload
error. `fault clear` and reload must restore normal hydration. No page state is
mutated using developer evaluation.

Final browser failure controls deliberately emit console errors. The final
normal tracker journey separately records no new warning/error. Source maps are
qualified at function-entry granularity, not as expression-level debugging.

For the navigation failure regression, start the fixture with the extra
`--route-failure-probe` option. At `/`, Prepare route failure then About must
retain `/` and the issue page with a visible error. Recover route failure then
About must navigate and mount once. Back, prepare failure, then Forward must
retain the prior successful route and report the initialization error. The
probe is compiled Diamond with a state-dependent divisor; it is not replacement
JavaScript. The original URL/DOM mismatch is in `w3_navigation_failure/before.json`;
final actual Chrome observations are in `../web_three_navigation_results.json`.
