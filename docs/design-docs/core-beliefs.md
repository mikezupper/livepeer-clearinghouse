# Core beliefs

1. A workload quote is an immutable observation, not a mutable rate card.
2. Authorization fails closed and never requires wallet custody in the client.
3. Signer `auth_id` is the workload identity used for later attribution.
4. Measured quote cost and signer-reported fee are separate facts.
5. Kafka is transport evidence; SQLite is the bundled ownership/quote authority.
6. Exact integer ratios replace floating-point money arithmetic.
7. Secrets are returned once and stored only as keyed digests.
8. Extensions depend on versioned contracts and ports, not infrastructure internals.
9. The default deployment stays operable as four long-running services.
10. Enterprise features may surround the core without changing these invariants.
