# Experimental buyer and supplier client

`cyber_client.supplier.SupplierClient` connects the invited registration,
qualification and fixed **$0 coding-job** flow. There is **no advertised public
endpoint for this API**. The operator must provide a reviewed endpoint and scoped
credential. Creating this client does not activate a service, provision an account
or grant provider access. The existing inference CLI and house protocol are separate.

The current server supports one synthetic public coding task. An operator must
provision owner identities, issue the qualification challenge and review permitted
supply locally. Suppliers qualify using their own permitted capacity; qualification
does not require a buyer wallet. A declaration does not prove commercial permission,
available compute, independent ownership or earnings. Live settlement is disabled.

## Read the requirements first

Integration example for an invited deployment; all configuration and IDs below
must come from its operator or your own retained records:

```python
from cyber_client.supplier import SupplierClient, artifact_bytes

worker = SupplierClient(
    base_url=approved_api,  # HTTPS ending in /v1; literal loopback HTTP for local tests
    token_file=private_worker_token_file,
)
worker.request("register_service", manifest=service_manifest,
               configuration_sha256=configuration_digest)

# The operator separately issues a task for this immutable service configuration.
task = worker.request("read_challenge", challenge_id=issued_challenge_id)
requirements = task["requirements"]
challenge = task["challenge"]
# Inspect task["task"], requirements, current status and submit_before before work.
# Generate an answer only with separately permitted resources and authority.
# Durably retain this exact answer, binding and submission intent before sending.
qualification = worker.request(
    "submit_answer", challenge_id=challenge["challenge_id"],
    binding_sha256=challenge["binding_sha256"], source=prepared_synthetic_source,
)
```

Requirements describe the pinned checker, allowed source subset, 32 KiB UTF-8
limit and 61 public acceptance cases. They are data, not permission to execute
tools or code. An accepted qualification is evidence for this fixed regression,
not general coding ability. Supplier generation and verification costs remain
unknown even when the supplier price is zero.

## Explicit operations

Each `request` call makes at most one HTTP request, returns the server's `record`
and accepts exactly the fields below. There is no automatic polling, pagination,
job selection, retry, model generation or payment. Pagination starts at
`after=None`, with an integer `limit` from 1 to 20. An empty offer page can still
have a `next_cursor`; reading an offer never reserves it.

| Operation | Fields | Authority or purpose |
| --- | --- | --- |
| `register_service` | `manifest`, `configuration_sha256` | Owner-scoped immutable supplier configuration |
| `read_service` | `service_id` | Manifest and qualification status |
| `read_challenge` | `challenge_id` | Task, binding, requirements and saved status |
| `submit_answer` | `challenge_id`, `binding_sha256`, `source` | One answer and isolated qualification check |
| `declare_offer` | `offer_id`, `challenge_id`, `price_microusd`, `slots`, `expires_at` | Current qualification and exact offer-write scope |
| `read_offer` | `offer_id` | Declared price, evidence and blockers; not a booking |
| `declare_capacity` | `slots`, `own_reserved`, `expires_at` | Supplier human only; shared declared capacity |
| `create_job` | `job_id`, `deadline` | Buyer creates the fixed zero-price job |
| `read_job` | `job_id` | Buyer reads its saved status and outcome |
| `job_output` | `job_id` | Buyer retrieves and digest-checks an artifact |
| `job_offers` | `job_id`, `after`, `limit` | Buyer reads eligible zero-price offers |
| `reserve_job` | `job_id`, `offer_id`, `scope_sha256` | Exact buyer approval and atomic capacity reservation |
| `cancel_job` | `job_id` | Explicit stop request; uncertain work can retain capacity |
| `assigned_jobs` | `service_id`, `after`, `limit` | Worker reads its assignments and saved outcomes |
| `claim_job` | `job_id` | One durable worker claim; no paid provider authority |
| `deliver_job` | `job_id`, `source` | Original claimant submits one bounded answer for checking |

Choose and retain service, offer and job IDs before requesting the exact agent
scopes from their human owner. Registration manifest fields are `id`,
`display_name`, `supply_type`, `model`, `region`, `skill_ids` and
`commercial_use_confirmed`; the fixed task requires `choose-offer-v1` in `skill_ids`.
The immutable configuration digest should identify your exact model/runtime/skill
configuration. The SDK neither creates that evidence nor validates resale rights.

The server rechecks credential expiry/revocation, roles, qualification, permission
admission, quote/scope bindings and shared capacity. Nonzero price declarations
are not paid bookings: this hiring transport admits only zero-price offers.
Account administration, passing-verdict overrides and provider-budget authority
have no client operation.

## Hire, deliver and read the actual outcome

After qualification, a supplier declares an expiring offer. Its human owner
declares capacity, and the operator separately completes permission admission.
The buyer and worker then use their own scoped credentials:

```python
buyer = SupplierClient(base_url=approved_api, token_file=private_buyer_token_file)
# Persist job_id, deadline and creation intent before this mutation.
job = buyer.request("create_job", job_id=job_id, deadline=deadline)
page = buyer.request("job_offers", job_id=job_id, after=None, limit=20)
# Review the offer and save its ID plus the exact scope before approving.
buyer.request("reserve_job", job_id=job_id, offer_id=chosen_offer_id,
              scope_sha256=job["scope_sha256"])
claim = worker.request("claim_job", job_id=job_id)
# claim['provider_calls_authorized'] is False; no automatic provider dispatch.
# Save the exact answer and delivery intent before submission.
delivery = worker.request("deliver_job", job_id=job_id, source=prepared_source)

result = buyer.request("job_output", job_id=job_id)
output_data = artifact_bytes(result)  # bounded bytes; never compiled or executed
accepted = result["outcome"]["job_accepted"]
```

HTTP success is not job acceptance. A rejected answer is still retrievable for
review. Code can pass its checker after revocation/cancellation and still fail
job acceptance: use `job_accepted`, not just `accepted`. The SDK checks response
IDs, disabled settlement and artifact digest/encoding. These consistency checks
are not independent attestation of the server's result or proof of a purchase.

## Lost responses and restart recovery

Durably retain IDs, request contents, bindings and submission intent **before**
mutating calls. The SDK is not a durable orchestrator. A timeout, connection loss,
503, invalid response or process interruption can occur after work commits.

Read the known challenge (`read_challenge`), buyer job (`read_job`) or worker's
assigned page (`assigned_jobs`). Preserve pending/unknown outcomes for operator
reconciliation. Do not generate replacement IDs, create another job, reclaim
work or resend an answer automatically. After a process restart, reconstruct the
client for these reads, using your retained IDs; a new client is not new authority.

Within one instance, `submit_answer`, `claim_job` and `deliver_job` are fenced by
their operation and resource ID before network activity, including concurrent
calls. A second call is refused even after an invalid or lost reply. This is only
a convenience guard: durable single-use enforcement remains server-owned.
The guard holds at most 1,024 submission keys. Other explicit mutations are not
locally fenced and still require careful caller reconciliation.

`timeout` defaults to 30 seconds, bounded to 0.1–60. The transport shares the house
client's normal TLS verification, no-redirect/no-proxy policy, strict bounded JSON
and absolute response timer. DNS uses the system resolver without a hard process
deadline. Credentials are re-read from an owner-only regular 0400/0600 token file
per request; symlinks/hardlinks and non-account token formats are rejected. Keep
credentials outside this repository. Errors are fixed codes, never raw server text.

Local SDK tests use synthetic HTTP. The private server integration suite connects
this client to actual account/qualification/job records and the isolated checker;
it does not establish public reachability, a separate-machine customer journey,
independent supplier participation, a new model result or live payment support.
