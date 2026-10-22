# Account-specific component state regression

In the actual tracker, Alice created/edited an issue and then signed in as Bob.
The server correctly changed identity, but the component retained Alice's prior
title, details, selected ID and output. `before.json` preserves this observation
and the original component/executable hashes.

The Diamond sign-in handler now performs one remote login, then clears private
issue fields, listing/output and password. Successful sign-out clears those
fields as well. The framework does not guess which application state is private.

Replay in the real browser fixture:

1. Sign in as Alice, create/edit an issue, and save its ID.
2. Sign in as Bob in the same component. Prior title/details/ID/listing must be
   empty, and output must contain the neutral signed-in prompt.
3. Enter Alice's ID and click **Read issue**. It must be denied without revealing
   Alice's content. Sign back in as Alice and confirm normal read recovery.
4. Sign out and confirm the private fields are cleared.

The final exact DOM observations are retained in
`../../web_two_browser_results.json`. Later-event failures and lost mutation
delivery can leave server effects committed; the UI explicitly instructs users
to refresh rather than promising rollback.
