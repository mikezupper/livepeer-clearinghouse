# Billing qualification

Billing qualification is an operator-invoked development exercise against a
staged Clearinghouse, the official local `livepeer-python-gateway` checkout,
and—only for explicitly selected cases—the public Livepeer network. It is not a
CI gate.

## Safety model

Copy `qualification.env.example` to the ignored `qualification.env`, set mode
`0600`, and configure the API credential. The checked-in defaults permit at
most `1000000000000` wei across the selected runnable cases,
`100000000000` wei per case, and 30 seconds per case. A case also has its own
price ratio, quantity, orchestrator allowlist, duration, and maximum authorized
amount. All rational comparisons use integers. Planning creates no workload.

Signer deposit and reserve values are collateral, not usage expenditure, and
are intentionally outside the qualification fee total.

## Case matrix

| Case | Boundary | Expected behavior |
|---|---|---|
| `persistent-short` | Live network | One short Flux Klein reservation and at least one matched ticket event. |
| `persistent-multi-cycle` | Live network | A 25-second reservation with at least two distinct event sequences. |
| `persistent-interrupted` | Live network | The suite terminates only its probe, allows in-flight delivery, proves funding stabilizes after a bounded grace period, and revokes the workload. |
| `fixed-success` | Live network | One fixed request and one fixed unit; requires a reviewed JSON payload. |
| `fixed-charged-failure` | Controlled | Application failure after payment retains one charge; rejection before payment creates none. |
| `runtime-price-policy` | Controlled | Equal/lower exact prices pass and any higher rational price fails before usage. |
| `signer-event-replay` | Controlled | Exact duplicates are idempotent, delayed valid events match, and foreign events remain unmatched. |
| `lv2v-pixel-accounting` | Controlled | Pixels reconcile against the immutable pixel-second quote and the wrong unit is rejected. |
| `lv2v-live` | Live network | Runs only with authoritative inventory, an explicit model/media fixture, pixel ceiling, and opt-in. |

Controlled scenarios use temporary SQLite databases and the production domain,
application, event-decoding, and storage components. They do not claim to have
tested public orchestrator behavior. Public cases use the real gateway package
and are reported as not runnable when inventory or required inputs are absent.
The broker target additionally exercises the running Redpanda service and core
consumer with one duplicate and one delayed event belonging to its own
short-lived workload. Event hashes and producer offsets are retained; raw
payment material is not.

## Operation

```sh
make qualify-billing-plan
make qualify-billing-controlled
make qualify-billing-broker
QUAL_EXECUTE=true make qualify-billing-run CASES=persistent-short
make qualify-billing-report
```

`CASES` is mandatory for execution and accepts a comma-separated list. The
legacy `make qualify-gateway` target remains available for a single manually
configured reservation.

For fixed execution, set `QUAL_FIXED_PAYLOAD_JSON` to a reviewed request body
appropriate for the selected capability. Never guess a third-party request
schema. For live LV2V, also set `QUAL_LV2V_INPUT`, `QUAL_LV2V_MODEL`, and
`QUALIFICATION_ALLOW_LV2V=true`; the case remains not runnable until a matching
authoritative price is present. The driver publishes decoded frames only up to
the manifest pixel ceiling and refuses an input that cannot produce a frame
within that bound.

## Evidence and interpretation

Evidence is written beneath `QUAL_EVIDENCE_ROOT` (`tmp/qualification` by
default):

- `plan-latest.json` records availability and maximum authorization before mutation.
- `controlled-<case>.json` records the exact deterministic test node and result.
- `gateway-<case>-<timestamp>.json` records sanitized live offer, session, usage, cost, and cleanup evidence.
- `report-latest.md` summarizes the latest result per case and aggregate observed fees.

Tokens, API credentials, and payment headers are never recorded. Workloads are
revoked after live evidence is collected. A live result passes only when usage
is matched, event sequences are unique and monotonic, quote-derived cost exactly
matches the measured quantity, and the cost aggregate includes every observed
event.

`quoted_fee` is the advertised price applied to measured usage.
`computed_fee` is the signer-reported amount. Ticket funding can be granular,
so the values may differ; that delta is evidence, not silently normalized away.
