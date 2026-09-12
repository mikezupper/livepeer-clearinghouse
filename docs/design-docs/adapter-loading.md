# Adapter composition

Ports describe capabilities; annotations do not load deployments. The reference composition root explicitly constructs SQLite storage, HTTP discovery, Resend delivery, optional Authlib OAuth, and the Kafka consumer.

An enterprise deployment may provide a different adapter package, but its composition root must import and instantiate that reviewed package directly. Environment variables select configuration values, not arbitrary Python modules. Storage replacements pass the core conformance suite; discovery and identity replacements preserve the versioned port contracts.
