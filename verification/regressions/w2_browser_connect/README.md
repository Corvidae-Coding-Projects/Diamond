# Connected-page fetch policy regression

Actual Chrome sign-in displayed an interrupted-request error. The connected
application inherited the asset-only W1 Content-Security-Policy with
`default-src 'none'` and no `connect-src`, blocking its generated fetch calls.
`before.json` retains the candidate hash, action, result and header.

Connected responses now add `connect-src 'self'` while retaining restrictive
script/default policy. The W1 asset-only profile is unchanged. Both the real
browser journey and independent HTTP header checks pass on the final candidate.

Replay:

1. Start `python3 verification/web_two.py --serve-fixture .diamond/csp-replay`.
2. Open its printed origin, enter the fixture's Alice credentials and click
   **Sign in**. The signed-in name must become Alice and the password must clear.
3. Create an issue and inspect its exact readback and owned listing.

`../../web_two.py` asserts the actual connected HTML CSP. The observed browser
journey is in `../../web_two_browser_results.json`; an HTTP assertion alone is
not a substitute for that journey.
