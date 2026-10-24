# Diamond Forge G5 product completion

Date: 2026-10-23

G5 is implemented in `examples/git-browser` and the bounded native adapters.
The retained machine-readable receipts are:

- `git_delivery_redteam_results.json`: 152 of 152 delivery, product, and mixed-stream checks;
- `git_scale_recovery_redteam_results.json`: 108 of 108 scale, search, routing,
  and recovery checks;
- `git_application_redteam_results.json`: 184 of 184 collaboration checks;
- `git_policy_ssh_redteam_results.json`: 196 of 196 protected-receive and SSH
  checks; and
- `git_foundation_redteam_results.json`: 120 of 120 transport and OAuth checks.

The G5 replay uses real PostgreSQL, the connected Diamond HTTP server, stock
Git smart HTTP, the native systemd sandbox, a separate Pages listener, and the
application RPCs. It demonstrates:

- deterministic search ranking, exact-title preference, whitespace
  normalization, literal wildcard punctuation, bounded summaries, and
  read-time policy reauthorization;
- repository notification enable/disable and unread-only behavior, with live
  repository reauthorization after membership revocation;
- manifest-declared `standard` and `small` executor profiles, selected per job,
  with an undeclared profile rejected before execution;
- generic artifact `HEAD`, bounded single byte ranges, suffix and open ranges,
  unsatisfiable and multiple-range rejection, `ETag`, weak/list
  `If-None-Match`, and strong `If-Range` behavior;
- authorized artifact deletion with immediate download fencing and delayed
  content-addressed garbage collection;
- immutable Pages rollback, environment stop, administrator-only canonical
  custom-host mapping, exact host routing, and immediate removal;
- stock Git mirror export and import of branches, annotated tags, Git notes,
  and Unicode path/content bytes; and
- existing OCI, cache, release, cancellation, dependency, traversal, symlink,
  credential-scope, filesystem-mode, and runner-cleanup boundaries.

## Real browser observations

A real Chrome session opened the disposable application, signed in with a
synthetic administrator, selected the generated repository and pipeline, and
rendered the live successful pipeline, isolated job log, OCI image, Pages
deployment, and stopped environment. The Activity route exposed the repository
notification preference fieldset with labeled checkboxes and controls.

At an explicit 390 by 844 viewport, the browser reported a 375-pixel client and
scroll width, so no horizontal overflow was present. The navigation changed to
`position: static`. The first Tab press focused the skip link; its transform was
the identity matrix and Chrome visibly rendered its focus outline. The viewport
override was reset after the check.

This browser observation covers the shared shell plus the G5 delivery and
activity surfaces. It is not a full WCAG conformance audit or an exhaustive
assistive-technology matrix.

## Findings repaired during G5

The active replay found and repaired:

1. SQL wildcard characters influencing fallback title search;
2. missing Git notes support at the reference-validation and inventory boundary;
3. incomplete HTTP validator and conditional-range behavior; and
4. an artifact/OCI subject rate bucket too small for an ordinary combined
   workflow; and
5. notification-preference functions that relied on row ownership without an
   explicit live repository read.

The final code also rejects cross-repository artifact access, nonadministrator
domain claims, malformed custom hosts, undeclared executor profiles, artifact
path traversal, output symlinks, stale event reuse, and post-deletion reads.

## Residual boundaries

G5 is the product-completion slice. G6 subsequently added concurrent Git and
mixed artifact/OCI/LFS/Pages memory measurements, replayed the distributed fault
and isolated-restore path, and published
`git_release_qualification_results.md`. Full OCI Distribution
push/resume/referrers conformance, broad assistive-technology testing, external
DNS ownership proof, and automatic TLS at the public edge remain outside the
local release campaign. Git, PostgreSQL, the operating system, the configured
isolation backend, the reverse proxy/CDN, DNS, TLS, and backup-key custody remain
explicit trusted dependencies.
