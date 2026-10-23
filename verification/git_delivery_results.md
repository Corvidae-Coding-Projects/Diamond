# Diamond Forge G3 delivery qualification

Date: 2026-10-23

The retained command is:

```sh
python3 verification/git_delivery_redteam.py
```

It builds a disposable PostgreSQL cluster, private Git/object/delivery stores,
the real connected Diamond server, the native delivery worker, and a separate
Pages listener. It uses stock Git over smart HTTP and the application RPCs; it
does not replace the product interfaces with test doubles.

The final replay passed all 80 checks recorded in
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
8. create a release bound to the successful source ref.

A separate real Chrome replay passed all 3 visual checks recorded in
`git_delivery_browser_results.json`: the reactive navigation exposed the
delivery route, its exact-ref scheduling and cancellation controls rendered,
and all delivery result regions were visible. The isolated Pages listener was
qualified separately through its real HTTP interface, including visible HTML,
cookie rejection, traversal rejection, and response isolation headers.

The offensive replay also verifies anonymous and cross-repository artifact
denial, OCI tag and digest lookup, bodyless `HEAD`, Pages traversal and ambient
cookie rejection, event rebinding conflict, cancellation fencing, dependent-job
cancellation, configuration traversal rejection, output-symlink rejection,
private store modes, content-addressed checksums, and runner workspace cleanup.

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

This is bounded G3 evidence, not full OCI Distribution conformance or the final
G6 adversarial release claim. Registry push/resume/referrers, artifact
retention/deletion, custom-domain TLS, additional runner profiles, concurrent
memory measurements, and full distributed failure injection remain in the
later checkpoints defined by the design.
