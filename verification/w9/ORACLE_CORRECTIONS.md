# Primary W9 verifier corrections

## Artifact fixture had no declared public API

The first `artifact_boundaries.py` run required `/api/v1/_contract` to return
200. Its fixture deliberately contains RPC operations but no `[http_api]`
declaration, so Diamond correctly returned 404. The relevant boundary is that a
nonexistent public API stays nonexistent and that neither the build contract nor
the planted server-only canary is served.

The expectation was corrected to require 404. Before and after the correction,
all guessed source/build paths returned 404, the undeclared hidden RPC returned
404, the private canary appeared only in `server.json`, and the password, CSRF
value and session token were absent from logs and browser artifacts. No product
code changed for this correction.

The independent campaign's two setup/expectation corrections are retained in
[`../w9_independent/ORACLE_CORRECTIONS.md`](../w9_independent/ORACLE_CORRECTIONS.md).

## Installation verifier retained the W7/W8 CLI label

The sealed original-MVP replay installed Diamond 0.15.0 successfully and all
behavioral installation cases passed, but `startup_installation.py` still required
the W7/W8 version/help label. The installed binary correctly printed the current
W9 label from `src/main.rs`.

The verifier now derives the version from `Cargo.toml` as before and requires the
current W9 profile text. The [failed 20-case receipt](mvp_regression/checkpoint_six_startup_installation_stale_oracle.json)
retains the two string-only mismatches; no product code changed for this correction.
