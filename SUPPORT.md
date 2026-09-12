# Support

Open Clearinghouse is community-maintained software. Support is best effort and
does not include an uptime or response-time commitment.

Before asking for help, check the [documentation index](docs/index.md),
[deployment runbook](docs/operations/deployment.md), and existing public issue
reports. Remove credentials, raw OTPs, signer keys, wallet material, and
personal or production data from every diagnostic.

Use the appropriate channel:

- reproducible clearinghouse defects: [structured bug
  report](https://github.com/livepeer/clearinghouse/issues/new?template=bug_report.yml);
- scoped feature proposals: [structured feature
  proposal](https://github.com/livepeer/clearinghouse/issues/new?template=feature_request.yml);
- architecture and operator questions: [Livepeer
  Forum](https://forum.livepeer.org/);
- community conversation: [Livepeer Discord](https://discord.gg/55SZFEEH5y);
- suspected vulnerabilities: [private vulnerability
  reporting](https://github.com/livepeer/clearinghouse/security/advisories/new).

GitHub Issues are an intake channel, not the project's implementation tracker.
Maintainers triage accepted work into Beads. Questions about a private or
modified deployment remain the deploying operator's responsibility; include a
minimal reproduction against the unmodified reference distribution when
possible.

The supported reference profile is the documented single-node distribution:
edge, one core process backed by SQLite, Redpanda, and the unmodified pinned
remote signer. High-availability layouts, external storage adapters, billing or
organization services, and other enterprise extensions are owned and supported
by the deployment that adds them.
