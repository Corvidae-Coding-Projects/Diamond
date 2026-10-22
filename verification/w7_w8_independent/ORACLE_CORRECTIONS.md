# Corrections grounded in the declared contract

These are checker corrections, not product fixes or passing substitutes.

- W7 external handlers retain W6's checked separation of outbound HTTP from
  application writes. The first combined HTTP+record/event handler correctly
  failed checking. The fixture now exercises external delivery/deduplication and
  database-only transactional completion/rollback in separate checked handlers.
- An initial WebSocket test reused prior legal short nominal fixture IDs.
  The endpoint's 64-character-only guard was a product limitation and was fixed
  by the implementing agent. The original response is retained in
  `first_runtime_observation.json`; `--short-ids` replays that original boundary.
- At-least-once delivery does not promise one attempt when no crash is injected.
  A normal concurrent job produced two identical receiver requests and one
  business effect, within the declared three-attempt bound. Requiring exactly
  one attempt contradicted REQ-14 and AC-14. The checker now requires bounded
  attempts, stable payload/key, and one effect in the explicitly deduplicating
  destination. The original observation remains `normal_retry_observation.json`.
- Session bootstrap may return a new anonymous session after invalid credentials.
  A 200 bootstrap is not proof the old identity survived. The restore checker
  now sends a protected operation with the original cookie/CSRF and requires
  401, in addition to independently confirming all pre-restore session rows
  were revoked and fresh password authentication works.
- `project-recover` accepts PostgreSQL and policy arguments, with no runtime
  grant argument. The driver now uses that documented interface.
