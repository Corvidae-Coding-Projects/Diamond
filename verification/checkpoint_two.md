# Checkpoint-two observable contract

Derived from the accepted implementation plan, design sections 8–9/12/24, and
the checkpoint-one limits. These expectations precede execution.

- Provision two distinct local accounts through the actual CLI. Passwords must
  be salted Argon2id hashes in a private, separate account database; duplicate
  provisioning must leave an existing account unchanged.
- An anonymous visitor cannot execute private application handlers. The visitor
  can open the runtime sign-in page and receives a session-bound CSRF form.
- Incorrect credentials, invalid login CSRF, and hostile origins cannot create
  an authenticated session. Successful login rotates the session token.
- A signed-in user receives the Diamond-authored post form with an automatically
  generated hidden CSRF field. No handler authors or verifies a CSRF token.
- Posting follows the checked form/parser/repository path, derives authorship
  from the verified session, commits to SQLite, and redirects to the generated
  typed detail URL. The actual browser must follow and show the submitted text.
- Hostile text stays text. Another user and an anonymous visitor cannot retrieve
  the unpublished post. Invalid fields, forged author, absent/stale/cross-session
  CSRF, and hostile origins must leave stored rows unchanged.
- Logout revokes the exact session. Old cookies and forms cannot continue to
  authorize private mutations; GET cannot log out a session.
- Restart the server against the same two databases. Old sessions are invalid;
  signing in again retrieves the original post with the original authorship.
- Replay checkpoint-one behavior and the native embedding consumer. Complete
  build, formatting, strict clippy, doctest, and documentation checks.

This pass does not add general queries, list rendering, production hosting,
concurrent serving, account registration/reset, or durable session storage.
