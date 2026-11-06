"""Logging whitelist. Raw tool output and payloads are deliberately not log events."""

import re
from collections.abc import Sequence

_ALLOWED = frozenset(
    {
        "event",
        "phase",
        "job_id",
        "target_id",
        "status",
        "error_code",
        "message",
        "progress",
        "timestamp",
        "elapsed_s",
        "exit_code",
    }
)
_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"(?i)(?:bearer\s+[^\s;,]+|(?:api[_-]?key|token|client[_-]?id|password)\s*[:=]\s*[^\s;,]+)"
)


def redact_event(event: dict, secrets: Sequence[str] = ()) -> dict:
    clean = {}
    for key, value in event.items():
        if key not in _ALLOWED or not isinstance(value, (str, int, float, bool, type(None))):
            continue
        if isinstance(value, str):
            for secret in sorted((s for s in secrets if s), key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            value = _URL.sub("[URL REDACTED]", value)
            value = _CREDENTIAL.sub("[REDACTED]", value)
            value = value[:2000]
        clean[key] = value
    return clean
