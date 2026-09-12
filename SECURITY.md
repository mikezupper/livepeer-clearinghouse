# Security policy

Security reports are welcome and should be made privately.

## Supported versions

Until the project publishes a stable release, the current `main` branch and the
most recent tagged release receive security fixes. Older snapshots and forks
are unsupported. Deployment operators should use immutable released artifacts
and apply security updates promptly.

## Report a vulnerability

Use [GitHub private vulnerability
reporting](https://github.com/livepeer/clearinghouse/security/advisories/new).
Do not open a public issue, discussion, or pull request for an undisclosed
vulnerability.

Include the affected version or commit, deployment context, impact, minimal
reproduction steps, and any suggested mitigation. Remove secrets, signer keys,
raw OTPs, personal data, and production credentials from the report. If a
proof of concept could affect a live network or third party, describe it rather
than running it without authorization.

Maintainers will acknowledge the report through the private advisory, assess
severity and affected versions, coordinate a fix and disclosure, and credit the
reporter when requested and appropriate. Response and remediation timing depend
on impact and complexity; the advisory remains the authoritative private
communication channel.

## Security boundaries

The reference deployment's trust boundaries, custody rules, authentication
model, and operational assumptions are documented in the
[security model](docs/SECURITY.md) and [threat
model](docs/security/threat-model.md). Vulnerabilities in upstream go-livepeer,
SQLite, Redpanda, browsers, or identity providers should also be reported to the
responsible upstream project. Separately packaged storage or enterprise
extensions are supported by their operators and upstream maintainers, not by
the reference core's security process unless the fault also exists in a public
Clearinghouse contract or implementation.

This policy does not authorize access to data or systems you do not own,
service disruption, social engineering, physical testing, or retention of
sensitive data. Good-faith research should minimize harm and stop when it
encounters real user or credential data.
