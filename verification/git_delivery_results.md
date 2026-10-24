# Diamond Forge G3 delivery, G5 product, and G6 streaming qualification

Date: 2026-10-23

The retained command is:

```sh
python3 verification/git_delivery_redteam.py
```

It builds a disposable PostgreSQL cluster, private Git/object/delivery stores,
the real connected Diamond server, the native delivery worker, and a separate
Pages listener. It uses stock Git over smart HTTP and the application RPCs; it
does not replace the product interfaces with test doubles.

The final replay passed all 152 checks recorded in
`git_delivery_redteam_results.json`. The demonstrated vertical path is:

1. push a commit containing strict `.diamond-ci.json`;
2. schedule that exact immutable commit with an idempotent event identity;
3. run it inside the explicitly granted chroot-style systemd sandbox with no
   host `/etc`, home, network, or writable system executables;
4. restore and republish a reference-scoped cache;
5. publish a content-addressed generic artifact, OCI manifest/layer,
   environment, and immutable Pages deployment;
6. stream the artifact and OCI layer through a repository-scoped credential;
7. serve Pages from the separate cookie-free listener; and
8. create a release bound to the successful source ref;
9. apply per-repository notification preferences;
10. export and import branches, annotated tags, Git notes, and Unicode content
    with stock Git mirror commands; and
11. exercise named executor profiles, artifact lifecycle operations, Pages
    rollback, environment stop, and custom-domain routing; and
12. stream four 16 MiB copies of each artifact, OCI layer, LFS object, and Pages
    object simultaneously while checking every size and SHA-256.

A separate real Chrome replay passed all 3 visual checks recorded in
`git_delivery_browser_results.json`: the reactive navigation exposed the
delivery route, its exact-ref scheduling and cancellation controls rendered,
and all delivery result regions were visible. The isolated Pages listener was
qualified separately through its real HTTP interface, including visible HTML,
cookie rejection, traversal rejection, and response isolation headers.

The offensive replay also verifies anonymous and cross-repository artifact
denial, single-range and unsatisfiable-range behavior, bodyless conditional
reads, deletion fencing, OCI tag and digest lookup, bodyless `HEAD`, Pages
traversal and ambient cookie rejection, custom-domain privilege and syntax
checks, event rebinding conflict, cancellation fencing, dependent-job
cancellation, unknown executor rejection, configuration traversal rejection,
output-symlink rejection, private store modes, content-addressed checksums, and
runner workspace cleanup.

The G6 mixed-stream wave transferred 268,435,456 bytes across sixteen
simultaneous clients. Web-process RSS rose from 91,904 KiB to 100,588 KiB and the
separate Pages process rose from 42,336 KiB to 50,320 KiB. Both listeners
survived, every response was byte-exact, and the large LFS upload was verified
by its declared digest before publication.

A sandboxed job that emitted 1,048,577 bytes was rejected at the 1 MiB job-log
limit. The failure was recorded without publishing outputs, and subsequent
delivery work continued normally.

Issues reproduced and repaired during G3 were:

- an incorrect detached-checkout argument boundary;
- a host where Bubblewrap namespaces are unavailable now fails closed and can
  use an explicitly configured, verified systemd chroot backend instead;
- systemd runner startup initially inherited too little authority for its user
  service bus, then used a mount profile that could not safely expose nested
  workspaces; the final profile passes only bus locator variables to the
  launcher and chroots the job into a private direct workspace;
- artifact and cache paths needed explicit symlink-safe workspace containment;
- failed dependency graphs left pending jobs behind;
- a Pages site-name collision could create unreachable deployment rows; and
- artifact, OCI blob, and Pages reads buffered whole objects instead of
  streaming fixed-size chunks under request permits.

The G5 expansion reproduced and repaired three product defects:

- standard `refs/notes/*` were rejected during mirror pushes; repository
  inventory and policy validation now retain notes alongside branches and tags;
- artifact validators handled only one exact tag and ignored `If-Range`; weak
  and list matching plus strong range validation now preserve HTTP semantics;
  and
- generic identity throttling allowed only 12 repository delivery reads per
  minute, enough for the original artifact test but not an ordinary artifact
  plus OCI workflow; delivery now has its own bounded 120-read repository
  bucket; and
- notification preference rows were owner-protected but did not independently
  reauthorize the repository; reads and writes now require a live authorized
  repository lookup and fail immediately after membership revocation.

This remains bounded evidence rather than full OCI Distribution conformance.
Registry push/resume/referrers and external DNS/TLS automation were not part of
the campaign. The deployment edge, Git, PostgreSQL, filesystem, and configured
runner isolation backend remain explicit trust boundaries. The aggregate G6
claim and the cross-campaign gaps are recorded in
`git_release_qualification_results.md`.
