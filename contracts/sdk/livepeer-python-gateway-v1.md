# livepeer-python-gateway workload SDK token contract

After an authenticated browser session or account API credential creates a
quoted workload, the Clearinghouse returns its base64-encoded JSON workload SDK
token once. The token contains:

- `signer`: the public origin that exposes the pinned go-livepeer signer endpoints;
- `discovery`: the Clearinghouse runner-discovery endpoint;
- `signer_headers.Authorization`: `Bearer <one-time workload secret>`; and
- `discovery_headers.Authorization`: the same workload credential.

The workload SDK token is data-plane configuration for this workload only. It
cannot authenticate account-level Clearinghouse operations or create another
workload. The embedded credential stops authorizing when the workload expires
or is revoked. Clients must save the encoded value when it is returned because
the Clearinghouse stores only keyed digests and cannot display it again.

The workload may also carry an immutable `max_spend_wei` created through the
Clearinghouse API. It is intentionally not embedded as a client-editable SDK
token claim. The Clearinghouse enforces it on every signer callback by tracking
authorized exposure and later reconciling signer events; no gateway change is
required.

An optional `orchestrators` string array selects static orchestrators using the
SDK's existing highest-priority discovery input. No SDK fork or custom runner
transport is required. `livepeer_gateway.parse_token()` decodes this shape, and
`runner_selector()`, `reserve_session()`, and `call_runner()` already forward
these signer/discovery headers through live-runner payment flows.

The Clearinghouse workload ID is the go-livepeer `auth_id`. The runner's payment
challenge supplies the manifest/payment session identifiers later observed on
the `create_signed_ticket` Kafka event. This gives the core enough correlation
without adding a Clearinghouse-specific header to the workload data plane.
