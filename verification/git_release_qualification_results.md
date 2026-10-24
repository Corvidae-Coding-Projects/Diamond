# Diamond Forge G6 bounded release qualification

Date: 2026-10-23

G6 completes the local adversarial checkpoint for the Diamond Forge reference
application in `examples/git-browser`. This is release evidence for the tested
Linux loopback profile. It is not a claim that the software is defect-free or
that an untested public deployment inherits these results.

## Final replay

The final source was built and driven through its real compiler, PostgreSQL,
HTTP, stock Git, OpenSSH, systemd job sandbox, filesystem, backup, restore, LFS,
OCI, and Pages interfaces. The six retained campaigns pass **774 of 774** checks:

| Campaign | Result | Principal boundaries |
| --- | ---: | --- |
| `git_compiler_boundaries.py` | 14 / 14 | Static bindings, capabilities, server-only authority, effect separation, OAuth policy shapes |
| `git_foundation_redteam.py` | 120 / 120 | Smart HTTP, OAuth state, credentials, repository configuration, cancellation, concurrent Git memory |
| `git_application_redteam.py` | 184 / 184 | Organizations, permissions, issues, reviews, merges, REST, webhooks, LFS, event recovery |
| `git_policy_ssh_redteam.py` | 196 / 196 | Protected receive, hooks, OpenSSH, archives, keys, certificates, promotion fencing and routed writes |
| `git_delivery_redteam.py` | 152 / 152 | Sandboxed CI/log limits, artifacts, OCI pull, Pages, caches, lifecycle, mixed streaming |
| `git_scale_recovery_redteam.py` | 108 / 108 | Two-node state, search, workers, shard failover, GC, encrypted backup and isolated restore |

Replay commands:

```sh
python3 verification/git_compiler_boundaries.py
python3 verification/git_foundation_redteam.py
python3 verification/git_application_redteam.py
python3 verification/git_policy_ssh_redteam.py
python3 verification/git_delivery_redteam.py
python3 verification/git_scale_recovery_redteam.py
```

The machine-readable `*_redteam_results.json` and
`git_compiler_boundaries_results.json` files are the sanitized final receipts.
The exercised tool profile was Diamond 0.16.0, Rust 1.96, Git 2.56.0,
PostgreSQL 18.6, OpenSSH 10.2p1, and Python 3.14.8.

## Release-pressure observations

The Git campaign pushed one incompressible 24 MiB object and then cloned it with
eight simultaneous stock clients. Every checkout matched the source bytes. The
web process grew from 73,048 KiB to 82,524 KiB, while the complete web/native-Git
process tree peaked at 586,132 KiB. A separate 20-client wave completed eight
exact clones and returned explicit `503` overload responses to twelve clients;
the complete process tree peaked at 593,420 KiB and the server remained usable.

The delivery campaign produced 16 MiB artifact, OCI, LFS, and Pages objects,
then opened four synchronized streams for each class. All sixteen responses,
268,435,456 bytes in total, matched the independent sizes and SHA-256 digests.
Web-process RSS increased by 8,684 KiB and the separate Pages process by 7,984
KiB. Both listeners survived. The LFS object was digest-verified on upload before
being included in the download wave. A separate sandboxed job emitted 1,048,577
bytes and failed closed at the 1 MiB log boundary; later jobs still ran.

The OpenSSH campaign also opened four stock-Git archives of the repository's 8
MiB incompressible object at once. Every command completed and every extracted
`random.bin` matched the pushed bytes.

The recovery campaign created an encrypted snapshot containing PostgreSQL and
the shared-object, Git, delivery, and operations roots. It rejected a wrong key
and tampered ciphertext before importing, restored only into empty isolated
roots, verified the manifest and Git histories, revoked old credentials,
repaired root-relative policy configuration, and cloned through the restored
placement. The distributed campaign also replays active-receive promotion
fencing, incomplete-target rejection, exact reconciliation, stale-generation
rejection, old-shard fencing, and a write through the promoted shard.

## Findings and repairs

1. A 20-client smart-Git wave could consume nearly the complete 1 GiB host
   profile through native pack processes even though the Diamond heap remained
   small. Smart Git now has a separate eight-slot semaphore held until stream
   completion. Excess work fails explicitly, the same wave remains below 600
   MiB, and `/health/metrics` reports only the aggregate active transport count.
2. The process-tree sampler could lose a short-lived child between `/proc`
   enumeration and status reading. The retained replay now treats that expected
   kernel race as a departed process without hiding server death or client
   failure.
3. The compiler attack corpus still targeted the removed raw `git_merge` call.
   It now mutates the current receipt-bound `git_integrate` adapter and proves
   that a non-text native object is rejected by the checker.

Earlier G1-G5 findings and their live regressions remain documented in the
foundation, application, policy/SSH, delivery, scale/recovery, and product
completion evidence files.

## Claim boundary

The final local campaign covers the implemented pull-oriented OCI surface, not
the entire OCI Distribution conformance suite or registry push, resumable
uploads, signatures, or referrers. It uses synthetic OAuth configuration and
local verifier inputs rather than live Google/GitHub account exchange. The
retained G5 Chrome receipts cover the application shell, delivery/activity
surfaces, keyboard focus, and the narrow mobile viewport; G6 did not rerun a
broad browser or assistive-technology matrix because it changed transport
capacity and verification drivers, not browser artifacts.

External TLS termination, DNS ownership, provider outages, cross-host network
partitions, PostgreSQL high availability, off-host retention, production RTO/RPO,
public-runner micro-VM isolation, and backup-key custody remain deployment work.
Git, OpenSSH, PostgreSQL, the operating system, the configured job isolation
backend, the reverse proxy/CDN, and deployment administrators remain trusted
dependencies. Operators must repeat the campaigns for their pinned versions and
validate those external controls before exposing the application publicly.
