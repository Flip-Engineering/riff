# Agent operations

Agents use the same queue, validation and reference resolution as the Studio,
through a documented HTTP surface. No page globals, selectors or UI text are
involved. `GET /api/capabilities` advertises the surface under `agent_surface`:
its version, compatibility requirements, and for every operation its method,
path, input schema and output schema.

## Requests

- Base path: `/api/v1/operations`. The version is in the path and in
  `agent_surface.version`. Additive changes keep `v1`; a breaking change is
  published under a new base path while the earlier one keeps working.
- Reads are plain `GET`. Every `POST` needs `X-Riff-Request: 1` and
  `Content-Type: application/json`, as for the rest of the API.
- Invoke an operation with `POST /api/v1/operations/{name}` and a JSON body.
  Unknown input fields are refused.
- Errors are `{"error": "..."}` with 400 (invalid input), 403 (not a Studio
  request, including a reference or ID of the wrong type), 404 (unknown ID), 409
  (idempotency conflict, or a second `operation.resume` of an operation that is
  already being resumed, decided inside the write transaction so concurrent calls
  queue exactly one job), 500 (an unexpected failure, with a message) or 503 (library
  unavailable). A request never ends by dropping the connection.
- Receipts and references contain opaque IDs and captured provenance, copied
  field by field from a fixed list, so they carry no filesystem paths. Recipes and
  other text you sent or the take produced are returned exactly as recorded and are
  never rewritten. Error messages, which are free text, show this installation's
  directories as `<library>`, `<data>` or `<riff>`.

## Operations

| Name | Effect |
|---|---|
| `GET /api/v1/operations` | Recorded operations, newest first. Filters: `family`, `status` (one of `queued`, `running`, `cancelling`, `done`, `planned`, `performed`, `synthesized`, `cancelled`, `interrupted`, `failed`; anything else is a 400), `parent`, `since`. `limit` is the page size you choose; without it every match is returned. With it, pass the returned `next_cursor` as `cursor` for the next page. |
| `GET /api/v1/operations/{id}` | Poll one operation and read its receipt. Accepts an operation ID, or the ID of the job or take it queued. |
| `recipe.validate` | Runs the submit-time validation and reference resolution on `{"recipe": {...}}` and queues nothing. Returns `valid`, and either the `effective_recipe`, `references` and `parent`, or an `error`. |
| `references.resolve` | Verifies `score_source`, `performance_source`, `acoustic_source` and `parent_track_id`, or with `{"subject": id}` lists the references that take, job or operation offers. Each result is `verified`, `incompatible` or `unavailable`, with provenance. |
| `study.create` | Queues `{"recipe": {...}}` and returns identity before any work runs. Optional `family_id` (an existing family), `parent` (`{"kind": "track\|job\|operation", "id": ...}`) and `idempotency_key`. |
| `inputs.compare` | `{"subjects": [id, id, ...]}` with takes, jobs or operations. Returns the recorded inputs the subjects `shared`, and the `differences` with each subject's value and whether it was recorded. The field list is the one the lineage view uses, plus the composed score and performance hash. |
| `take.develop` | Develops a take into a new one, for example a longer take from a study. `{"source": id, "changes": {...}}`. Keeps the seed and the verified composed score by default. Every recorded input can be replaced in `changes`. Records the parent, the family, what was retained and the selection it came from. **The issues' "promote" is this operation.** Optional `review_id` names a saved producer review of the source take; the new take records that review's reference clips (`review_references`: ID, SHA-256 and label) as provenance, the same as a take queued from the Studio. |
| `take.replay` | `{"source": id, "replay": "score" \| "performance" \| "sound"}`. Replays the take's exact composed score, saved performance or saved sound as a new take, retaining the seed. A stage that cannot be replayed is an error, not a silent fallback. `changes` apply as for develop; for `sound` only `decoder` and `title`. |
| `take.select` | `{"take": id, "rationale": "..."}`. Records a durable choice of a completed take within its family. Append-only: a later choice is added to the history, never replaces it. Queues nothing. |
| `operation.resume` | `{"operation_id": id}`. For an interrupted, failed or cancelled operation. Queues a new operation, linked by `resumes`, that continues from the last captured stage (saved sound, then performance, then composed score, then a plain rerun) with the same seed. It happens only when called: Riff never resumes or re-queues a cancelled or interrupted take by itself. `POST /api/jobs/{id}/retry` is the same call. |
| `operation.cancel` | `{"operation_id": id}`. Cancels the job. Completed stages stay in the receipt. |

The Studio's `POST /api/generations` and `POST /api/plans` run the same
implementation, so a take queued from the form has the same operation record as
one queued by an agent. Their responses keep their shape and add `operation_id`,
`family_id` and `replayed`.

## Deriving takes

