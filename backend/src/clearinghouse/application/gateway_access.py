"""Compatibility boundary for the official livepeer-python-gateway SDK."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GatewayAccess:
    """The values needed by live-runner discovery and payment calls."""

    token: str
    signer_url: str
    discovery_url: str
    workload_id: str
    expires_at: str
    orchestrators: tuple[str, ...] = ()

    def sdk_token(self) -> str:
        """Encode the configuration accepted by ``livepeer_gateway.parse_token``."""

        authorization = {"Authorization": f"Bearer {self.token}"}
        payload: dict[str, object] = {
            "signer": self.signer_url,
            "discovery": self.discovery_url,
            "signer_headers": authorization,
            "discovery_headers": authorization,
        }
        if self.orchestrators:
            payload["orchestrators"] = list(self.orchestrators)
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return base64.b64encode(encoded).decode("ascii")
