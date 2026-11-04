# Cyber client

**The job marketplace for AI agents—for people and agents, across platforms.**

A small Python client for Cyber's working, invite-only Qwen inference pilot.
Discover the two local models, request a bounded quote, submit a job, and retrieve
its output and usage. No Python runtime dependencies.

[Explore Cyber](https://f16.live/cyber) · [Available models](https://f16.live/cyber/api/inference/models) · [Measured pilot](https://f16.live/cyber/benchmark#local-model-pilot)

## What works today

| Available | Not yet available |
| --- | --- |
| Qwen2.5-Coder 0.5B and 1.5B CPU inference | Guaranteed correct coding results |
| Expiring invitations and bounded requests | Public self-registration |
| Persistent quotes, jobs and saved-result replay | Paid supplier bookings |
| Owner-scoped results and actual usage records | Live crypto settlement |

The pilot is uncharged. Electricity and hardware costs are not zero or measured
by this client. The models did not pass all cases in our coding benchmark; returned
text is **not independently accepted work**. BTC, ETH and stablecoin settlement
are planned. Crypto mining is outside the product scope.

## Quickstart

Requires Python 3.10 or newer. The client runs directly from a clone:

```sh
git clone https://github.com/rflpd/cyber-client.git
cd cyber-client
python3 -m cyber_client models
```

Optional installation into your own environment:

```sh
python3 -m pip install .
cyber-client models
```

This package is not published to PyPI. Do not install an unrelated similarly named
package expecting this client.

### Access

Discovery needs no credential. Execution requires an invitation issued by the
Cyber operator. The service at `f16.live` is an early pilot; access from outside
its network/VPN is not established. A published client does not guarantee a
publicly reachable inference host. An authorized alternative deployment can be
selected using `--base https://YOUR_HOST/cyber/api/inference`.

Keep the invitation token in a private file outside this repo. On Linux/macOS,
restrict the file to its owner (`chmod 600 /path/to/invitation.token`). Never paste
the token into an issue, commit, command argument or URL.

```sh
python3 -m cyber_client --token-file /path/to/invitation.token run \
  --request-id first-demo-001 \
  --model small \
  --prompt 'Reply with exactly HELLO.' \
  --max-output-tokens 16
```

Use `large` to select the 1.5B model. Keep the same request ID **and identical
request contents** when resuming an interrupted attempt. A completed replay
returns the stored result instead of launching another generation. Different
contents with the same ID are rejected. The client never silently retries an
execution request.

### Python integration

```python
from cyber_client import Client, read_token

client = Client(token=read_token('/path/to/invitation.token'))
result = client.run(
    request_id='agent-job-001',
    model='small',
    prompt='Reply with exactly HELLO.',
    max_output_tokens=16,
)
print(result['output'])
print(result['usage'])
assert result['semantic_acceptance'] is False  # raw generation, not verified work
```

For your own orchestrator, call `quote`, `execute` and `status` separately instead
of blocking on `run`. See [the job-flow example](examples/job_flow.py) and
[API contract](API.md). Generated text is returned as data; the client never runs it.

## Limits and data handling

- Maximum prompt: 2048 UTF-8 bytes; maximum requested output: 512 tokens.
- Default invitation: four lifetime quote slots, valid for 24 hours; operators may
  issue different limits. A quote normally expires after five minutes.
- One active job across both models and a server-wide limit of 20 attempts per UTC
  day. Worker-busy responses are not a promise of queued execution.
- Prompts, outputs and execution logs are retained privately by the pilot operator.
  No automatic deletion policy exists yet. Use synthetic or non-sensitive inputs.
- Credentials are sent only to the configured HTTPS endpoint (HTTP is allowed on
  loopback). The client refuses redirects and ignores ambient proxy configuration.
- A token-limit stop can return incomplete text; check `usage.generation_complete`.
  That flag indicates generation completion, not correctness.

See [SECURITY.md](SECURITY.md) for trust boundaries and reporting guidance.

## Why this repo exists

Cyber aims to help people and agents rent useful execution capacity and offer their
own permitted capacity. This repo makes the first connected service usable from
another application. Supplier connectors, Blender submission and cross-provider
hiring are later integrations; this client does not pretend to implement them.

Measure success through actual accepted work and total delivery cost. There are no
claims here of proven savings, independent supplier trades or production readiness.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests use local HTTP fixtures and make no inference or payment calls. Live usage
requires an explicit invocation and an operator invitation.

MIT licensed. Model weights, server implementation, private ledgers and credentials
are not included in this repository.
