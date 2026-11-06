"""Publishing data contracts. None of these models authorize external writes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class TopicSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["selected", "needs_selection"]
    group_name: str | None = None
    tags: tuple[str, ...] = ()
    matches: tuple[str, ...] = ()
    source: Literal["title", "manual"] = "title"
    reason: Literal["no_match", "ambiguous_match"] | None = None
