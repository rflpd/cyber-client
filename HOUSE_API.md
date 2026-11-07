# Experimental house-model client

`cyber_client.house.HouseClient` prepares Slotbot and other orchestrators for the
versioned house protocol. **There is no advertised house endpoint or qualified
house model yet.** This module has no default URL, discovers no service and does
not change the existing Qwen2.5 `Client` or CLI.

The operator must supply a reviewed endpoint, scoped invitation and exact task,
resource and model-profile bindings. Listing a model profile in the package does
not mean that model is available. Local synthetic tests are not model benchmarks.

## Explicit operations

The following is integration code for an eventual approved deployment, not a
command to run against the current showcase. `request` must be the exact approved
request saved by your application. `approved_endpoint` and `private_token_file`
come from your operator; never put credentials in a URL or a command argument.

```python
from cyber_client.house import HouseClient

client = HouseClient(
    base_url=approved_endpoint,  # HTTPS URL ending in /v1/house
    token_file=private_token_file,
)
job = client.quote(request)  # makes one request; does not execute

# Durably save job.request, job.quote and submission intent BEFORE execute.
# Keep those records private: the request contains your prompt.
record = client.execute(job)  # at most one submission in this client instance
# If still running, later make a single status read:
record = client.status(job)
```

`timeout` defaults to 15 seconds (maximum 60) for connection/request writing and
for quote, status and cancellation calls. `execution_timeout` separately allows
up to 345 seconds by default (maximum 360) to receive an execute response after
the request was sent. An absolute response timer also bounds slow headers or
trickling bodies. DNS resolution is not protected by a hard process deadline.
This transport allowance does not extend the server's job,
invitation or operator-grant deadline. A timeout or lost response consumes no
new execution authority: retain the same job and reconcile status; never submit
a replacement or retry execution automatically.

The request contains exactly `schema` (`cyber-house-trial/1`), `request_id`,
`profile_id`, `task_contract_sha256`, `resource_contract_sha256`, `prompt` and
`max_output_tokens`. Profiles are versioned configurations, not aliases for the
old `small`/`large` models. Limits are 8,192 UTF-8 prompt bytes, 1,536 requested
output tokens and an 8,192-byte output. The server separately enforces template
and context fit, resource authority, invitation quotas and execution grants.

A returned quote binds the normalized request digest and model profile, task,
prompt and resource digests. The client requires zero charge, disabled settlement
and `semantic_acceptance: false`. A completed result also needs matching model and
runtime provenance and complete-generation/cleanup flags. These checks establish
response consistency, not independent model attestation or correctness. Never
execute returned source without your approved isolated evaluator.

## Interrupted work and restarts

No method retries HTTP requests or creates replacement work automatically. A lost
execute acknowledgement is an **unknown outcome**, not permission to execute
again. The client refuses a second execute for the same quote in its lifetime,
including concurrent attempts. This is a convenience guard; durable duplicate
prevention remains a server responsibility.

After a process restart, restore your saved records for **status or an explicit
stop request, never execution**:

```python
client = HouseClient(base_url=approved_endpoint, token_file=private_token_file)
job = client.restore(saved_request, saved_quote)  # local validation, no HTTP
record = client.status(job)                     # one read, no new execution
```

A restored job cannot execute. Preserve unknown/interrupted records for operator
reconciliation; do not replace the request ID, construct a new client to resubmit,
or repeat the quote/execute sequence as a recovery shortcut. The SDK is not a
durable job orchestrator and does not save your records for you. If the saved
request/quote is missing, recover through the operator instead of guessing.

## Requesting cancellation

```python
acknowledgement = client.cancel(job)  # one authenticated stop request
record = client.status(job)           # separate read; no retry or new job
```

Cancellation uses the existing quote and works for restored jobs. The client
blocks further execute submissions for that quote before sending the request,
including if the request is interrupted or its acknowledgement is lost. It never
automatically retries cancellation or creates replacement work. Repeating an
explicit cancellation reads the same immutable server request; it does not create
an execution claim or restore quota.

The distinct `cyber-house-cancellation/1` acknowledgement binds the saved quote
and request digest. `requested` reports persisted intent; `execution_prevented`
can be true only for a request accepted before admission. It always carries
`cleanup_verified: false` and no retry/payment/acceptance authority. A running
worker may still be stopping. A job completed before cancellation retains its
result. Keep the acknowledgement separately from the complete generation result
and operator cleanup evidence. Accepted cancellation facts also constrain later
status/results: a cancelled job cannot later become a completed generation, and
terminal observations cannot regress. Contradictions are rejected before updating
state or output receipt. Expired or revoked credentials cannot request
cancellation; the server worker separately observes authority ending and stops.

