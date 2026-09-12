# Remote signer operations

The default stack uses the unmodified upstream go-livepeer image pinned by digest and commit in `deploy/signer/Dockerfile`.

Required inputs are network/chain/controller, RPC URL, funded signer address, matching encrypted V3 keystore, password file, webhook secret, funding floors, broker topic, and discovery configuration. `make signer-preflight` validates them without signing or moving funds.

Suggested evaluation floors are 0.001 ETH for `SIGNER_MIN_GAS_WEI` and `1` wei for both deposit and reserve. Production floors are operator risk decisions and should reflect expected request volume and replenishment time.

For local Redpanda leave `LP_KAFKAUSER` and `LP_KAFKAPASSWORD` blank. An external authenticated broker requires both. Keep the topic dedicated to this signer identity.

Static discovery requires `SIGNER_REMOTE_DISCOVERY=true` plus comma-separated `SIGNER_ORCH_ADDR` service endpoints with explicit ports. Generated SDK access automatically pins the service endpoint belonging to the workload's selected offer.

The signer exposes only its public protocol through edge. Never expose loopback admin port 4935. The current upstream binary may log the full RPC URL; avoid embedded credentials and protect logs. An optional Bead tracks adopting upstream `ethUrl` redaction if/when available.

Stop new signing with the admin Operations global stop before custody, RPC, controller, or broker maintenance. Existing upstream operations may still have protocol effects; the Clearinghouse stop governs new callback authorization only.
