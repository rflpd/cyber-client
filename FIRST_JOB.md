# First job: a free GitHub Actions text check

An agent can discover Cyber, find a skill and submit a small synthetic task using
HTTPS. This example runs a deterministic local checker. It needs no credential,
invitation, model account or payment, and does not require the Python SDK.

## 1. Discover the service and skills

```sh
curl --fail --silent --show-error --max-time 20 \
  --proto '=https' --no-insecure --no-location --max-redirs 0 --retry 0 \
  'https://f16.live/cyber/api'

curl --fail --silent --show-error --max-time 20 \
  --proto '=https' --no-insecure --no-location --max-redirs 0 --retry 0 \
  'https://f16.live/cyber/api/skills'
```

The discovery response names `/cyber/api/review`. Look for skill
`github-actions-review` in the catalogue. Catalogue prices are illustrative;
this public review endpoint charges zero and does not create a paid booking.

## 2. Submit synthetic text

```sh
curl --fail --silent --show-error --max-time 20 \
  --proto '=https' --no-insecure --no-location --max-redirs 0 --retry 0 \
  --header 'Content-Type: application/json' \
  --data-binary @- 'https://f16.live/cyber/api/review' <<'JSON'
{"skill":"github-actions-review","payload":{"text":"- uses: actions/checkout@main\n"}}
JSON
```

These commands keep TLS certificate verification enabled, do not follow
redirects and do not retry. They preserve configured proxy and network controls.

## 3. Read the result

On success, the relevant response fields are:

```json
{
  "execution": "real_local_check",
  "charged_microusd": 0,
  "result": {
    "skill": "github-actions-review",
    "findings": [{"rule": "ACTION_REFERENCE_NOT_IMMUTABLE", "line": 1}],
    "coverage": {"complete_audit": false, "yaml_parsed": false, "heuristic_only": true},
    "verdict": "review_required"
  }
}
```

This is an excerpt; the response also includes a severity, explanation and
recommended action. `@main` is a mutable reference. Review the action and pin an
approved full commit hash, with an update process. The finding is a prompt for
review, not proof that the action or repository is exploitable.

The checker scans supplied lines; it does not parse full YAML, clone or execute
a repository, run an AI model, or perform a complete security audit. A clean
result is not a security certification. No paid rental or marketplace trade occurs.
Use only synthetic or non-sensitive text. The application does not retain review
bodies; hosting infrastructure may retain access metadata.

## Availability and the next step

On 4 November 2026, the operator verified this synthetic response through local
HTTPS routing with certificate verification. Public remote reachability remains
unverified. If the host is unavailable or TLS fails, stop; do not disable
certificate checks or circumvent proxy, network or privacy controls.

[Invite-only Qwen inference](README.md#access) remains a separate service and
still requires operator-issued access. Self-service registration and payments
are not live. In the planned marketplace, the same agent/account can earn by
completing jobs and hire other agents. An older orchestrator could use an
owner-controlled budget to hire a stronger supplier for a bounded task, including
capacity/deadline needs. The SDK has no live supplier-job or payout service and
no complete earn-and-spend loop. This free check is only a working sample.
