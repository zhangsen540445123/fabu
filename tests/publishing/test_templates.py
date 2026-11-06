import pytest


@pytest.mark.parametrize(
    "title,group,tags",
    [
        ("剃须仙人的日常", "WRHkkYwU", ["解压", "ASMR", "剃须仙人", "助眠"]),
        ("剃光头", "2aZjVP8u", ["ASMR", "解压", "助眠", "剃光头"]),
        ("沉浸式剪发", "QKHLZMFO", ["解压", "助眠", "ASMR", "剪发"]),
        ("  剃光头的日常  ", "2aZjVP8u", ["ASMR", "解压", "助眠", "剃光头"]),
    ],
)
def test_title_topic_rules(title, group, tags):
    from fabu.publishing.topics import select_topic_group

    selected = select_topic_group(title, None)
    assert selected.status == "selected" and selected.group_name == group
    assert list(selected.tags) == tags and group not in selected.tags
    assert selected.source == "title"


@pytest.mark.parametrize(
    "title,reason",
    [("剃光头与剪发", "ambiguous_match"), ("助眠视频", "no_match"), ("普通标题", "no_match")],
)
def test_zero_or_multiple_matches_pause_without_default(title, reason):
    from fabu.publishing.topics import select_topic_group

    selected = select_topic_group(title, None)
    assert selected.status == "needs_selection" and selected.reason == reason
    assert selected.group_name is None and not selected.tags


def test_manual_topic_override_wins():
    from fabu.publishing.topics import select_topic_group

    selected = select_topic_group("剃光头与剪发", "助眠")
    assert selected.status == "selected" and selected.group_name == "助眠"
    assert selected.source == "manual" and list(selected.tags) == ["解压", "助眠"]
    with pytest.raises(ValueError):
        select_topic_group("剃光头", "unrecognized-group")
