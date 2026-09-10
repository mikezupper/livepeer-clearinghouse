# Adapter loading and deployment

## Decision

Ports are typed protocols wired explicitly during process startup. Decorator or
annotation scanning is prohibited because it hides dependencies and makes
startup order difficult to reason about.

The core supports three adapter sources:

1. Built-in adapters selected by stable names.
2. Trusted Python distributions registered through versioned package entry
   points and installed into an immutable derived image.
3. Built-in HTTP bridge adapters for implementations in other languages.

The loader reads validated settings, resolves the configured adapter, checks its
contract version and capabilities, constructs it once, and injects the complete
adapter set into workflows. Arbitrary dotted imports from environment variables
are not allowed.

## Reference adapter set

| Port | Reference adapter |
| --- | --- |
| Identity | Local accounts, email OTP, optional Google/GitHub verification |
| Signer | go-livepeer HTTP and discovery |
| Custody | Encrypted file keystore mounted into the signer container |
| Metering | Redpanda input normalized into PostgreSQL usage |
| Pricing | Static database-backed rate cards |
| Collection | Audited operator grants |
| Events out | Signed webhook delivery plus structured local output |

PostgreSQL is the walking slice's fixed store implementation.

## Pymthouse deployment

Pymthouse remains a separate product. Its TypeScript integrations run as an
adapter service and the core selects HTTP bridge adapters with environment
configuration. A native Python adapter bundle may instead be installed into a
derived image, but is never added to the reference image.

```text
pymthouse product ──> clearinghouse public/admin API
        │
        └── pymthouse adapter service <── HTTP bridge ports ── core
```

Adapter repositories run the published conformance kit in CI. The core fails
closed if a configured remote adapter is unavailable.
