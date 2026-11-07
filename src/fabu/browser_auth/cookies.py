"""Strict serialization; errors never contain cookie values or browser-supplied text."""

import math
import re
import time

MAX_MESSAGE = 262144
MAX_COOKIES = 256
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")
ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}/$")
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*youtube\.com$")
_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FIELDS = {"name", "value", "domain", "path", "secure", "httpOnly", "hostOnly", "session"}


class BrowserSessionError(ValueError):
    """Only static, application-owned reason codes belong in this exception."""


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def serialize_cookies(rows, *, now=None):
    now = time.time() if now is None else now
    if type(rows) is not list or not rows or len(rows) > MAX_COOKIES:
        raise BrowserSessionError("browser_cookies_invalid")
    lines = ["# Netscape HTTP Cookie File", "# Fabu temporary YouTube session; do not share"]
    for row in rows:
        if (
            type(row) is not dict
            or not _FIELDS <= row.keys()
            or row.keys() - (_FIELDS | {"expirationDate"})
        ):
            raise BrowserSessionError("browser_cookies_invalid")
        for field in ("name", "value", "domain", "path"):
            value = row[field]
            if (
                type(value) is not str
                or len(value) > 8192
                or any(ord(c) < 32 or ord(c) > 126 for c in value)
            ):
                raise BrowserSessionError("browser_cookies_invalid")
        if (
            not _NAME.fullmatch(row["name"])
            or not row["path"].startswith("/")
            or not _DOMAIN.fullmatch(row["domain"].removeprefix("."))
        ):
            raise BrowserSessionError("browser_cookies_invalid")
        if any(type(row[key]) is not bool for key in ("secure", "httpOnly", "hostOnly", "session")):
            raise BrowserSessionError("browser_cookies_invalid")
        expiry = row.get("expirationDate")
        if expiry is not None and (not finite_number(expiry) or not 0 <= expiry <= 253402300799):
            raise BrowserSessionError("browser_cookies_invalid")
        if not row["session"]:
            if expiry is None:
                raise BrowserSessionError("browser_cookies_invalid")
            if expiry <= now:
                continue
        domain = row["domain"].removeprefix(".")
        if not row["hostOnly"]:
            domain = "." + domain
        if row["httpOnly"]:
            domain = "#HttpOnly_" + domain
        lines.append(
            "\t".join(
                (
                    domain,
                    "FALSE" if row["hostOnly"] else "TRUE",
                    row["path"],
                    "TRUE" if row["secure"] else "FALSE",
                    "0" if row["session"] else str(int(expiry)),
                    row["name"],
                    row["value"],
                )
            )
        )
    text = "\n".join(lines) + "\n"
    if len(lines) == 2:
        raise BrowserSessionError("browser_no_cookies")
    if len(text.encode("utf-8")) > MAX_MESSAGE:
        raise BrowserSessionError("browser_cookies_invalid")
    return text
