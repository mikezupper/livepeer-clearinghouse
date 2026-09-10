"""Private Resend-compatible OTP sink used only by disposable qualification."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

MAX_REQUEST_BYTES = 16_384
CODE_PATTERN = re.compile(r"\b([0-9]{6})\b")
RUNTIME_UID = 10_001


def read_secret(path: str) -> str:
    """Read one bounded non-empty secret without exposing its value in errors."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise ValueError("qualification mail secret file is unreadable") from error
    if not 1 <= len(raw) <= 4096:
        raise ValueError("qualification mail secret must be 1..4096 bytes")
    value = raw.decode("utf-8").rstrip("\r\n")
    if not value:
        raise ValueError("qualification mail secret is empty")
    return value


def drop_privileges() -> None:
    """Irreversibly enter the same unprivileged identity as the backend."""
    if os.geteuid() != 0:
        raise ValueError("qualification mail must start as root to read protected secrets")
    os.setgroups([])
    os.setgid(RUNTIME_UID)
    os.setuid(RUNTIME_UID)
    if os.geteuid() != RUNTIME_UID or os.getegid() != RUNTIME_UID:
        raise ValueError("qualification mail could not drop privileges")


class Mailbox:
    """Thread-safe latest-code store; codes never enter process logs."""

    def __init__(self) -> None:
        self._codes: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, email: str, code: str) -> None:
        with self._lock:
            self._codes[email.casefold()] = code

    def latest(self, email: str) -> str | None:
        with self._lock:
            return self._codes.get(email.casefold())


class FakeResendHandler(BaseHTTPRequestHandler):
    """Minimal SDK-compatible endpoint plus a separately authenticated mailbox."""

    mailbox: ClassVar[Mailbox]
    resend_key: ClassVar[str]
    mailbox_token: ClassVar[str]
    server_version = "qualification-mail"
    sys_version = ""

    def log_message(self, _format: str, *args: object) -> None:
        """Suppress request logging because request bodies contain raw OTPs."""

    def _json(self, status: HTTPStatus, body: dict[str, object]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _authorized(self, expected: str, prefix: str = "Bearer ") -> bool:
        supplied = self.headers.get("Authorization", "")
        return supplied.startswith(prefix) and hmac.compare_digest(
            supplied[len(prefix) :], expected
        )

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler API.
        target = urlsplit(self.path)
        if target.path == "/health/ready":
            self._json(HTTPStatus.OK, {"status": "ready"})
            return
        if target.path != "/_qualification/latest" or not self._authorized(self.mailbox_token):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        values = parse_qs(target.query, keep_blank_values=True)
        email = values.get("email", [""])[0].strip().casefold()
        code = self.mailbox.latest(email)
        if not email or code is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "message_not_found"})
            return
        self._json(HTTPStatus.OK, {"email": email, "code": code})

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler API.
        if urlsplit(self.path).path != "/emails" or not self._authorized(self.resend_key):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 1 <= length <= MAX_REQUEST_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        try:
            payload: Any = json.loads(self.rfile.read(length))
            recipients = payload["to"]
            email = recipients[0] if isinstance(recipients, list) else recipients
            text = payload["text"]
            match = CODE_PATTERN.search(text)
            if not isinstance(email, str) or not isinstance(text, str) or match is None:
                raise ValueError
        except IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        self.mailbox.put(email, match.group(1))
        self._json(HTTPStatus.OK, {"id": "qualification-email"})


def build_server(host: str, port: int, resend_key: str, mailbox_token: str) -> ThreadingHTTPServer:
    """Construct the isolated HTTP server with per-process credentials."""
    handler = type(
        "ConfiguredFakeResendHandler",
        (FakeResendHandler,),
        {"mailbox": Mailbox(), "resend_key": resend_key, "mailbox_token": mailbox_token},
    )
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 -- container-only service.
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args(argv)
    resend_key = read_secret(os.environ["QUALIFICATION_RESEND_KEY_FILE"])
    mailbox_token = read_secret(os.environ["QUALIFICATION_MAILBOX_TOKEN_FILE"])
    drop_privileges()
    server = build_server(args.host, args.port, resend_key, mailbox_token)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
