"""Bounded Chrome native-messaging framing. Never log input payloads."""

import json
import struct

from fabu.browser_auth.cookies import MAX_MESSAGE, BrowserSessionError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BrowserSessionError("browser_protocol_invalid")
        result[key] = value
    return result


def decode_message(raw):
    try:
        if len(raw) > MAX_MESSAGE:
            raise BrowserSessionError("browser_protocol_invalid")
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise BrowserSessionError("browser_protocol_invalid") from None


def _read_exact(stream, size, *, allow_eof=False):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            if allow_eof and not data:
                return None
            raise BrowserSessionError("browser_protocol_truncated")
        data.extend(chunk)
    return bytes(data)


def read_message(stream):
    header = _read_exact(stream, 4, allow_eof=True)
    if header is None:
        return None
    size = struct.unpack("<I", header)[0]
    if not 0 < size <= MAX_MESSAGE:
        raise BrowserSessionError("browser_protocol_invalid")
    return decode_message(_read_exact(stream, size))


def write_message(stream, message):
    body = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if not 0 < len(body) <= MAX_MESSAGE:
        raise BrowserSessionError("browser_protocol_invalid")
    stream.write(struct.pack("<I", len(body)))
    stream.write(body)
    stream.flush()
