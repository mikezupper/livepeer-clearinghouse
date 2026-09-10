"""Resend SDK email delivery adapter."""

from __future__ import annotations

import resend
from resend.exceptions import ResendError
from resend.http_client_httpx import HTTPXClient

from clearinghouse.domain.auth import AuthenticationUnavailable


class ResendEmailCodeSender:
    """Deliver OTP mail through the official SDK and a configurable endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str,
        from_address: str,
        timeout_seconds: int = 10,
    ) -> None:
        # The official SDK exposes process-wide transport configuration. The
        # composition root creates exactly one sender before serving requests.
        resend.api_key = api_key
        resend.api_url = api_url.rstrip("/")
        resend.default_async_http_client = HTTPXClient(timeout=timeout_seconds)
        self._from_address = from_address

    async def send_code(self, email: str, code: str, expires_in_minutes: int) -> None:
        """Send a code without logging or returning it."""
        try:
            await resend.Emails.send_async(
                {
                    "from": self._from_address,
                    "to": [email],
                    "subject": "Your Livepeer Clearinghouse sign-in code",
                    "text": (
                        f"Your sign-in code is {code}. It expires in {expires_in_minutes} minutes."
                    ),
                }
            )
        except ResendError as error:
            raise AuthenticationUnavailable("email delivery unavailable") from error
