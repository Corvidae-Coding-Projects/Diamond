# Accounts workspace

The relational tracker with invitation enrollment, verification/recovery,
administrator disablement, account-wide logout and scoped API credentials.
Browser controls are authored in Diamond. It shares PostgreSQL and an explicitly
granted private development mail directory across application servers.

Follow [the accounts/API guide](../../docs/accounts-and-apis.md) for setup,
private delivery, exact codecs, grants and development boundaries. The example
requires `project-admin-add` for its initial administrator; public registration
cannot set administrative status.

For password change, put the **new** password in the top Password field and the
existing password in Current password, then click Change password. Verification
and recovery codes go in the code field, never a URL. After an identity change,
sign in again and reload the desired workspace. Read credentials created by the
UI last one hour and are restricted to the workspace scope JSON you entered.
