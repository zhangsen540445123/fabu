"""Even dimensions with explicit rotation/SAR handling; never stretch to a fixed ratio."""

from fractions import Fraction

from pydantic import BaseModel, ConfigDict

from fabu.media.probe import MediaInfo, UnsupportedMedia


class Size(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    width: int
    height: int


def fit_output(info: MediaInfo, crop_ratio: Fraction | None = None) -> Size:
    ratio = info.display_aspect if crop_ratio is None else crop_ratio
    if ratio <= 0:
        raise UnsupportedMedia("Display aspect must be positive")
    bound_w, bound_h = (2560, 1440) if ratio > 1 else (1440, 2560)
    if ratio == 1:
        return Size(width=1440, height=1440)
    height = min(Fraction(bound_h), Fraction(bound_w) / ratio)
    width = height * ratio
    if min(width, height) < 2:
        raise UnsupportedMedia("Aspect is too narrow for supported even output dimensions")

    # Nearest even integer; ties round down deterministically without floating-point drift.
    def even(n: Fraction) -> int:
        lower = (n // 2) * 2
        return int(lower + (2 if n - lower > 1 else 0))

    return Size(width=min(even(width), bound_w), height=min(even(height), bound_h))
