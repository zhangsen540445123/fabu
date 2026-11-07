import pytest

from fabu.publishing.progress import summary_reason


@pytest.mark.parametrize(
    "values, expected",
    [
        ({}, "provider_tracking_pending"),
        ({"a": {"status": "success", "next_check_at": None}}, "provider_tracking_pending"),
        (
            {
                "a": {"status": "success", "next_check_at": None},
                "b": {"status": "success", "next_check_at": None},
            },
            "provider_reported_success",
        ),
        ({"a": {"status": "failed", "next_check_at": None}}, "provider_reported_failure"),
        ({"a": {"status": "unknown", "next_check_at": None}}, "provider_tracking_needs_attention"),
        ({"a": {"status": "unknown", "next_check_at": "later"}}, "provider_tracking_pending"),
    ],
)
def test_summary_never_promotes_platform_state(values, expected):
    assert summary_reason("provider_tracking_pending", values, ["a", "b"]) == expected
    assert (
        summary_reason("operator_submission_reconcile_required", values, ["a", "b"])
        == "operator_submission_reconcile_required"
    )