## Transport and credentials

This experimental module supports POSIX systems with private, owner-held regular
token files (mode 0400 or 0600); symlinks and hard links are refused. Reads check
file identity and modification metadata. Use a trusted destination and filesystem;
the SDK does not protect credentials against another process running as you.

HTTPS uses normal certificate verification. HTTP is allowed only for a literal
loopback address in local tests. Redirects and ambient proxies are not used.
Responses are capped at 256 KiB, with strict JSON and protocol validation. Errors
use fixed categories and never echo server error bodies or tokens. The short
`timeout` and separate `execution_timeout` budgets above bound socket waits;
an absolute response timer closes slow headers or bodies. DNS resolution does not
have a hard process deadline.

The SDK does not grant retention/deletion guarantees, publishing permission,
payments, model access or an execution budget. Use synthetic data until the
operator has established the deployed service's data-handling contract.

## One status view for people and agents

`client.summary(job)` returns a JSON-ready projection of the same validated server
records used by quote, execute, status and cancellation. It makes no HTTP call. A human dashboard
and an agent can consume this object without inventing a separate job state:

```python
import json

# After a successful operation, or in the caller's fixed-error handler:
print(json.dumps(client.summary(job), indent=2))
```

The projection includes exact profile/task/resource bindings, the last validated
state, observation status, whether this client attempted execution and whether a
validated complete output was received. It omits the token, prompt and answer
text. Keep it private: quote IDs and hashes remain job metadata. A received output
digest is preserved; a later different answer for that same job is rejected.

The projection is now `cyber-house-client-status/2`. It replaces the earlier draft
`restored_for_status_only` field with `restored_without_execution_authority` and
adds separate cancellation observations. A cancellation observation is either
`not_requested`, `outcome_unknown` or `validated_acknowledgement`. The last received
cancellation state/request flag remains a prior observation after an error; it
never establishes worker cleanup or output receipt. This change belongs to the
unmerged experimental client, not a released endpoint upgrade.

| Situation | Observation | Meaning |
| --- | --- | --- |
| New quote received | `validated_quote` | A quote was checked; it is not execution permission |
| Saved records restored | `restored_records` | Historical snapshot; read status to learn the server's current state |
| Valid status/execute response | `validated_response` | Server bindings checked; no independent model attestation |
| Execute request fails | `execution_outcome_unknown` | Work may have started; keep the job and use status/operator reconciliation |
| Status request fails or is rejected | `status_unavailable` | Last known state is retained; freshness is unknown |

Even a `completed` quote does not prove the caller received an output. A complete
generation remains `semantic_acceptance: false`; full cost remains unknown and
settlement disabled. The view grants no new execution or retry. It is a client
projection for an eventual invited service, not a deployed browser dashboard,
public discovery endpoint or proof of a qualified house model.

## Evidence

The client suite passes 25 test methods using local HTTP fixtures, including
lost acknowledgements and restart reconciliation, concurrent submission guards,
wrong-result/payment/acceptance rejection, credential-file checks and redaction.
A separate integration check against the server's actual API adapter and a
private temporary Store confirmed quote/status compatibility, all four profile
bindings and refusal of disabled execution with zero attempts. No real model,
paid provider, production ledger or remote endpoint ran in these checks.

On 7 November, the complete client suite passed 31 test methods after the status
projection was added. An additional actual server-adapter/temporary-Store HTTP
check used both prepared Qwen3.5-9B and GLM profiles with the new 768-token remote
scope. Quote/status hashes matched, saved jobs restored for status only, and
execution remained disabled with zero canonical attempts. These are source and
synthetic transport checks, not house-model generation or remote delivery.

The later cancellation extension passed 42 client test methods. Actual
server-adapter checks with temporary Stores exercised both prepared profiles:
restored stop requests were idempotent, both client and server refused subsequent
execution, and each Store retained one quote, one cancellation and zero attempts.
Server/thread cleanup passed. No model, remote delivery or paid provider ran.

The execution-response timeout increment passed all 46 client tests. Delayed
execution responses can outlast the short connection budget, while status calls
stay short; trickling headers and lost responses retain the submission guard.
A separate check connected this SDK to the actual private Unix HTTP adapter and
a temporary canonical Store. The selected Qwen review request produced one quote
and one cancellation. Protected execution was rejected before admission, leaving
zero attempts; socket and temporary-state cleanup passed. This local transport
check does not establish remote delivery or installed service readiness.
