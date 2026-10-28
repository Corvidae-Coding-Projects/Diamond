# Rails comparison fixture

This is the deliberately small Rails 8.1.4 API application used by the parent
[Diamond and Rails benchmark](../README.md). Do not run it as an independent
application: `../run.py` owns its temporary PostgreSQL schema, synthetic secret,
seed data, process lifecycle, workload, and persisted-state oracle.

The fixture mirrors the Diamond app's bearer authentication, workspace
authorization, durable admission accounting, typed JSON wire shape, cursor
encoding, and CRUD behavior. Dependency versions are locked in `Gemfile.lock`;
`../prepare.sh` builds native gems with the pinned official Ruby image.
