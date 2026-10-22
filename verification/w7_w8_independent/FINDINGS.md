# Reproduced findings and final replay

The independent agent changed only this verification folder. The implementing
agent made the product fixes. Every final gate uses executable `3d58bff2...`;
see `COVERAGE.md` and the full JSON fingerprints.

| Finding | Actual initial observation | Product correction and final evidence |
| --- | --- | --- |
| Declared typed job syntax was not parsed | Real `diamond check` failed `host.job_submit<Text>(...)` with D010 at its first comma | Generic-call parser recognizes job/event calls; valid fixture and typed rejection cases pass in `checker_results.json`; original `parser_initial_failure.json` |
| Legal short nominal parent IDs could not subscribe | Actual authenticated A/B upgrades both returned 403 with ordinary `issue-red-one` fixture ID | Endpoint accepts its documented bounded URL-safe ID alphabet; full `jobs_events.py --short-ids` passes; original `first_runtime_observation.json` |
| Retry query's native integer parameter was inferred as PostgreSQL float8 | Actual workers exited 1 with redacted PostgreSQL error on retry path, preventing jobs from completing | SQL retry interval parameter explicitly typed integer; bounded real three-attempt receiver failures and normal conflict retries pass; original `workers_initial_failure.json` |
| Cookie work lost unrestricted scope semantics on persistence | With retry handling repaired, actual cookie jobs became dead with `denied` before any receiver call | Persist/reconstruct optional restrictions as null versus actual map; cookie jobs execute, and scoped token remains attenuated after parent moves; original `workers_denied_failure.json`, final `jobs_events_results.json` |
| UI source and handler implementation were coupled to durable queue schema | Implementing agent identified overly broad current schema/version sealing while reviewing distributed behavior | Queue schema, executable handler version, and browser contract are distinct; actual process replacement and old/new queued-handler behavior pass `job_versions_results.json`. No separate old-binary reproduction of this last finding was retained, so it is not claimed as independently reproduced before patch |

The first combined HTTP+write fixture was correctly rejected under retained W6
semantics and was split into external and transactional handler profiles. An
exactly-one-attempt healthy-job expectation was also corrected to the declared
bounded at-least-once contract; the observed duplicate is preserved in
`normal_retry_observation.json`. Initial restore checker mistakes and the
fixture CA's missing key-usage extension are verification defects, not product
security findings. Details are in `ORACLE_CORRECTIONS.md`.

The tested Caddy configuration preserves `Host` using
`{http.request.hostport}` because `{http.request.host}` omits an explicit port.
That correction is grounded in official Caddy documentation and verified in the
successful TLS campaign. The first TLS fixture failed certificate validation
before HTTP and therefore does not establish an observed Host-header rejection.
