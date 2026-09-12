"""Strict decoding for the pinned go-livepeer create_signed_ticket event."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from clearinghouse.domain.core import SignedTicketEvent

_RFC3339_NANO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def epoch_nanoseconds(value: str) -> int:
    match = _RFC3339_NANO.fullmatch(value)
    if match is None:
        raise ValueError("timestamp must be RFC3339Nano")
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction = (match.group(7) or "").ljust(9, "0")
    zone = match.group(8)
    normalized = f"{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{zone}"
    instant = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    delta = instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + int(fraction)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _Data(_Strict):
    session_id: str = Field(min_length=1, max_length=256)
    session_status: str = ""
    app: str = ""
    pipeline: str = ""
    request_id: str = ""
    orch_address: str = ""
    manifest_id: str = ""
    pm_session_id: str = ""
    current_time: str
    current_time_unix: int
    previous_time: str
    previous_time_unix: int
    billable_secs: int | Decimal = 0
    pixels: int = Field(default=0, ge=0)
    session_balance: str = "0"
    computed_fee: str = Field(pattern=r"^(0|[1-9][0-9]{0,77})$")
    cost: str = "0"
    sequence_number: int = Field(ge=0)
    num_tickets: int = Field(ge=1, le=100)
    auth_id: str = Field(min_length=1, max_length=256)


class _Envelope(_Strict):
    id: str
    type: str
    timestamp: str
    gateway: str = ""
    data: _Data

    @field_validator("id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value.lower():
            raise ValueError("event id must be a canonical UUID")
        return value.lower()


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode_signed_ticket(payload: bytes) -> SignedTicketEvent | None:
    raw = json.loads(payload, parse_float=Decimal, object_pairs_hook=_pairs)
    if not isinstance(raw, dict) or raw.get("type") != "create_signed_ticket":
        return None
    envelope = _Envelope.model_validate(raw)
    data = envelope.data
    current_ns = epoch_nanoseconds(data.current_time)
    previous_ns = epoch_nanoseconds(data.previous_time)
    if current_ns // 1_000_000 != data.current_time_unix:
        raise ValueError("current_time does not match current_time_unix")
    if previous_ns // 1_000_000 != data.previous_time_unix:
        raise ValueError("previous_time does not match previous_time_unix")
    return SignedTicketEvent(
        envelope.id,
        datetime.fromisoformat(data.current_time.replace("Z", "+00:00")),
        data.current_time,
        envelope.gateway,
        data.session_id,
        data.session_status,
        data.app,
        data.pipeline,
        data.request_id,
        data.orch_address.lower(),
        data.manifest_id,
        data.pm_session_id,
        data.current_time,
        current_ns,
        data.current_time_unix,
        data.previous_time,
        previous_ns,
        data.previous_time_unix,
        str(data.billable_secs),
        data.pixels,
        int(data.session_balance),
        data.cost,
        int(data.computed_fee),
        data.sequence_number,
        data.num_tickets,
        data.auth_id,
    )
