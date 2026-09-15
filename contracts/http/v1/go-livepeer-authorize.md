# go-livepeer authorization compatibility v1

The normative wire schema is the
`POST /v1/compat/go-livepeer/authorize` operation in `contracts/openapi.yaml`.
It is pinned to go-livepeer commit
`v0.9.2` (`38eb47d12ab1d2d874fc4c7c061aa1900b7c0bad`).

- Authenticate the signer connection with the configured bearer webhook secret.
  Headers nested in the JSON body are forwarded gateway input and do not
  authenticate the signer.
- The body is `{headers, state}`. The core consumes the Go state fields
  `StateID`, `PMSessionID`, `OrchestratorAddress`, `InitialPricePerUnit`,
  `InitialPixelsPerUnit`, `SequenceNumber`, `Type`, `LastUpdate`, and optional `AuthID`; other pinned
  go-livepeer state fields are ignored.
- Valid application decisions always use transport HTTP 200. The JSON `status`
  is the status go-livepeer returns to its caller. A non-200 callback transport
  status is treated by go-livepeer as an internal failure.
- Every successful decision returns a clearinghouse-controlled, stable opaque
  `auth_id` and `expiry: 0`. Omitting the identity permits fallback to an
  untrusted forwarded `Signer-Auth-Id`; nonzero expiry bypasses callbacks until
  cached authorization expires.
- Authorization evaluates the global stop, workload credential, workload
  status and expiry, frozen price ceiling, selected orchestrator, and existing
  state binding in one store transaction. The first successful call binds the
  workload and authorization identity to `StateID`; a later call for another
  state fails closed. The core does not move funds or maintain custody balances.
- When a workload has `max_spend_wei`, each callback reserves conservative
  exposure before the signer returns tickets. Fixed work reserves one unit;
  initial live and LV2V states reserve the pinned 10-second and 60-second
  preload respectively; later states use the signed state timestamp delta.
  Missing timestamps, gaps, stale sequences, and concurrent state forks fail
  closed. An exact retry of the latest sequence returns the same decision
  without increasing exposure.
- The ceiling is not an account balance and does not move funds. Authorization
  requires `max(attributed signer fee, cumulative authorized fee) + new fee`
  to be no greater than the immutable workload ceiling. Successful decisions
  continue to return `expiry: 0` so every payment request is evaluated.
