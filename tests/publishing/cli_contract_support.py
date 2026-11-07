"""Synthetic copies of envelopes observed from installed yxer 3.2.32 offline checks."""

from copy import deepcopy


def offline_response(payload, phase):
    request = deepcopy(payload)
    request.pop("action")
    account = request["publishArgs"]["accountForms"][0]
    request["cover"] = deepcopy(account["cover"])
    request["desc"] = account["contentPublishForm"]["description"]
    request["isAppContent"] = False
    platform = request["platforms"][0]
    if phase == "validate":
        data = {
            "valid": True,
            "prepared": True,
            "remoteChecks": False,
            "platform": platform,
            "type": "video",
            "request": request,
        }
        action = "validate"
    else:
        data = {
            "dryRun": True,
            "request": request,
            "meta": {
                "accountIds": [account["platformAccountId"]],
                "clientId": request["clientId"],
                "publishChannel": "local",
                "effectivePublishChannel": "local",
                "schemaChecked": True,
                "platform": platform,
                "publishType": "video",
                "remoteChecks": False,
                "platformDraft": False,
                "yixiaoerDraft": False,
                "normalizations": [],
                "contentImageMaterialization": [],
            },
        }
        action = "publish.dry-run"
    return {"ok": True, "version": "3.2.32", "action": action, "data": data}
