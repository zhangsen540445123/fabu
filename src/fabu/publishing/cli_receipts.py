"""Installed CLI submission receipts acknowledge creation, never platform publication."""

import re

from fabu.publishing.gateway import SubmissionReceipt
from fabu.publishing.models import CLI_CONTRACT_VERSION

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


def parse_publish_acknowledgement(raw, *, transport_ok, target_id, client_id):
    """Only consume stdout of the one bound publish command, not user-supplied evidence.

    yxer 3.2.32 normalizes a successful gateway result to taskSetId/response. It
    does not echo the route. Missing route evidence is not a route mismatch, but
    must not turn into a claim that the platform accepted or published content.
    """
    raw = raw if isinstance(raw, dict) else {}
    data = raw.get("data")
    data = data if isinstance(data, dict) else {}
    identifier = data.get("taskSetId")
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        identifier = None

    def unknown(reason):
        return SubmissionReceipt(
            target_id=target_id,
            state="UNKNOWN",
            provider_task_id=identifier,
            reason=reason,
        )

    if not transport_ok or raw.get("ok") is not True or raw.get("version") != CLI_CONTRACT_VERSION:
        return unknown("submission_outcome_unknown")
    echoed = "publishChannel" in data or "clientId" in data
    if echoed and (data.get("publishChannel") != "local" or data.get("clientId") != client_id):
        return unknown("channel_mismatch")
    if raw.get("action") != "publish" or not data.keys() <= {
        "taskSetId",
        "response",
        "publishChannel",
        "clientId",
    }:
        return unknown("submission_receipt_unverified")
    if not identifier:
        return unknown("missing_provider_identity")
    # The official normalizer retains object-valued provider data under response.
    # Do not mistake a nested contradiction for missing evidence. Unknown nested
    # shapes remain unverified rather than recursively searching for a success.
    if "response" in data:
        response = data["response"]
        if isinstance(response, str):
            if response != identifier:
                return unknown("provider_identity_mismatch")
        elif isinstance(response, dict):
            if not response.keys() <= {"taskSetId", "id", "publishChannel", "clientId"}:
                return unknown("submission_receipt_unverified")
            for field in ("taskSetId", "id"):
                if field in response and response[field] != identifier:
                    return unknown("provider_identity_mismatch")
            nested_echo = "publishChannel" in response or "clientId" in response
            if nested_echo and (
                response.get("publishChannel") != "local" or response.get("clientId") != client_id
            ):
                return unknown("channel_mismatch")
            # Even a nested echo does not prove platform execution; retain the
            # original conservative direct-echo policy for actual_channel.
        else:
            return unknown("submission_receipt_unverified")
    return SubmissionReceipt(
        target_id=target_id,
        state="PROVIDER_ACCEPTED",
        provider_task_id=identifier,
        actual_channel="local" if echoed else None,
        reason=None if echoed else "provider_accepted_channel_unconfirmed",
    )
