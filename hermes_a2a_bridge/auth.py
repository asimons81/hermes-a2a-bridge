"""Bearer-token helpers. Secrets are never included in responses."""

from __future__ import annotations

import hmac
import re
import secrets


_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+)([^\s,;]+)")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(auth(?:orization)?_?token|token|password|secret)\b(\s*[:=]\s*|\s+)([^\s,;]+)"
)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def bearer_is_valid(header: str | None, expected: str) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    supplied = header[7:]
    return bool(supplied) and constant_time_equal(supplied, expected)


def redact_secrets(value: object, *known_secrets: str | None) -> str:
    text = str(value)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    text = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    for secret in known_secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text
