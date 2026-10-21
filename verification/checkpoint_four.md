# Checkpoint-four enforcement contract

This pass reviews the entire supported interpreted surface and fixes reproduced
enforcement gaps. It does not implement the excluded language, query, package,
concurrency, or production profiles. Expectations below precede the new checks.

- Closed function-call effects include calls nested in constructors, arguments,
  matches, iteration, templates, and recursive call graphs. GET/HEAD cannot write,
  and source declarations cannot mint a host database grant.
- Request principals, scopes, repositories, and transactions cannot be captured,
  forged, serialized, or returned as ordinary values. Unsupported callbacks and
  dynamic dispatch are rejected. Transaction nesting, including through calls,
  is rejected while valid scoped transactions remain usable.
- Every get/select/create/update/delete requires its policy and scope grant.
  Defaults and derived identity precede create checks; updates check complete
  before/after state; returned mutation records also require read permission.
  Direct opaque-ID deletion need not grant unrelated read authority.
- Selection filters, ordering, and pagination observe authorized relations.
  Invalid stored values or failed policy evaluation cannot silently become a
  successful partial selection. Stored IDs must obey their checked opaque shape.
- Explicit HEAD routes take precedence over GET fallback regardless of source
  order, and enforce their own private/public declaration.
- HTTP startup rejects a database path without a source database declaration
  before opening or creating that file. Anonymous sessions issued for error
  responses must be transported or revoked, avoiding unreachable registry entries.
- Transactions preserve confirmed commit, confirmed abort, and indeterminate
  outcomes. Actual SQLite contention/fault checks must not report success or
  confirmed rollback when evidence is insufficient. Unknown HTTP outcomes expose
  the opaque operation identifier and never instruct automatic retry.
- Typed HTML, URLs, forms, response mapping, and native display reject restricted
  values and retain contextual escaping. Authentication, CSRF, expiry, revocation,
  native consumers, and the complete existing browser journey remain working.

Coverage records distinguish source inspection, behavioral evidence, and untested
dependencies/fault schedules. Broad adversarial exploration and fine-detail work
remain checkpoints five and six.
