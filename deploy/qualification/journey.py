"""Qualify the account-to-charge journey against an isolated Compose stack."""

from __future__ import annotations

import argparse
import calendar
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import uuid4

from deploy.qualification.run import (
    BASE_COMPOSE,
    BUILT_SERVICES,
    DOCKER,
    OVERLAY_COMPOSE,
    STARTED_SERVICES,
    Harness,
    QualificationFailure,
    parse_json_stream,
    redact,
    validate_loopback_publishers,
    validate_service_states,
)

ROOT: Final = Path(__file__).resolve().parents[2]
MAX_RESPONSE_BYTES: Final = 1_048_576
OPERATOR_EMAIL: Final = "operator@qualification.example.com"
USER_EMAIL: Final = "holder@qualification.example.com"
NPM: Final = shutil.which("npm") or "/usr/bin/npm"


class ApiClient:
    """Small cookie-jar client that applies the browser's CSRF contract."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))

    def csrf(self) -> str:
        for cookie in self.cookies:
            if cookie.name == "och_csrf" and isinstance(cookie.value, str):
                return cookie.value
        raise QualificationFailure("authenticated client has no CSRF cookie")

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        csrf: bool = False,
    ) -> Any:
        encoded = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request_headers = {"Accept": "application/json", **(headers or {})}
        if encoded is not None:
            request_headers["Content-Type"] = "application/json"
        if csrf:
            request_headers.update({"Origin": self.base_url, "X-CSRF-Token": self.csrf()})
        request = Request(  # noqa: S310 -- base URL is a generated loopback fixture.
            self.base_url + path, data=encoded, headers=request_headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=10)  # noqa: S310 -- loopback fixture.
        except HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
        if status not in expected:
            raise QualificationFailure(f"{method} {path.split('?', 1)[0]} returned {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise QualificationFailure("qualification HTTP response exceeded its bound")
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise QualificationFailure("qualification HTTP response was not JSON") from error


def epoch_milliseconds(value: datetime) -> int:
    """Convert an aware UTC instant without floating-point arithmetic."""
    normalized = value.astimezone(UTC)
    return calendar.timegm(normalized.utctimetuple()) * 1000 + normalized.microsecond // 1000


def payment_state(state_id: str, last_update: str) -> dict[str, object]:
    """Return the supported fixed-price go-livepeer compatibility state."""
    return {
        "StateID": state_id,
        "PMSessionID": f"pm_{state_id}",
        "LastUpdate": last_update,
        "OrchestratorAddress": "0x" + "a" * 40,
        "App": "qualification",
        "AuthExpiry": 0,
        "SenderNonce": 1,
        "Balance": "0",
        "InitialPricePerUnit": 1,
        "InitialPixelsPerUnit": 2,
        "Type": "fixed",
        "SequenceNumber": 0,
        "AuthID": "",
    }


def signed_event(event_id: str, state: dict[str, object], auth_id: str) -> bytes:
    """Build the exact pinned create_signed_ticket confirmation for one receipt."""
    current_time = str(state["LastUpdate"])
    instant = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
    timestamp = epoch_milliseconds(instant)
    body = {
        "id": event_id,
        "type": "create_signed_ticket",
        "timestamp": str(timestamp),
        "gateway": "",
        "data": {
            "session_id": state["StateID"],
            "session_status": "new",
            "app": state["App"],
            "pipeline": "fixed",
            "request_id": f"request_{event_id}",
            "orch_address": state["OrchestratorAddress"],
            "orch_url": "",
            "manifest_id": f"manifest_{event_id}",
            "pm_session_id": state["PMSessionID"],
            "current_time": current_time,
            "current_time_unix": timestamp,
            "previous_time": current_time,
            "previous_time_unix": timestamp,
            "billable_secs": 0,
            "pixels": 0,
            "session_balance": "0",
            "computed_fee": "1",
            "cost": "0.0000000000",
            "sequence_number": 0,
            "num_tickets": 1,
            "auth_id": auth_id,
        },
    }
    return json.dumps(body, separators=(",", ":")).encode()


class JourneyHarness(Harness):
    """Own the fixture while the API, broker, and browser journey execute."""

    def prepare(self) -> None:
        super().prepare()
        if self.env_file is None:
            raise QualificationFailure("qualification environment was not initialized")
        configured = self.env_file.read_text()
        marker = "CLEARINGHOUSE_SIGNER_GLOBAL_EXPOSURE_CAP=0\n"
        if marker not in configured:
            raise QualificationFailure("qualification global exposure setting is missing")
        self.env_file.write_text(
            configured.replace(marker, "CLEARINGHOUSE_SIGNER_GLOBAL_EXPOSURE_CAP=1000\n")
        )
        os.chmod(self.env_file, 0o600)

    def secret(self, name: str) -> str:
        if self.temp is None:
            raise QualificationFailure("qualification secrets are unavailable")
        return (Path(self.temp.name) / name).read_text().rstrip("\r\n")

    def mailbox_code(self, email: str) -> str:
        token = self.secret("qualification-mailbox-token")
        url = f"http://127.0.0.1:{self.resend_port}/_qualification/latest?" + urlencode(
            {"email": email}
        )
        for _attempt in range(40):
            request = Request(url, headers={"Authorization": f"Bearer {token}"})
            try:
                with urlopen(request, timeout=5) as response:  # noqa: S310 -- loopback fixture.
                    body = response.read(4097)
                value = json.loads(body)
                code = value.get("code") if isinstance(value, dict) else None
                if isinstance(code, str) and len(code) == 6 and code.isdigit():
                    return code
            except HTTPError as error:
                if error.code != 404:
                    raise QualificationFailure("protected qualification mailbox failed") from error
            time.sleep(0.1)
        raise QualificationFailure("email code was not delivered to the protected mailbox")

    def login(self, email: str) -> tuple[ApiClient, dict[str, object]]:
        client = ApiClient(f"http://127.0.0.1:{self.edge_port}")
        client.request("POST", "/v1/auth/email/code", payload={"email": email}, expected=(202,))
        session = client.request(
            "POST",
            "/v1/auth/email/verify",
            payload={"email": email, "code": self.mailbox_code(email)},
        )
        if not isinstance(session, dict):
            raise QualificationFailure("email verification did not return a session")
        return client, session

    def publish(self, payload: bytes) -> dict[str, int]:
        if self.env_file is None:
            raise QualificationFailure("qualification environment was not initialized")
        command = [
            DOCKER,
            "compose",
            "--project-directory",
            str(ROOT),
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(OVERLAY_COMPOSE),
            "-p",
            self.project,
            "--env-file",
            str(self.env_file),
            "exec",
            "-T",
            "consumer",
            "python",
            "/app/deploy/qualification/publish_event.py",
        ]
        result = subprocess.run(  # noqa: S603 -- fixed executable, project, and module.
            command, cwd=ROOT, input=payload, capture_output=True, check=False
        )
        if result.returncode != 0:
            detail = redact(
                (result.stdout + result.stderr).decode(errors="replace"), tuple(self.secrets)
            ).strip()
            raise QualificationFailure(
                "real Redpanda event publish failed" + (f": {detail[:1000]}" if detail else "")
            )
        try:
            value = json.loads(result.stdout)
            return {"partition": int(value["partition"]), "offset": int(value["offset"])}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise QualificationFailure(
                "publisher returned invalid transport coordinates"
            ) from error

    def browser_assertions(self, *, account_name: str, charge_id: str) -> None:
        environment = {
            **os.environ,
            "QUALIFICATION_EDGE_URL": f"http://127.0.0.1:{self.edge_port}",
            "QUALIFICATION_MAILBOX_URL": f"http://127.0.0.1:{self.resend_port}",
            "QUALIFICATION_MAILBOX_TOKEN": self.secret("qualification-mailbox-token"),
            "QUALIFICATION_OPERATOR_EMAIL": OPERATOR_EMAIL,
            "QUALIFICATION_USER_EMAIL": USER_EMAIL,
            "QUALIFICATION_ACCOUNT_NAME": account_name,
            "QUALIFICATION_CHARGE_ID": charge_id,
        }
        result = subprocess.run(  # noqa: S603 -- repository-owned pinned Playwright command.
            [
                NPM,
                "exec",
                "playwright",
                "test",
                "--config=e2e/qualification.playwright.config.ts",
            ],
            cwd=ROOT / "frontend",
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise QualificationFailure("production browser qualification failed")

    def journey(self) -> dict[str, object]:
        operator, operator_session = self.login(OPERATOR_EMAIL)
        if operator_session.get("roles") != ["operator"]:
            raise QualificationFailure("bootstrap identity is not the operator")
        suffix = uuid4().hex[:12]
        account_name = f"Qualification payer {suffix}"
        tenant = operator.request(
            "POST",
            "/v1/tenants",
            payload={"display_name": f"Qualification {suffix}"},
            expected=(201,),
            csrf=True,
        )
        tenant_two = operator.request(
            "POST",
            "/v1/tenants",
            payload={"display_name": f"Isolation {suffix}"},
            expected=(201,),
            csrf=True,
        )
        account = operator.request(
            "POST",
            "/v1/accounts",
            payload={
                "tenant_id": tenant["id"],
                "display_name": account_name,
                "unit": "wei",
                "exposure_cap": "100",
            },
            expected=(201,),
            csrf=True,
        )
        other_account = operator.request(
            "POST",
            "/v1/accounts",
            payload={
                "tenant_id": tenant_two["id"],
                "display_name": f"Isolation payer {suffix}",
                "unit": "wei",
                "exposure_cap": "100",
            },
            expected=(201,),
            csrf=True,
        )
        target = operator.request(
            "POST",
            "/v1/principals",
            payload={
                "tenant_id": tenant["id"],
                "account_id": account["id"],
                "display_name": "Qualification holder",
                "roles": ["credential_holder"],
            },
            expected=(201,),
            csrf=True,
        )
        user, unscoped = self.login(USER_EMAIL)
        invitation = operator.request(
            "POST",
            f"/v1/principals/{quote(target['id'])}/identity-invitations",
            payload={
                "source_principal_id": unscoped["principal_id"],
                "reason": "qualification identity link",
            },
            expected=(201,),
            csrf=True,
        )
        user.request(
            "POST",
            "/v1/auth/identity-links",
            payload={"invitation_secret": invitation["invitation_secret"]},
            csrf=True,
        )
        user, scoped = self.login(USER_EMAIL)
        if scoped.get("account_id") != account["id"] or scoped.get("tenant_id") != tenant["id"]:
            raise QualificationFailure("linked identity did not receive the intended tenant scope")
        user.request("GET", f"/v1/balances/{quote(other_account['id'])}", expected=(403, 404))
        grant_key = f"qualification-grant-{suffix}"
        grant_body = {
            "account_id": account["id"],
            "kind": "credit",
            "amount": {"amount": "100", "unit": "wei"},
            "reason": "qualification funding",
            "external_reference": f"qualification-{suffix}",
        }
        grant = operator.request(
            "POST",
            "/v1/grants",
            payload=grant_body,
            headers={"Idempotency-Key": grant_key},
            expected=(201,),
            csrf=True,
        )
        duplicate_grant = operator.request(
            "POST",
            "/v1/grants",
            payload=grant_body,
            headers={"Idempotency-Key": grant_key},
            expected=(201,),
            csrf=True,
        )
        if duplicate_grant.get("id") != grant.get("id"):
            raise QualificationFailure("grant idempotency created a second ledger entry")
        effective = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        operator.request(
            "POST",
            "/v1/rate-cards",
            payload={
                "capability": "fixed",
                "model": None,
                "rate": {
                    "numerator": "1",
                    "denominator": "2",
                    "charge_unit": "wei",
                    "quantity_unit": "fixed",
                },
                "effective_at": effective,
            },
            expected=(201,),
            csrf=True,
        )
        operator.request(
            "PUT",
            f"/v1/accounts/{quote(account['id'])}/capabilities",
            payload={
                "capability": "fixed",
                "model": None,
                "allowed": True,
                "reason": "qualification allow",
            },
            expected=(204,),
            csrf=True,
        )
        issued = operator.request(
            "POST",
            "/v1/credentials",
            payload={
                "account_id": account["id"],
                "principal_id": target["id"],
                "label": "Qualification credential",
            },
            expected=(201,),
            csrf=True,
        )
        session = operator.request(
            "POST",
            "/v1/sessions",
            payload={
                "capability": "fixed",
                "model": None,
                "app": "qualification",
                "requested_cap": "10",
                "unit": "wei",
                "ttl_seconds": 600,
            },
            headers={
                "Authorization": f"Bearer {issued['secret']}",
                "Idempotency-Key": f"qualification-session-{suffix}",
            },
            expected=(201,),
        )
        instant = datetime.now(UTC).replace(microsecond=123000).isoformat().replace("+00:00", "Z")
        state = payment_state(f"qualification_state_{suffix}", instant)
        authorize_body = {
            "headers": {"Authorization": [f"Bearer {session['token']}"]},
            "state": state,
        }
        webhook = {"Authorization": f"Bearer {self.secret('signer-webhook-secret')}"}
        allowed = operator.request(
            "POST", "/v1/compat/go-livepeer/authorize", payload=authorize_body, headers=webhook
        )
        if allowed.get("status") != 200 or not isinstance(allowed.get("auth_id"), str):
            raise QualificationFailure("compatibility authorization was not allowed")
        replay = operator.request(
            "POST", "/v1/compat/go-livepeer/authorize", payload=authorize_body, headers=webhook
        )
        if replay.get("status") != 402 or replay.get("reason") != "replayed_state":
            raise QualificationFailure("authorization replay was not idempotently denied")
        event_id = str(uuid4())
        event = signed_event(event_id, state, allowed["auth_id"])
        first_position = self.publish(event)
        second_position = self.publish(event)
        if second_position["offset"] <= first_position["offset"]:
            raise QualificationFailure("duplicate broker event did not traverse Redpanda")
        charge: dict[str, object] | None = None
        usage_count = 0
        for _attempt in range(80):
            charges = user.request("GET", f"/v1/charges?{urlencode({'account_id': account['id']})}")
            usage = user.request("GET", f"/v1/usage?{urlencode({'account_id': account['id']})}")
            if len(charges["items"]) == 1 and len(usage["items"]) == 1:
                charge = charges["items"][0]
                usage_count = len(usage["items"])
                break
            time.sleep(0.25)
        if charge is None or charge["amount"] != {"value": "1", "unit": "wei"}:
            raise QualificationFailure("authoritative charge did not settle exactly once")
        balance = user.request("GET", f"/v1/balances/{quote(account['id'])}")
        if balance["posted"] != {"amount": "99", "unit": "wei"}:
            raise QualificationFailure("ledger balance did not reflect the settled charge")
        operator.request(
            "PUT",
            "/v1/operations/kill-switch",
            payload={"enabled": True, "reason": "qualification stop"},
            csrf=True,
        )
        kill_state = payment_state(f"qualification_kill_{suffix}", instant)
        killed_body = {
            "headers": {"Authorization": [f"Bearer {session['token']}"]},
            "state": kill_state,
        }
        killed = operator.request(
            "POST", "/v1/compat/go-livepeer/authorize", payload=killed_body, headers=webhook
        )
        if killed.get("status") != 402 or killed.get("reason") != "kill_switch_active":
            raise QualificationFailure("kill switch did not fail authorization closed")
        operator.request(
            "PUT",
            "/v1/operations/kill-switch",
            payload={"enabled": False, "reason": "qualification resume"},
            csrf=True,
        )
        reopened = operator.request(
            "POST", "/v1/compat/go-livepeer/authorize", payload=killed_body, headers=webhook
        )
        if reopened.get("status") != 200:
            raise QualificationFailure("authorization did not resume after kill-switch reopening")
        self.browser_assertions(account_name=account_name, charge_id=str(charge["id"]))
        return {
            "authentication": "email_otp_operator_and_linked_holder",
            "tenant_isolation": "denied",
            "grant_idempotency": "verified",
            "authorization_replay": "denied_without_second_reservation",
            "broker_duplicate": "settled_once",
            "usage_records": usage_count,
            "charge_records": 1,
            "ledger_posted": "99 wei",
            "kill_switch": "denied_then_reopened",
            "browsers": ["admin-web", "user-web"],
        }

    def run_journey(self) -> int:
        evidence: dict[str, object] = {
            "schema_version": 1,
            "status": "failed",
            "project": self.project,
        }
        try:
            self.prepare()
            self.compose("--profile", "ops", "build", *BUILT_SERVICES)
            evidence["images"] = self.image_manifest()
            self.started = True
            self.compose("up", "-d", "--wait", "--wait-timeout", "180", *STARTED_SERVICES)
            rows = parse_json_stream(self.compose("ps", "-a", "--format", "json").stdout)
            evidence["services"] = validate_service_states(rows)
            evidence["loopback_publishers"] = validate_loopback_publishers(
                rows, self.edge_port, self.resend_port
            )
            evidence["journey"] = self.journey()
            evidence["status"] = "passed"
        except (
            QualificationFailure,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            evidence["failure"] = redact(str(error), tuple(self.secrets))
            if self.started:
                evidence["diagnostics"] = self.diagnostics()
        finally:
            cleaned = self.cleanup()
            evidence["cleanup"] = "verified" if cleaned else "failed"
            if not cleaned:
                evidence.update(
                    {"status": "failed", "failure": "scoped Compose cleanup could not be verified"}
                )
            self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
            self.evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            if self.temp is not None:
                self.temp.cleanup()
        print(json.dumps({"status": evidence["status"], "evidence": str(self.evidence_path)}))
        return 0 if evidence["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "tmp/qualification/journey.json")
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        print("qualification requires Docker", file=sys.stderr)
        return 2
    return JourneyHarness(args.evidence.resolve()).run_journey()


if __name__ == "__main__":
    raise SystemExit(main())
