# Authorized red-team result

The campaign in `red_team.md` targeted only the local Diamond executable,
disposable files and SQLite databases, and disposable loopback listeners. It
confirmed nine shared-boundary defects and preserved every pre-fix observation
under `verification/regressions/red_team_*` before editing.

The repairs now:

- reject non-UTF-8 argv without panic and sanitize the final rendered CLI error
  chain against terminal controls;
- enforce the shared 1 MiB source ceiling during the file read and reject FIFO,
  directory, and other non-regular source paths without blocking;
- propagate call-graph effects and transaction requirements through a reverse
  work queue instead of repeated whole-program snapshots;
- keep the 4,096-session registry available under anonymous churn, rotate login
  in place, and preserve authenticated sessions during anonymous eviction;
- anchor account files with nonblocking no-follow opens, enforce private regular
  single-link metadata, reject SQLite symlink opens, and compare device/inode
  identity before schema access;
- validate the complete bounded account set, exact Argon2id parameters, and
  schema at provider startup so corrupt rows cannot create a username oracle.

Two complete passes used fresh seeds `0xD1A06011` and `0xD1A06029` against the
same frozen executable:

`51b5829a10ebad5500ca78b83d8f39a4a61d97c685a54646d97c083cdd70fa3f`

Each pass included 48 fresh malformed sources, the 4,094-function maximum call
chain, 32 generated HTML payloads, four redirect attacks, 16 uniform-login
comparisons, 8,194 anonymous-session flood requests, the deterministic account
replacement race, corrupt-hash startup, the public native session consumer, 395
compiler-authority invocations, 306 transport requests, and three stateful
two-user application sequences. The stateful passes issued 745 and 730 HTTP
requests respectively. Both scanned 62 locked dependencies against the local
RustSec database with zero vulnerabilities or warnings. The RustSec database was
refreshed on 2026-10-21 before the passes; its refresh reported 1,348 advisories
at commit `4b587f00996ebe5c05a73eaef105ef241e876414`.

The detailed machine-readable receipts are `red_team_pass_one.json` and
`red_team_pass_two.json`. This is bounded negative evidence for the tested MVP
surfaces. The single-threaded loopback adapter, its five-second client deadline,
process-local rate/session state, operating system, SQLite internals, and the
documented production exclusions remain outside any production-security claim.
