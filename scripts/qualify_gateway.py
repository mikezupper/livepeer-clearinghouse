#!/usr/bin/env python3
"""Run a guarded, ad-hoc Clearinghouse and Python gateway qualification."""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = ROOT / "tmp" / "qualification"


class QualificationError(RuntimeError):
    """A safe, operator-actionable qualification failure."""


@dataclass(frozen=True, slots=True)
class Settings:
    clearinghouse_url: str
    gateway_root: Path
    api_credential: str
    capability: str
    model: str | None
    orchestrator: str | None
    currency: str
    unit: str
    max_numerator: int
    max_denominator: int
    workload_ttl_seconds: int
    execution_timeout_seconds: float
    metering_timeout_seconds: float
    session_hold_seconds: float
    allow_stale: bool
    execute: bool
    evidence_root: Path
    case_id: str = "persistent-short"
    action: str = "reserve"
    request_payload: dict[str, object] | None = None
    minimum_events: int = 1
    max_spend_wei: int = 100_000_000_000

    @classmethod
    def from_values(cls, values: dict[str, str]) -> Settings:
        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise QualificationError(f"{name} is required")
            return value

        credential = required("QUAL_API_CREDENTIAL")
        if not credential.startswith("och_live_"):
            raise QualificationError("QUAL_API_CREDENTIAL must start with och_live_")
        gateway_root = Path(required("QUAL_GATEWAY_ROOT")).expanduser().resolve()
        if not (gateway_root / "pyproject.toml").is_file():
            raise QualificationError("QUAL_GATEWAY_ROOT must contain pyproject.toml")
        max_denominator = _positive_int(values, "QUAL_MAX_PRICE_DENOMINATOR", 1)
        clearinghouse_url = required("QUAL_CLEARINGHOUSE_URL").rstrip("/")
        if urlparse(clearinghouse_url).scheme not in {"http", "https"}:
            raise QualificationError("QUAL_CLEARINGHOUSE_URL must use http or https")
        return cls(
            clearinghouse_url=clearinghouse_url,
            gateway_root=gateway_root,
            api_credential=credential,
            capability=required("QUAL_CAPABILITY"),
            model=_optional(values, "QUAL_MODEL"),
            orchestrator=_optional(values, "QUAL_ORCHESTRATOR"),
            currency=required("QUAL_EXPECT_CURRENCY").lower(),
            unit=required("QUAL_EXPECT_UNIT").lower(),
            max_numerator=_positive_int(values, "QUAL_MAX_PRICE_NUMERATOR"),
            max_denominator=max_denominator,
            workload_ttl_seconds=_positive_int(values, "QUAL_WORKLOAD_TTL_SECONDS", 600),
            execution_timeout_seconds=_positive_float(
                values, "QUAL_EXECUTION_TIMEOUT_SECONDS", 120
            ),
            metering_timeout_seconds=_positive_float(values, "QUAL_METERING_TIMEOUT_SECONDS", 120),
            session_hold_seconds=_non_negative_float(values, "QUAL_SESSION_HOLD_SECONDS", 2),
            allow_stale=_boolean(values, "QUAL_ALLOW_STALE_DISCOVERY", False),
            execute=_boolean(values, "QUAL_EXECUTE", False),
            evidence_root=Path(
                values.get("QUAL_EVIDENCE_ROOT", str(DEFAULT_EVIDENCE_ROOT))
            ).expanduser(),
            case_id=values.get("QUAL_CASE_ID", "persistent-short").strip() or "persistent-short",
            action=values.get("QUAL_ACTION", "reserve").strip() or "reserve",
            request_payload=_json_object(values, "QUAL_REQUEST_PAYLOAD_JSON"),
            minimum_events=_positive_int(values, "QUAL_MINIMUM_EVENTS", 1),
            max_spend_wei=_positive_int(values, "QUAL_MAX_AUTHORIZED_WEI", 100_000_000_000),
        )


