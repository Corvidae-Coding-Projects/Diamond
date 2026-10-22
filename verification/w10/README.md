# Replaying W10

Run from the repository root with Rust/Cargo 1.96.0, Python 3, PostgreSQL tools,
Git, GnuPG, Chrome and the checksum-verified Caddy 2.11.7 executable used by W9.
The scripts create isolated temporary PostgreSQL clusters and directories. They do
not deploy infrastructure. Fetch the current official RustSec database before the
offline dependency receipt.

```sh
cargo build --locked
cp target/debug/diamond /tmp/diamond-w10-final
cp target/debug/libdiamond.rlib /tmp/libdiamond-w10-final.rlib

python3 verification/web_three_independent.py \
  --binary /tmp/diamond-w10-final --library target/debug/libdiamond.rlib \
  --report verification/w10/w3_results.json
python3 verification/web_five_independent.py \
  --binary /tmp/diamond-w10-final --library target/debug/libdiamond.rlib \
  --dependencies target/debug/deps --report verification/w10/w5_results.json
python3 verification/w6_independent/run.py \
  --binary /tmp/diamond-w10-final --library target/debug/libdiamond.rlib \
  --dependencies target/debug/deps --output-dir verification/w10/w6 \
  --report verification/w10/w6/results.json
python3 verification/w7_w8_independent/run.py \
  --binary /tmp/diamond-w10-final --caddy /tmp/diamond-caddy-2.11.7/caddy \
  --output-dir verification/w10/w7_w8 \
  --report verification/w10/w7_w8_results.json
python3 verification/w10/w9_campaign.py \
  --binary /tmp/diamond-w10-final --output-dir verification/w10/w9 \
  --report verification/w10/w9_campaign_results.json
python3 verification/w9/capacity.py \
  --binary /tmp/diamond-w10-final --duration 1805 \
  --report verification/w10/capacity_results.json
python3 verification/w10/accessibility.py \
  --binary /tmp/diamond-w10-final \
  --report verification/w10/accessibility_results.json
env CARGO_HOME=/tmp/diamond-w10-cargo python3 verification/w10/dependency_review.py \
  --cargo-home /tmp/diamond-w10-cargo \
  --report verification/w10/dependency_results.json
```

Run the workspace and catalog fixtures from
`verification/web_seven_eight_browser.py` against the same frozen executable,
operate their full journeys in real Chrome, and save DOM/network plus independent
PostgreSQL/receiver observations as `workspace_browser_results.json` and
`catalog_browser_results.json`. Browser actions cannot be replaced by repository
tests.

After the report/design text is final and the inputs will no longer change, run:

```sh
python3 verification/checkpoint_six.py --full \
  --output-dir verification/w10/checkpoint_six \
  --report verification/w10/checkpoint_six_results.json
python3 verification/w10/coverage.py \
  --binary /tmp/diamond-w10-final --library /tmp/libdiamond-w10-final.rlib \
  --require-evidence --report verification/w10/coverage_results.json
```

The frozen executable and `target/debug/diamond` must have the same SHA-256 after
the last build. A changed source, embedded scaffold, dependency lock, verifier or
artifact requires replaying the affected gate and the final checkpoint seal.
