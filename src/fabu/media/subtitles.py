"""Plain UTF-8 SRT and explicit font validation; no style commands or online font lookup."""

import re
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError
from PIL import ImageFont
from pydantic import BaseModel, ConfigDict

from fabu.assets import assert_no_reparse
from fabu.media.models import PlanInvalid

_TIMING = re.compile(
    r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> (\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$"
)


class Cue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    start_ms: int
    end_ms: int
    text: str


def parse_srt(path: Path, scope_duration_ms: int) -> list[Cue]:
    assert_no_reparse(path)
    if not path.is_file() or path.suffix.lower() != ".srt" or path.stat().st_size > 5_000_000:
        raise PlanInvalid("Select a supported local UTF-8 SRT file")
    try:
        text = (
            path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
        )
    except UnicodeError as exc:
        raise PlanInvalid("Subtitles must be UTF-8") from exc
    if not text or "\0" in text:
        raise PlanInvalid("Subtitles are empty or contain invalid controls")
    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise PlanInvalid("Invalid SRT block")
        match = _TIMING.fullmatch(lines[1].strip())
        if not match:
            raise PlanInvalid("Invalid SRT timestamp")
        values = list(map(int, match.groups()))
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        if end <= start or end > scope_duration_ms or (cues and start < cues[-1].end_ms):
            raise PlanInvalid("Subtitle timing is invalid or outside its edited scope")
        content = "\n".join(lines[2:])
        if not content.strip():
            raise PlanInvalid("Subtitle cue is empty")
        cues.append(Cue(start_ms=start, end_ms=end, text=content))
    if len(cues) > 5000:
        raise PlanInvalid("Too many subtitle cues for the first-version renderer")
    return cues


def validate_font(path: Path, text: str) -> None:
    assert_no_reparse(path)
    if not path.is_file() or path.suffix.lower() not in {".ttf", ".otf"}:
        raise PlanInvalid("Choose an existing supported local font")
    try:
        with TTFont(path, lazy=True) as font:
            cmap = font.getBestCmap() or {}
            missing = {c for c in text if not c.isspace() and ord(c) not in cmap}
        if missing:
            raise PlanInvalid("Selected font lacks required glyphs; choose another font")
        ImageFont.truetype(str(path), 32)
    except (OSError, TTLibError) as exc:
        raise PlanInvalid("Selected font cannot be decoded") from exc