def load_env(path: Path) -> dict[str, str]:
    """Load the deliberately small KEY=VALUE subset used by qualification files."""

    if not path.is_file():
        raise QualificationError(f"qualification environment file not found: {path}")
    values = dict(os.environ)
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise QualificationError(f"invalid environment line {number}: expected KEY=VALUE")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "A").isalnum():
            raise QualificationError(f"invalid environment name on line {number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values.setdefault(name, value)
    return values


def select_offer(items: object, settings: Settings) -> dict[str, Any]:
    """Select exactly one explicitly bounded offer from a discovery response."""

    if not isinstance(items, list):
        raise QualificationError("offers response items must be a list")
    matches: list[dict[str, Any]] = []
    for candidate in items:
        if not isinstance(candidate, dict) or candidate.get("capability") != settings.capability:
            continue
        if settings.model is not None and candidate.get("model") != settings.model:
            continue
        constraints = candidate.get("constraints")
        orchestrator = (
            constraints.get("orchestrator_url") if isinstance(constraints, dict) else None
        )
        if settings.orchestrator is not None and orchestrator != settings.orchestrator:
            continue
        price = candidate.get("price")
        if not isinstance(price, dict):
            continue
        if str(price.get("currency", "")).lower() != settings.currency:
            continue
        if str(price.get("quantity_unit", "")).lower() != settings.unit:
            continue
        try:
            numerator = int(str(price["numerator"]))
            denominator = int(str(price["denominator"]))
        except KeyError, TypeError, ValueError:
            continue
        if denominator <= 0 or numerator < 0:
            continue
        if numerator * settings.max_denominator > settings.max_numerator * denominator:
            continue
        matches.append(candidate)
    if not matches:
        suffix = f" at {settings.orchestrator}" if settings.orchestrator else ""
        raise QualificationError(
            f"no safe {settings.capability} offer{suffix} matched the configured "
            "price and unit limits"
        )
    if len(matches) != 1:
        orchestrators = sorted(
            {
                str(candidate.get("constraints", {}).get("orchestrator_url", "unknown"))
                for candidate in matches
                if isinstance(candidate.get("constraints"), dict)
            }
        )
        raise QualificationError(
            "offer selection is ambiguous; set QUAL_ORCHESTRATOR to one of: "
            + ", ".join(orchestrators)
        )
    return matches[0]


def request_json(
    settings: Settings,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[Any, dict[str, str]]:
    """Call an authenticated Clearinghouse JSON endpoint."""

    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(  # noqa: S310 - Settings restricts the origin to HTTP(S)
        f"{settings.clearinghouse_url}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.api_credential}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urlopen(  # noqa: S310 - request URL was restricted to HTTP(S)
            request, timeout=settings.execution_timeout_seconds
        ) as response:
            raw = response.read()
            content = json.loads(raw) if raw else None
            return content, {name.lower(): value for name, value in response.headers.items()}
    except HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise QualificationError(f"{method} {path} returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise QualificationError(f"{method} {path} failed: {error}") from error


def run_gateway(settings: Settings, sdk_token: str) -> dict[str, Any]:
    """Run the real gateway environment without placing its token in argv."""

    probe = ROOT / "scripts" / "gateway_probe.py"
    uv = shutil.which("uv")
    if uv is None:
        raise QualificationError("uv is required to run the gateway probe")
    request = json.dumps(
        {
            "sdk_token": sdk_token,
            "capability": settings.capability,
            "timeout_seconds": settings.execution_timeout_seconds,
            "hold_seconds": settings.session_hold_seconds,
            "action": settings.action,
            "payload": settings.request_payload or {},
        }
    )
    if settings.action == "interrupt":
        return run_interrupted_gateway(settings, request, uv, probe)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed uv command and reviewed script
            [uv, "run", "python", str(probe)],
            cwd=settings.gateway_root,
            input=request,
            capture_output=True,
            text=True,
            timeout=settings.execution_timeout_seconds + settings.session_hold_seconds + 30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise QualificationError(f"gateway probe could not run: {error}") from error
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        stderr = completed.stderr[-1000:].strip()
        raise QualificationError(f"gateway probe returned invalid JSON: {stderr}") from error
    expected_status = {"call": "called", "lv2v": "lv2v"}.get(settings.action, "reserved")
    if (
        completed.returncode
        or not isinstance(result, dict)
        or result.get("status") != expected_status
    ):
        message = (
            result.get("error", "unknown gateway failure") if isinstance(result, dict) else result
        )
        raise QualificationError(f"gateway probe failed: {message}")
    return result


def run_interrupted_gateway(
    settings: Settings, request: str, uv: str, probe: Path
) -> dict[str, Any]:
    """Interrupt only the qualification-owned probe after reservation succeeds."""

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed uv command and reviewed script
            [uv, "run", "python", str(probe)],
            cwd=settings.gateway_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None:
            raise QualificationError("interrupted gateway probe pipes are unavailable")
        process.stdin.write(request)
        process.stdin.close()
        ready, _, _ = select.select([process.stdout], [], [], settings.execution_timeout_seconds)
        if not ready:
            raise QualificationError("interrupted gateway probe did not reserve before timeout")
        result = json.loads(process.stdout.readline())
        if not isinstance(result, dict) or result.get("status") != "reserved":
            raise QualificationError("interrupted gateway probe did not announce a reservation")
        time.sleep(settings.session_hold_seconds)
        os.killpg(process.pid, signal.SIGTERM)
        return_code = process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise QualificationError(f"interrupted gateway probe could not run: {error}") from error
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    result.update({"status": "interrupted", "exit_code": return_code})
    return result


def wait_for_usage(
    settings: Settings, workload_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Poll boundedly for an attributed usage event and its cost aggregate."""

    deadline = time.monotonic() + settings.metering_timeout_seconds
    while time.monotonic() < deadline:
        usage, _ = request_json(settings, "/v1/usage")
        costs, _ = request_json(settings, "/v1/costs")
        usage_items = usage.get("items", []) if isinstance(usage, dict) else []
        cost_items = costs.get("items", []) if isinstance(costs, dict) else []
        events = [
            item
            for item in usage_items
            if isinstance(item, dict) and item.get("workload_id") == workload_id
        ]
        cost = next(
            (
                item
                for item in cost_items
                if isinstance(item, dict)
                and isinstance(item.get("workload"), dict)
                and item["workload"].get("id") == workload_id
                and int(item.get("event_count", 0)) >= settings.minimum_events
            ),
            None,
        )
        if len(events) >= settings.minimum_events and isinstance(cost, dict):
            return events, cost
        time.sleep(2)
    raise QualificationError(
        f"no attributed usage and cost appeared for {workload_id} within "
        f"{settings.metering_timeout_seconds:g} seconds"
    )


def wait_for_interruption_stability(
    settings: Settings, workload_id: str, initial: dict[str, Any]
) -> dict[str, Any]:
    """Allow in-flight delivery, then prove the interrupted probe stopped funding."""

    def current() -> dict[str, Any]:
        costs, _ = request_json(settings, "/v1/costs")
        items = costs.get("items", []) if isinstance(costs, dict) else []
        found = next(
            (
                item
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("workload"), dict)
                and item["workload"].get("id") == workload_id
            ),
            None,
        )
        if not isinstance(found, dict):
            raise QualificationError("interrupted workload cost disappeared")
        return found

    time.sleep(12)
    settled = current()
    time.sleep(5)
    stable = current()
    if stable.get("event_count") != settled.get("event_count"):
        raise QualificationError("billing continued beyond the interruption grace period")
    if int(str(stable.get("event_count", 0))) < int(str(initial.get("event_count", 0))):
        raise QualificationError("interrupted workload cost regressed")
    return stable


def validate_accounting(
    event: dict[str, Any] | list[dict[str, Any]],
    cost: dict[str, Any],
    expected_ceiling: int | None = None,
) -> None:
    """Reject superficially attributed events whose quantities do not reconcile."""

    events = event if isinstance(event, list) else [event]
    try:
        quantities = [int(str(item["quantity"])) for item in events]
        computed_fees = [int(str(item["computed_fee"])) for item in events]
        quoted_fee = int(str(cost["quoted_fee"]))
        aggregate_fee = int(str(cost["computed_fee"]))
        event_count = int(str(cost["event_count"]))
    except (KeyError, TypeError, ValueError) as error:
        raise QualificationError(
            "usage or cost response contains invalid accounting values"
        ) from error
    if any(item.get("status") != "matched" for item in events):
        raise QualificationError("usage event was not matched to the workload")
    if any(value < 0 for value in (*quantities, *computed_fees, quoted_fee, aggregate_fee)):
        raise QualificationError("usage or cost response contains negative accounting values")
    if any(
        fee > 0 and quantity == 0 for fee, quantity in zip(computed_fees, quantities, strict=True)
    ):
        raise QualificationError("nonzero signer fee was attributed as zero measured usage")
    if sum(computed_fees) > 0 and quoted_fee == 0:
        raise QualificationError("nonzero signer fee produced zero quote-derived cost")
    if event_count < len(events) or aggregate_fee < sum(computed_fees):
        raise QualificationError(
            "workload cost aggregate does not include the observed usage event"
        )
    ticket_counts = [int(str(item["ticket_count"])) for item in events if "ticket_count" in item]
    if any(value < 1 for value in ticket_counts):
        raise QualificationError("usage event has an invalid ticket count")
    sequence_numbers = [
        int(str(item["sequence_number"])) for item in events if "sequence_number" in item
    ]
    if len(sequence_numbers) > 1 and (
        sequence_numbers not in (sorted(sequence_numbers), sorted(sequence_numbers, reverse=True))
        or len(sequence_numbers) != len(set(sequence_numbers))
    ):
        raise QualificationError("usage event sequence numbers are not strictly monotonic")
    workload = cost.get("workload")
    quoted_price = workload.get("quoted_price") if isinstance(workload, dict) else None
    if isinstance(quoted_price, dict):
        try:
            measured_quantity = int(str(cost["measured_quantity"]))
            numerator = int(str(quoted_price["numerator"]))
            denominator = int(str(quoted_price["denominator"]))
            price_unit = str(quoted_price["quantity_unit"]).lower()
            measured_unit = str(cost["measured_unit"]).lower()
        except (KeyError, TypeError, ValueError) as error:
            raise QualificationError("cost response contains an invalid quote") from error
        if measured_unit == "nanosecond" and price_unit in {"second", "seconds"}:
            denominator *= 1_000_000_000
        elif measured_unit == "nanosecond" and price_unit in {"hour", "hours"}:
            denominator *= 3_600_000_000_000
        expected_quote = (measured_quantity * numerator + denominator - 1) // denominator
        if quoted_fee != expected_quote:
            raise QualificationError("quote-derived cost does not reconcile exactly")
    if expected_ceiling is not None:
        try:
            ceiling = int(str(cost["spend_ceiling"]))
            authorized = int(str(cost["authorized_fee"]))
            pending = int(str(cost["pending_fee"]))
            remaining = int(str(cost["remaining_spend"]))
        except (KeyError, TypeError, ValueError) as error:
            raise QualificationError("cost response omitted enforced spend values") from error
        if ceiling != expected_ceiling:
            raise QualificationError(
                "cost response spend ceiling differs from the requested ceiling"
            )
        if pending != max(authorized - aggregate_fee, 0):
            raise QualificationError("pending signer exposure does not reconcile")
        if remaining != max(ceiling - aggregate_fee - pending, 0):
            raise QualificationError("remaining spend does not reconcile")


def safe_offer(offer: dict[str, Any]) -> dict[str, object]:
    """Keep only non-secret offer evidence."""

    return {
        key: offer.get(key)
        for key in (
            "id",
            "capability",
            "model",
            "runner_url",
            "orchestrator_address",
            "constraints",
            "price",
            "observed_at",
            "expires_at",
        )
    }


def git_revision(path: Path) -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    completed = subprocess.run(  # noqa: S603 - fixed git inspection
        [git, "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def write_evidence(settings: Settings, evidence: dict[str, object]) -> Path:
    settings.evidence_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_case = "".join(
        character for character in settings.case_id if character.isalnum() or character in "-_"
    )
    path = settings.evidence_root / f"gateway-{safe_case}-{timestamp}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def qualify(settings: Settings) -> tuple[dict[str, object], Path]:
    started_at = datetime.now(UTC).isoformat()
    health, _ = request_json(settings, "/health/ready")
    offers, headers = request_json(settings, "/v1/offers")
    if not isinstance(offers, dict):
        raise QualificationError("offers response must be a JSON object")
    stale = headers.get("x-clearinghouse-discovery-stale", "false").lower() == "true"
    if stale and not settings.allow_stale:
        raise QualificationError(
            "discovery is serving a stale snapshot; set QUAL_ALLOW_STALE_DISCOVERY=true to proceed"
        )
    offer = select_offer(offers.get("items"), settings)
    evidence: dict[str, object] = {
        "schema": "livepeer.clearinghouse.gateway-qualification.v1",
        "case_id": settings.case_id,
        "started_at": started_at,
        "status": "planned",
        "execute": settings.execute,
        "clearinghouse_url": settings.clearinghouse_url,
        "clearinghouse_revision": git_revision(ROOT),
        "gateway_revision": git_revision(settings.gateway_root),
        "health": health,
        "discovery_stale": stale,
        "selected_offer": safe_offer(offer),
        "maximum_authorized_wei": str(settings.max_spend_wei),
    }
    if not settings.execute:
        path = write_evidence(settings, evidence)
        return evidence, path

    created, _ = request_json(
        settings,
        "/v1/workloads",
        method="POST",
        payload={
            "offer_id": str(offer["id"]),
            "ttl_seconds": settings.workload_ttl_seconds,
            "client_reference": (
                f"gateway-qualification-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            ),
            "max_spend_wei": str(settings.max_spend_wei),
        },
    )
    if not isinstance(created, dict):
        raise QualificationError("workload response must be a JSON object")
    workload_id = created.get("id")
    sdk_token = created.get("sdk_token")
    if not isinstance(workload_id, str) or not isinstance(sdk_token, str):
        raise QualificationError("workload response omitted id or sdk_token")
    try:
        gateway_result = run_gateway(settings, sdk_token)
        events, cost = wait_for_usage(settings, workload_id)
        if settings.action == "interrupt":
            cost = wait_for_interruption_stability(settings, workload_id, cost)
            events, _ = wait_for_usage(settings, workload_id)
        evidence.update(
            {
                "workload_id": workload_id,
                "gateway_result": gateway_result,
                "usage_events": events,
                "cost": cost,
                "signer_fee_delta_wei": str(
                    int(str(cost["computed_fee"])) - int(str(cost["quoted_fee"]))
                ),
            }
        )
        validate_accounting(events, cost, settings.max_spend_wei)
    except QualificationError as error:
        evidence.update(
            {
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error": str(error),
            }
        )
        path = write_evidence(settings, evidence)
        raise QualificationError(f"{error}; evidence: {path}") from error
    finally:
        try:
            request_json(settings, f"/v1/workloads/{workload_id}", method="DELETE")
            evidence["cleanup"] = "workload-revoked"
        except QualificationError as cleanup_error:
            evidence["cleanup"] = f"failed: {cleanup_error}"
    if evidence["cleanup"] != "workload-revoked":
        evidence.update(
            {
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error": str(evidence["cleanup"]),
            }
        )
        path = write_evidence(settings, evidence)
        raise QualificationError(f"workload cleanup failed; evidence: {path}")
    evidence.update(
        {
            "status": "passed",
            "completed_at": datetime.now(UTC).isoformat(),
        }
    )
    path = write_evidence(settings, evidence)
    return evidence, path


def _optional(values: dict[str, str], name: str) -> str | None:
    return values.get(name, "").strip() or None


def _positive_int(values: dict[str, str], name: str, default: int | None = None) -> int:
    raw = values.get(name, "") or (str(default) if default is not None else "")
    try:
        value = int(raw)
    except ValueError as error:
        raise QualificationError(f"{name} must be an integer") from error
    if value <= 0:
        raise QualificationError(f"{name} must be positive")
    return value


def _positive_float(values: dict[str, str], name: str, default: float) -> float:
    try:
        value = float(values.get(name, "") or default)
    except ValueError as error:
        raise QualificationError(f"{name} must be a number") from error
    if value <= 0:
        raise QualificationError(f"{name} must be positive")
    return value


def _non_negative_float(values: dict[str, str], name: str, default: float) -> float:
    try:
        value = float(values.get(name, "") or default)
    except ValueError as error:
        raise QualificationError(f"{name} must be a number") from error
    if value < 0:
        raise QualificationError(f"{name} must be non-negative")
    return value


def _boolean(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise QualificationError(f"{name} must be true or false")


def _json_object(values: dict[str, str], name: str) -> dict[str, object] | None:
    raw = values.get(name, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise QualificationError(f"{name} must be valid JSON") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{name} must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("qualification.env"))
    arguments = parser.parse_args()
    try:
        settings = Settings.from_values(load_env(arguments.env_file))
        evidence, path = qualify(settings)
    except QualificationError as error:
        print(f"gateway qualification failed: {error}", file=sys.stderr)
        return 1
    offer = evidence["selected_offer"]
    if not isinstance(offer, dict):
        print("gateway qualification failed: evidence omitted selected offer", file=sys.stderr)
        return 1
    constraints = offer.get("constraints")
    orchestrator = (
        constraints.get("orchestrator_url", "unknown")
        if isinstance(constraints, dict)
        else "unknown"
    )
    print(f"gateway qualification {evidence['status']}: {offer['capability']} at {orchestrator}")
    print(f"evidence: {path}")
    if not settings.execute:
        print("read-only plan complete; set QUAL_EXECUTE=true to authorize execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
