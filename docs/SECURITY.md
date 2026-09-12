# Security design

The edge is the only host-published service. Core, Redpanda, and signer traffic remain on the private Compose network. Production terminates HTTPS before or at Caddy and sets secure cookies.

OTP, session, API-credential, OAuth-state, nonce, and workload secrets are never stored in plaintext. Browser sessions use HTTP-only same-site cookies; mutations additionally require exact Origin and a matching CSRF cookie/header. OAuth providers are disabled unless complete credentials are present. Admin access is tied to the normalized configured admin email.

The signer callback uses an independent deployment secret. Signer custody uses an encrypted Ethereum V3 key and separate password file mounted read-only from outside the repository. The signer wrapper validates permissions, ownership, addresses, network, webhook, broker, and static orchestrator inputs before dropping privileges. The pinned upstream signer may expose `ethUrl` in logs, so credential-free RPC URLs are recommended until optional upstream redaction work lands.

SQLite and Kafka values are treated as untrusted at their read boundaries. Exact prices avoid float rounding. Unknown signer events are retained as unmatched evidence rather than silently attributed. The core reports only signer events it has received; it does not claim billing completeness when the upstream asynchronous Kafka producer loses an event.

The reference profile is single-node and does not include an Internet-facing rate limiter, automated backup service, or long-term audit system. Production operators must provide request and concurrency limits at the edge, protect and back up the SQLite and custody volumes, monitor core/broker/signer health and unmatched usage, and keep signer funding within their risk tolerance. Separately packaged storage and enterprise services have their own security and support boundary.

Report vulnerabilities using the private process in the repository root [SECURITY.md](../SECURITY.md). Do not open public issues containing secrets or exploit details.
