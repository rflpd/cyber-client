# Security and trust boundaries

This is an early client for an invitation-only service. It is not a production
security certification or a wallet SDK.

- Use only a Cyber deployment you trust. The configured server receives prompts
  and, on authenticated requests, your invitation credential.
- HTTPS is mandatory except for loopback development. Redirects are refused so
  credentials cannot follow a redirected request to another origin.
- Keep tokens in owner-only files outside the checkout. The CLI prints error codes,
  not credentials or raw network exceptions. Do not put secrets in job prompts.
- The service retains prompts and outputs privately; it has no automatic retention
  guarantee. Use synthetic inputs in the current pilot.
- Returned text can be incorrect or malicious. It is never executed by this client.
  Independent verification and scoped execution are the caller's responsibility.
- A successful response is not an accepted coding task or payment receipt.
- Polling is bounded. Execution requests are not automatically retried after an
  ambiguous network failure. Reuse the same request ID and consult job status.

Never post tokens, customer inputs, wallet secrets or exploit details in public
issues. Use GitHub private vulnerability reporting if enabled for this repository;
otherwise request a private reporting channel without disclosing sensitive details.
