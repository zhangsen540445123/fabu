"""Pure, immutable encoder selection. No runtime fallback inside an encode."""

from fabu.media.models import (
    ExecutionProfile,
    MediaPlan,
    PlanInvalid,
    plan_digest,
    verify_plan_digest,
)


class HardwareUnavailable(PlanInvalid):
    pass


def bind_execution(
    plan: MediaPlan, report=None, *, previous: MediaPlan | None = None, preset=None
) -> MediaPlan:
    verify_plan_digest(plan)
    intent = plan.output_profile.acceleration

    def finish(bound):
        if preset is not None:
            from fabu.media.compiler import bind_tool_identity

            return bind_tool_identity(bound, preset, bound.tool_identity)
        return bound.model_copy(update={"digest": plan_digest(bound)})

    if previous is not None:
        verify_plan_digest(previous)
        candidate = plan.model_copy(update={"execution": previous.execution})
        candidate = finish(candidate)
        if candidate != previous:
            raise HardwareUnavailable("processing_snapshot_changed")
        if previous.execution and previous.execution.path == "mixed":
            if (
                report is None
                or not report.available
                or report.fingerprint != previous.execution.device_fingerprint
            ):
                raise HardwareUnavailable("gpu_environment_changed")
        return candidate
    if intent is None:
        return plan
    if intent.mode == "cpu":
        execution = ExecutionProfile(path="cpu", encoder="libx265", reason="cpu_requested")
    elif report is None:
        raise HardwareUnavailable("gpu_probe_required")
    elif report.available:
        execution = ExecutionProfile(
            path="mixed",
            encoder="hevc_nvenc",
            reason="nvenc_verified",
            device_fingerprint=report.fingerprint,
            device_name=report.device_name,
            gpu_index=0,
            nvenc_preset="p5",
            cq=20,
        )
    elif intent.mode == "gpu":
        raise HardwareUnavailable("gpu_" + report.reason)
    else:
        execution = ExecutionProfile(path="cpu", encoder="libx265", reason=report.reason)
    bound = plan.model_copy(update={"execution": execution})
    return finish(bound)


def encoder_args(plan: MediaPlan) -> list[str]:
    intent, execution = plan.output_profile.acceleration, plan.execution
    if intent is not None and execution is None:
        raise PlanInvalid("Acceleration must be resolved before rendering")
    if execution is not None:
        # model_copy is used internally; never trust it to have validated field updates.
        execution = ExecutionProfile.model_validate(execution.model_dump())
        if (
            intent is None
            or (intent.mode == "cpu" and execution.path != "cpu")
            or (intent.mode == "gpu" and execution.path != "mixed")
        ):
            raise PlanInvalid("Encoder selection is incompatible with preset intent")
        if execution.path == "mixed":
            return [
                "-c:v",
                "hevc_nvenc",
                "-gpu",
                "0",
                "-preset",
                execution.nvenc_preset,
                "-tune",
                "hq",
                "-rc",
                "vbr",
                "-cq",
                str(execution.cq),
                "-b:v",
                "0",
            ]
    p = plan.output_profile
    return [
        "-c:v",
        p.codec,
        "-preset",
        p.preset,
        "-crf",
        str(p.crf),
        "-x265-params",
        "pools=6:frame-threads=2:log-level=error",
    ]
