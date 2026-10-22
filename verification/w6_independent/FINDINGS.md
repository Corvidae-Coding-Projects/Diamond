# W6 independent finding and driver corrections

## Actual finding: resolver deadline exceeded during runtime cleanup

The original frozen `cc42010c647d9956d060502a483ba83e6d2c3ba7fd322439535c62e65244d505`
executable returned `503 unavailable` after **8.0063 seconds** for a native HTTP
operation with `timeout_ms=1000`. A process-local LD_PRELOAD fixture delayed only
`getaddrinfo("w6-stall.invalid")` for8 seconds, then called the real system resolver.
The fixture recorded exactly one invocation and fabricated no resolver answer.
Fresh RPCs and clean process shutdown passed. This exceeded both the configured
one-second deadline and the five-second maximum profile deadline.

Evidence: `dns_initial_failure.json`, `dns_delay.c`, `dns.py`.
Reproduce against the initial binary:

```
python3 verification/w6_independent/dns.py --binary /path/to/cc420-diamond --report /tmp/dns-replay.json --expect-getaddrinfo 1
```

The implementation owner replaced blocking resolver work with asynchronous DNS.
The frozen18878345 replay passes: the retained hostname invokes getaddrinfo zero
times, and actual owned UDP DNS drops return in1.0076 seconds for timeout_ms=1000.
Valid UDP/TLS, CNAME, mixed-address denial, private-address denial and fresh lookup
recovery also pass. The .invalid former hostname is locally rejected by Hickory;
the actual UDP cases therefore use synthetic.test names and require observed DNS
queries. This avoids confusing local name rejection with cancellation evidence.

## Verification-driver corrections

- An eager8MiB wrong-principal sender raised BrokenPipe when the server rejected
  authority before consuming the body. The duplicate large negative probe was
  removed; small/empty principal-denial probes and valid8MiB transfer remain.
- A helper named `http.py` shadowed Python's standard-library package. It was
  renamed `outbound.py` before running outbound qualification.
- The driver attempted delete on reserved entries, although delete applies to
  published/deleting state. It now expires reservations then runs reconciliation.
- Immediate receiving-to-failed metadata transition was an invented assertion;
  the contract promises bounded resource release plus expiry reconciliation.
  The driver now verifies physical partial removal, private readability, fresh
  same-node work and later metadata cleanup.
- The first browser-host compiler probe had invalid state syntax. It was replaced
  with valid typed state plus an event that requests a host handle; its diagnostic
  now exercises the intended browser authority boundary.

These were fixture errors, not product findings. Final receipts replace only W6
qualification outputs; the actual initial resolver failure remains preserved.

## Bounded resource observation

`outbound_initial_resource_observation.json` retained a serial-to-concurrent FD
increase20→30 after three disconnected callers. Equal concurrency was then
repeated three times:20→30→30→30, with nine actual upstream calls, zero active
fixture work after each batch, and successful fresh same-node calls. The initial
+6 ceiling confused bounded pool warmup with a leak; it was replaced by repeated
concurrency stability. This is a bounded resource observation, not universal
absence of leaks or a production capacity claim.

## Actual finding: reserved IPv6 literals do not return policy denial

The implementation owner's final source review requested exactly two literal
public-profile probes. On frozen18878345, https://[3ffe::1] returned503 unavailable
in1.00675s and https://[3fff::1] returned503 unavailable in1.00869s, rather than
immediate403. `reserved_ipv6_initial.json` retains both observations and two clean
node exits. No CA, resolver or route changes were made; no other external literal
was probed. This proves the policy-denial gap, not a successful IPv6 connection.
The narrow repair must return exact403 denied on both saved cases.

Final narrow repair replay on0aa105a7 returns403 denied for both saved cases:
3ffe::1 in2.78ms and3fff::1 in5.66ms. The full runner includes both cases alongside
the unchanged file, outbound, DNS, compiler and native campaigns.
