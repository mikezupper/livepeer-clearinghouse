"""Opaque, scope-bound cursor primitives shared by every collection boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import cast, overload

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class InvalidCursor(ValueError):
    """A cursor is malformed, forged, or belongs to another collection query."""


class StaleCursor(ValueError):
    """A cursor names a discovery snapshot that is no longer current."""


@dataclass(frozen=True, slots=True, eq=False)
class KeysetPage[T]:
    items: tuple[T, ...]
    next_key: tuple[str, ...] | None

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    def __getitem__(self, index: int | slice) -> T | tuple[T, ...]:
        return self.items[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, KeysetPage):
            return self.items == other.items and self.next_key == other.next_key
        return isinstance(other, Sequence) and list(self.items) == list(other)


@dataclass(frozen=True, slots=True)
class Cursor:
    key: tuple[str, ...]
    snapshot: str | None = None
    stale: bool = False


class CursorCodec:
    """Version and authenticate cursors without exposing storage details."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("cursor secret must be at least 16 characters")
        self._secret = secret.encode()

    @staticmethod
    def _b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def encode(
        self,
        *,
        collection: str,
        scope: str,
        filters: dict[str, str | None],
        key: tuple[str, ...],
        snapshot: str | None = None,
        stale: bool = False,
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "collection": collection,
                "scope": scope,
                "filters": filters,
                "key": key,
                "snapshot": snapshot,
                "stale": stale,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return f"{self._b64(payload)}.{self._b64(signature)}"

    def decode(
        self,
        token: str | None,
        *,
        collection: str,
        scope: str,
        filters: dict[str, str | None],
    ) -> Cursor:
        if token is None:
            return Cursor(())
        try:
            payload_part, signature_part = token.split(".", 1)
            payload = self._unb64(payload_part)
            signature = self._unb64(signature_part)
            expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise InvalidCursor("cursor signature is invalid")
            decoded = cast(dict[str, object], json.loads(payload))
            if not isinstance(decoded, dict):
                raise InvalidCursor("cursor payload is invalid")
            if (
                decoded.get("v") != 1
                or decoded.get("collection") != collection
                or decoded.get("scope") != scope
                or decoded.get("filters") != filters
            ):
                raise InvalidCursor("cursor does not belong to this collection query")
            raw_key = decoded.get("key")
            if not isinstance(raw_key, list) or not all(isinstance(item, str) for item in raw_key):
                raise InvalidCursor("cursor key is invalid")
            snapshot = decoded.get("snapshot")
            if snapshot is not None and not isinstance(snapshot, str):
                raise InvalidCursor("cursor snapshot is invalid")
            stale = decoded.get("stale", False)
            if not isinstance(stale, bool):
                raise InvalidCursor("cursor stale marker is invalid")
            return Cursor(tuple(raw_key), snapshot, stale)
        except InvalidCursor:
            raise
        except (binascii.Error, TypeError, ValueError, UnicodeDecodeError) as error:
            raise InvalidCursor("cursor is malformed") from error


def validate_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"page limit must be between 1 and {MAX_PAGE_SIZE}")
    return limit
