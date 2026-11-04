# Inference pilot API

Base: `https://f16.live/cyber/api/inference`. All responses are JSON objects.
POSTs require a bearer invitation token and `Content-Type: application/json`.

| Operation | Method/path | Request |
| --- | --- | --- |
| Discover models and limits | GET `/models` | None; no credential sent |
| Create or retrieve quote | POST `/quotes` | `request_id`, `model`, `prompt`, `max_output_tokens` |
| Execute quoted request | POST `/execute` | `quote_id` |
| Read state/result | POST `/status` | `quote_id` |

The quote binds its owner, request contents, model, output bound and expiry. Quoting
consumes an invitation slot even if it later expires. `execute` starts the job once
and returns 202/running, or returns a stored result for an already completed job.
`status` returns the current state, with output and usage when complete.

Typical states: `quoted → running → completed`; failures remain `failed` and need
operator review. A crash can leave `running` without a live worker. The client
will eventually stop polling; it must not create a replacement request automatically.
Expired/revoked invitations cannot retrieve results. Revocation does not undo work
already in progress.

A completed result includes `quote_id`, `model`, `output`, `usage`,
`charged_microusd: 0`, `settlement: "disabled"`, and `semantic_acceptance: false`.
Generation usage includes token counts, timings, stop reason and whether the output
finished before its token limit. No current response proves payment or code quality.

Common errors:

| HTTP | Meaning | Client response |
| --- | --- | --- |
| 400 | Invalid fields, model or limits | Fix the request; preserve IDs only for identical contents |
| 401 | Missing, expired or revoked invitation | Obtain valid operator-authorized access |
| 404 | Unknown route or quote not owned by this invitation | Check the ID and deployment |
| 409 | Conflict, expired quote, busy worker or uncertain attempt | Inspect status and error; don't blindly launch another job |
| 429 | Invitation or global quota exhausted | Stop; request operator guidance |
| 502/503 | Generation or service unavailable | Reconcile the existing request before retrying |

No tools, arbitrary endpoints, uploads, shell commands, wallet operations or
supplier payout fields are supported. Hosted model outputs remain untrusted data.
