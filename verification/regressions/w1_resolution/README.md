# W1 resolver failures

An independent verifier reproduced these against the first W1 implementation.
`baseline_observations.json` retains the frozen pre-fix executable hash and actual
outputs. The base Git commit predates W1 and does not contain that implementation.

1. `a::b_c` and `a_b::c` produced the same internal name, rejecting a legal
   program. Length-prefixed module qualification fixes the collision. The
   accepted server entry must now produce exactly `117`.
2. Source could guess `__diamond_fn_...` names to reach private or unimported
   functions. Rejecting every source reference in the reserved namespace fixes
   that visibility bypass. Fixtures include the old format and current
   length-prefixed format on both targets and across a missing direct import.

The original exploratory observations remain in `observations.json`; their
intermediate executable hash differs from the final diagnostic-only build.
The final replay is embedded in `../../web_one_modules_results.json`.

```sh
python3 verification/regressions/w1_resolution/replay.py target/debug/diamond
```

All seven cases execute the actual CLI. The tests require failures to remain
failures after changing the internal naming scheme; simply changing mangling
must never become a visibility control. Errors identify the original module
and source line rather than an internal combined-program line offset.
