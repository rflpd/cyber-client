# Contributing

Keep changes focused on the documented inference client. Include offline tests for
authentication, retries, parsing or request-flow changes. Test fixtures must be
synthetic and must not call paid APIs, run models or move funds.

Do not commit credentials, private logs, model weights or copies of customer data.
Run `python3 -m unittest discover -s tests -v` before proposing changes.

Avoid claiming production readiness or model correctness. New integrations should
document their exact supported service and current limitations.
