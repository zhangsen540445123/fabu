"""Read-only delivery summaries; never promote provider output to platform proof."""

from fabu.publishing.provider_tracking import observations_for_run


def summary_reason(reason, observations, target_ids):
    if reason != "provider_tracking_pending":
        return reason
    values = [observations.get(identifier) for identifier in target_ids]
    if any(value and value["status"] == "failed" for value in values):
        return "provider_reported_failure"
    if any(
        value and value["status"] == "unknown" and value["next_check_at"] is None
        for value in values
    ):
        return "provider_tracking_needs_attention"
    if values and all(value and value["status"] == "success" for value in values):
        return "provider_reported_success"
    return reason


def public_job(store, job):
    if job.pause_reason != "provider_tracking_pending":
        return job
    with store.connection() as db:
        row = db.execute(
            "SELECT id FROM publication_runs WHERE job_id=? AND scope='live' "
            "ORDER BY rowid DESC LIMIT 1",
            (job.id,),
        ).fetchone()
    if row is None:
        return job
    reason = summary_reason(
        job.pause_reason, observations_for_run(store, row[0]), job.spec.target_ids
    )
    return job.model_copy(update={"pause_reason": reason})
