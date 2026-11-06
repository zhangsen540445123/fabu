"""Strict single-video identities, without playlist expansion or arbitrary URL fetching."""

import re
from urllib.parse import parse_qs, urlsplit

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_youtube_url(url: str) -> str:
    value = url.strip()
    if VIDEO_ID.fullmatch(value):
        return value
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or parts.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
            or parts.username
            or parts.password
            or parts.port
        ):
            raise ValueError("Select a single HTTPS YouTube video")
        query = parse_qs(parts.query, keep_blank_values=True)
        if "list" in query:
            raise ValueError("Playlists are not supported; provide only the video URL")
        if parts.hostname == "youtu.be":
            identifier = parts.path.removeprefix("/")
        elif parts.path == "/watch" and len(query.get("v", [])) == 1:
            identifier = query["v"][0]
        elif parts.path.startswith("/shorts/"):
            identifier = parts.path.removeprefix("/shorts/")
        else:
            raise ValueError("Only individual watch, shortlink and Shorts URLs are supported")
        if not VIDEO_ID.fullmatch(identifier):
            raise ValueError("Invalid YouTube video ID")
        return identifier
    except (ValueError, TypeError) as exc:
        raise ValueError("A single valid YouTube video URL or video ID is required") from exc
