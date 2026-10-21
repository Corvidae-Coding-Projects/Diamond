# Checkpoint-three observable contract

The accepted breadth-first plan calls for filling out the MVP, ending with the
message board's complete intended user journey written in Diamond.

- Nominal, non-generic enums have named constructors and positional payloads.
  Matches over enums, Bool, Option, and Result are exhaustive; incompatible
  branches, unknown/duplicate cases, and malformed bindings fail checking.
- Immutable lists, bounded iteration, and typed HTML fragments support listing
  records. Match/iteration blocks use a final expression; nested early returns,
  nested/or patterns, generic enums, and general callbacks remain excluded.
- Expected form/repository failures are usable Result values. Matching them
  permits ordinary application feedback, while `?` preserves their safe HTTP
  failure class. Internal failures and indeterminate transaction outcomes cannot
  be converted into a successful ordinary operation.
- Selections accept structurally bound equality filters and bounded limit/offset,
  order by opaque id, and return only the authorized relation before pagination.
  Missing read policy, invalid filters, and unsupported query shapes fail checking.
  Joins, aggregates, arbitrary predicates, and bulk mutations remain excluded.
- Transaction blocks roll back a propagated failure or a returned Err. Catching
  an operation failure cannot turn a failed transaction into a successful commit.
- In the actual browser, an account can sign in, list its posts, create, follow
  the detail link, edit the title/body, publish/unpublish, delete, and sign out.
  Public visitors can list/read published posts; other users cannot edit/delete
  someone else's post. Validation errors preserve stored rows and allow retry.
- Restarting retains committed records. Login is required again. Replays inspect
  SQLite independently, exercise distinct users and hostile/Unicode text, and
  check selection filtering/pagination against independently computed relations.
- Prior checkpoint journeys, the native consumer, build, formatting, strict
  Clippy, doctests, and rustdoc remain passing on the final implementation.

This is a local interpreted MVP feature pass. Full enforcement review, broader
fault campaigns, production hosting, and fine syntax/diagnostic polish remain
later checkpoints.
