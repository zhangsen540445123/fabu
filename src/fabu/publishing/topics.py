"""User-confirmed topic snapshots; title matching never selects a media-processing preset."""

from fabu.publishing.models import TopicSelection

GROUPS = {
    "助眠": ("解压", "助眠"),
    "2aZjVP8u": ("ASMR", "解压", "助眠", "剃光头"),
    "QKHLZMFO": ("解压", "助眠", "ASMR", "剪发"),
    "WRHkkYwU": ("解压", "ASMR", "剃须仙人", "助眠"),
}
RULES = (("剃须仙人", "WRHkkYwU"), ("剃光头", "2aZjVP8u"), ("剪发", "QKHLZMFO"))


def select_topic_group(title: str, override_group: str | None) -> TopicSelection:
    if override_group is not None:
        if override_group not in GROUPS:
            raise ValueError("Select a known topic group")
        return TopicSelection(
            status="selected",
            group_name=override_group,
            tags=GROUPS[override_group],
            source="manual",
        )
    matches = tuple(group for keyword, group in RULES if keyword in title.strip())
    if len(matches) != 1:
        return TopicSelection(
            status="needs_selection",
            matches=matches,
            reason="no_match" if not matches else "ambiguous_match",
        )
    return TopicSelection(
        status="selected", group_name=matches[0], tags=GROUPS[matches[0]], matches=matches
    )