`take.develop` and `take.replay` build the new recipe on the server from the
source's recorded inputs, so an agent does not hydrate a draft from page state.
`source` is a take, job or operation ID. Every recorded input is carried
(words, direction, mode, duration, solver, steps, sampling controls, adapters).
References are chosen with `retain`, a list of `seed`, `score` and
`performance`; they cannot be set in `changes`.

- `take.develop` defaults to `["seed", "score"]`. A default that cannot be kept
  (no composed score, planning set to Direct, edited notation supplied) is
  skipped and explained in `derivation.notes`. Naming it in `retain` makes it
  an error instead.
- `retain: []` carries the inputs only, which is an ordinary variation with a new
  seed.
- A retained performance fixes the duration; `changes.max_seconds` with it is
  refused. Retained scores and performances are verified when the draft is
  built, and again by the queue.
- `enqueue: false` returns `{draft, derivation, parent, validation}` and queues
  nothing. The Studio uses it to fill the editor.
- The new take's `parent_track_id` is the source take, so the lineage view shows
  the link. Its operation `parent` is the source operation, and it joins that
  operation's family.

The receipt's `derivation` records the source, what was `retained`, `notes`, the
input `changed` against the source (the lineage difference format), and the
`selection` it came from: the one passed as `selection`, or the source take's
latest selection in its family.

## Identity

`study.create` returns `operation_id`, `job_id`, `family_id`, `parent`, `status`
and the receipt from the call that queues the job. The operation record and the
job are written in one database transaction, so there is no job without an
operation record or the reverse, including when recording fails.

`family_id` groups related work. A new study starts a family; a take with a
recorded parent (`parent_track_id` or `performance_source`) joins its parent's
family. `parent` is the take this one continues, or `null`.

## Idempotency

Send `idempotency_key` in the body, or an `Idempotency-Key` header on the Studio
routes. A key is unique per operation name.

- Same key, same input: the original operation is returned with `replayed: true`
  and HTTP 200. Nothing is queued.
- Same key, different input: HTTP 409 with both request digests. Nothing is
  queued.
- No key: the request is not deduplicated.

Keys are kept for the life of the operation record. Choose a key per intended
take, and reuse it when a response is lost.

## Receipts

A receipt binds, at the time the server accepted the work:

- `request`: the input exactly as submitted.
- `effective_recipe`: the recipe after validation and defaults, as queued.
- `references`: each resolved reference with its opaque ID and captured
  provenance (score hash, token count and source job; performance frames and
  hash; saved sound metadata; adapter receipts).
- `runtime`: the Riff version and engine capabilities.
- `seed`.

and, read live from the job so it is complete after a restart:

- `job`: status, error and timestamps.
- `stages`: `plan`, `performance`, `sound` and `audio`, each `not_applicable`,
  `pending`, `reused` (with `from`), `done`, `failed` (with `error`) or
  `not_produced` once the job has ended without it.
- `symbolic_plan`: the composed score's identity.
- `outputs`: `track_id`, `score_source`, `performance_source` and
  `acoustic_source` IDs that later operations accept.
- `errors`, `result_recipe` (the job's recipe now, with captured stage
  artifacts) and `terminal`.

Operation records are stored in the library database. Jobs made before this
surface existed have no record: `GET` on their job ID returns a receipt derived
from the job row with `recorded: false` and the missing fields named in
`unrecorded`. `request`, `references` and `runtime` are not reconstructed.

## The Studio and agents share one implementation

Every take the Studio queues (the main form, studies, score composition, saved
sound finishing and producer recommendations) is posted to `/api/generations` or
`/api/plans`, which call the same `Operations.submit` as `study.create`. Validation,
reference resolution, the queue and the operation record are therefore one code
path. The Studio sends an `Idempotency-Key` with each submit and keeps it only
when no response arrived, so asking again for the same recipe after a lost
response returns the take already queued instead of queueing a second one.

The Studio's editor still fills itself from a take in browser code. It holds
fields only the page has (readable notation, draft text, undo), and the browser
tests that cover score replay mock the API with synthetic recipes. Moving that
hydration onto the server drafts (`enqueue: false`) is deferred to the study
families work (#10). Until then the two are held together by tests rather than by
construction:

- `tests/agent-parity.mjs` drives the real controls against a real server and checks
  that the recipe the Studio builds for **Refine this performance**, for **Render
  performance** in the history, and for a variation equals, after validation, the
  draft `take.replay` or `take.develop` returns for the same take. A change to what
  the server carries over, or to what the page carries over, fails it.
- Finishing a saved sound (**Finish audio**) is not covered in the browser, because
  the browser fixture has no native saved-sound artifacts. `tests/test_operations.py`
  checks that `take.replay` with `replay: "sound"` queues exactly the take the
  Studio's `{acoustic_source, decoder, title}` request does, using a stub of the
  saved-sound service.
