# go-livepeer authorization compatibility v1

The normative wire schema is the
`POST /v1/compat/go-livepeer/authorize` operation in `contracts/openapi.yaml`.
It is pinned to go-livepeer commit
`e8dcf7a34744d5cb6b65ba43c0d9160a3975ccc6`.

- Authenticate the signer connection using configured callback headers or
  mTLS. Headers nested in the JSON body are forwarded gateway input and do not
  authenticate the signer.
- The body is `{headers, state}`. State property names preserve Go's PascalCase
  spelling. `LastUpdate` must retain RFC3339Nano precision; `Balance` is the
  callback state's exact rational string and is not the Kafka event's rounded
  `session_balance`.
- Valid application decisions always use transport HTTP 200. The JSON `status`
  is the status go-livepeer returns to its caller. A non-200 callback transport
  status is treated by go-livepeer as an internal failure.
- Every successful decision returns a clearinghouse-controlled, stable opaque
  `auth_id` and `expiry: 0`. Omitting the identity permits fallback to an
  untrusted forwarded `Signer-Auth-Id`; nonzero expiry bypasses callbacks until
  cached authorization expires.
- The reference adapter accepts only `live`, `lv2v`, and `fixed` state types.
  Unsupported or ambiguous shapes are denied in the JSON response.
- Authorization serializes reservation identity
  `(signer_id, StateID, SequenceNumber)` and atomically moves a conservative
  amount from lease available value to pending value before allowing signing.
