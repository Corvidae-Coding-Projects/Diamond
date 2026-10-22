# W5 identity reply superseded by another UI event

Requirement: a competing UI event must not cancel the client side of a pending
identity transition and preserve old private application state after the server
has revoked its session. Unknown identity outcomes must clear private client state.

Reproduced in actual Chrome with executable
`ebc09281ed8de79dc08d49b14e0abd505c8f2ef298277946c72ee0a43329a047`:

1. Run `python3 verification/web_five_browser_fixture.py --gated` in an
   interactive terminal. It starts real disposable PostgreSQL and Diamond on
   backend127.0.0.1:39174 with public origin http://127.0.0.1:39171.
2. Start `python3 verification/web_five_browser_gate.py`. It forwards real
   requests/responses unchanged; `arm` holds one completed logout-all reply.
3. Sign in, create a workspace/project/issue and load its private page.
4. Send `arm` to the gate. Click **End all sessions**. The real backend responds
   200 and deletes its sessions; the gate holds those actual bytes.
5. While the UI displays Working, click **First page** (or edit a bound input).
6. Release the reply using `release` on the gate. Observe actual UI and PG rows.

Initial result: the competing event aborted the client's logout fetch. A new
read correctly received an authentication denial, but the private issue, signed
in state and workspace ID remained in the DOM. PostgreSQL independently showed
zero sessions. The gate observed a real completed200 logout reply before holding
it. No authentication or application responses were fabricated.

The regression must reject competing event/input/navigation changes during an
identity transition, finish the identity handler after release, and remove the
private cache. Also truncate a held completed reply (`drop`) to exercise an unknown
outcome: private state must clear and a visible bounded error require fresh
sign-in. Reauthenticate and read persisted authorized data to verify recovery.
Record the final executable, observations and clean owned-service shutdown in
`verification/web_five_browser_results.json`.

`close` drops every response byte; qualification observed Chrome repeat that
transport request and receive401 for the revoked session. That path must also
clear private state. Diamond has no application retry loop; this is not an
exactly-once HTTP guarantee. Send `stop` to both fixture terminals after replay.
