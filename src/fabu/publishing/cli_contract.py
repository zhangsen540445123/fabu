"""Bind offline CLI checks to the exact local request, never to publication success.

Envelope shapes verified with installed yxer 3.2.32 and an empty isolated config.
These observations do not establish the transport/submission receipt contract.
"""

import math
from copy import deepcopy

from fabu.publishing.models import CLI_CONTRACT_VERSION


def _same_json(left, right):
    # Go JSON round-trips 1.0 as 1. Never extend that equivalence to bools or strings.
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def matches_local_preflight(raw: dict, payload: dict, phase: str) -> bool:
    """Accept only the observed validate/dry-run shape for an unchanged local request."""
    if phase not in {"validate", "dry_run"} or not isinstance(raw, dict):
        return False
    action = "validate" if phase == "validate" else "publish.dry-run"
    if (
        raw.get("ok") is not True
        or raw.get("version") != CLI_CONTRACT_VERSION
        or raw.get("action") != action
    ):
        return False
    try:
        expected = deepcopy(payload)
        if (
            expected.pop("action") != "publish"
            or expected["publishChannel"] != "local"
            or expected["publishType"] != "video"
            or expected["isDraft"] is not False
            or len(expected["platforms"]) != 1
        ):
            return False
        accounts = expected["publishArgs"]["accountForms"]
        if len(accounts) != 1:
            return False
        account = accounts[0]
        # Only these three top-level inferences were observed. No field rewriting allowed.
        expected.setdefault("cover", deepcopy(account["cover"]))
        expected.setdefault("desc", account["contentPublishForm"]["description"])
        expected.setdefault("isAppContent", False)
        if expected["isAppContent"] is not False:
            return False
        data = raw["data"]
        if not isinstance(data, dict) or not _same_json(data.get("request"), expected):
            return False
        platform = expected["platforms"][0]
        if phase == "validate":
            return (
                data.get("valid") is True
                and data.get("prepared") is True
                and data.get("remoteChecks") is False
                and data.get("platform") == platform
                and data.get("type") == "video"
            )
        meta = data.get("meta")
        return (
            data.get("dryRun") is True
            and isinstance(meta, dict)
            and meta.get("clientId") == expected["clientId"]
            and meta.get("publishChannel") == "local"
            and meta.get("effectivePublishChannel") == "local"
            and meta.get("accountIds") == [account["platformAccountId"]]
            and meta.get("platform") == platform
            and meta.get("publishType") == "video"
            and meta.get("schemaChecked") is True
            and meta.get("remoteChecks") is False
            and meta.get("platformDraft") is False
            and meta.get("yixiaoerDraft") is False
            and meta.get("normalizations") == []
            and meta.get("contentImageMaterialization") == []
        )
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError):
        return False
