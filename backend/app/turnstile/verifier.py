from __future__ import annotations

from typing import Protocol, runtime_checkable


class TurnstileVerificationError(Exception):
    """Raised when Turnstile verification fails."""


@runtime_checkable
class TurnstileVerifier(Protocol):
    def verify(self, token: str | None, *, remote_ip: str | None) -> bool: ...


class NoOpTurnstileVerifier:
    def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        return True


class FakeTurnstileVerifier:
    """Test verifier: accepts token 'valid-turnstile-token'."""

    def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        return token == "valid-turnstile-token"


class CloudflareTurnstileVerifier:
    def __init__(self, *, secret_key: str, timeout_seconds: float = 10.0) -> None:
        self._secret_key = secret_key
        self._timeout_seconds = timeout_seconds

    def verify(self, token: str | None, *, remote_ip: str | None) -> bool:
        if not token:
            return False
        import urllib.parse
        import urllib.request

        payload = urllib.parse.urlencode(
            {
                "secret": self._secret_key,
                "response": token,
                **({"remoteip": remote_ip} if remote_ip else {}),
            }
        ).encode()
        request = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                import json

                body = json.loads(response.read().decode())
                return bool(body.get("success"))
        except Exception:
            return False
