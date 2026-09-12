# livepeer-python-gateway compatibility contract

The Clearinghouse returns a base64-encoded JSON SDK token containing:

- `signer`: the public origin that exposes the pinned go-livepeer signer endpoints;
- `discovery`: the Clearinghouse runner-discovery endpoint;
- `signer_headers.Authorization`: `Bearer <one-time workload secret>`; and
- `discovery_headers.Authorization`: the same workload credential.

An optional `orchestrators` string array selects static orchestrators using the
SDK's existing highest-priority discovery input. No SDK fork or custom runner
transport is required. `livepeer_gateway.parse_token()` decodes this shape, and
`runner_selector()`, `reserve_session()`, and `call_runner()` already forward
these signer/discovery headers through live-runner payment flows.

The Clearinghouse workload ID is the go-livepeer `auth_id`. The runner's payment
challenge supplies the manifest/payment session identifiers later observed on
the `create_signed_ticket` Kafka event. This gives the core enough correlation
without adding a Clearinghouse-specific header to the workload data plane.
