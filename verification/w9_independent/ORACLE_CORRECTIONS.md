# Independent checker corrections

## Readiness error classification during PostgreSQL loss

The first `edge_campaign.py` run required a 5xx readiness response while the
owned PostgreSQL process was stopped. The actual observation was `live=200`,
`ready=403`, protected RPC `503`, and the preexisting WebSocket closed. The W8
contract requires readiness to become non-ready; it does not prescribe a 5xx
classification for every dependency probe. The shared health implementation
maps its internal failed readiness probe through a redacted denial response.

The oracle was corrected to require any non-200 readiness result while retaining
the stronger protected-RPC 5xx and socket-closure checks. This is a verifier
expectation defect, not a product security finding. No product behavior changed
between the two baseline runs.

## Storage harness omitted a declared runtime binding

The first `storage_loss.py` invocation did not configure the controlled
`inventory` receiver required by the fixture's declared `Http<inventory>`
capability. Diamond rejected `serve` during startup with access denied, before
any storage operation ran. The harness was corrected to provide the isolated
loopback receiver and then rerun from fresh state. This is a fixture setup defect,
not a product finding.
