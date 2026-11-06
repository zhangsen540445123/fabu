"""One shared timeline for trim/speed, with explicit main/full audio effects."""

from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field

from fabu.media.models import PlanInvalid
from fabu.media.probe import MediaInfo, validate_supported_input
from fabu.media.registry import AudioParams, SpeedParams, TrimParams
from fabu.presets import StepSpec


def seconds(value: Fraction) -> str:
    return f"{float(value):.9f}".rstrip("0").rstrip(".") or "0"


class TimingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    main_duration_ms: int
    main_duration: Fraction
    audio_duration: Fraction
    trim_start: Fraction
    trim_end: Fraction
    speed: Fraction
    video_filters: list[str] = Field(default_factory=list)
    audio_filters: list[str] = Field(default_factory=list)
    main_audio_effects: list[str] = Field(default_factory=list)
    full_audio_effects: list[str] = Field(default_factory=list)


def map_main_time(source_ms: int, trim_start_ms: int, speed: Fraction) -> int:
    if speed <= 0 or source_ms < trim_start_ms:
        raise PlanInvalid("Source time cannot map before the selected main segment")
    return round(Fraction(source_ms - trim_start_ms) / speed)


def audio_effects(params: AudioParams, duration: Fraction) -> list[str]:
    fade_in, fade_out = Fraction(params.fade_in_ms, 1000), Fraction(params.fade_out_ms, 1000)
    if fade_in > duration or fade_out > duration:
        raise PlanInvalid("Audio fade interval exceeds its selected scope duration")
    result = []
    if params.gain_db:
        result.append(f"volume={params.gain_db}dB")
    if fade_in:
        result.append(f"afade=t=in:st=0:d={seconds(fade_in)}")
    if fade_out:
        result.append(f"afade=t=out:st={seconds(duration - fade_out)}:d={seconds(fade_out)}")
    return result


def compile_timing(
    info: MediaInfo, steps: list[StepSpec], *, full_duration: Fraction | None = None
) -> TimingResult:
    validate_supported_input(info)
    start, end, speed = Fraction(0), info.duration, Fraction(1)
    trimmed = False
    for step in steps:
        if not step.enabled:
            continue
        if step.kind == "trim":
            p = TrimParams.model_validate(step.params)
            start, end = Fraction(p.start_ms, 1000), Fraction(p.end_ms, 1000)
            if end > info.duration:
                raise PlanInvalid("Trim end exceeds the source duration")
            trimmed = True
        elif step.kind == "speed":
            speed = Fraction(str(SpeedParams.model_validate(step.params).rate))
    duration = (end - start) / speed
    offset = info.audio.start_time - info.start_time
    original_audio = max(Fraction(0), info.audio.duration + offset)
    video, audio = [], []
    if trimmed:
        video.extend([f"trim=start={seconds(start)}:end={seconds(end)}", "setpts=PTS-STARTPTS"])
        audio.extend(
            [
                f"apad=whole_dur={seconds(end)}",
                f"atrim=start={seconds(start)}:end={seconds(end)}",
                "asetpts=PTS-STARTPTS",
            ]
        )
    audio_duration = (end - start if trimmed else original_audio) / speed
    if speed != 1:
        video.append(f"setpts=PTS*{speed.denominator}/{speed.numerator}")
        audio.extend(
            [
                f"atempo={seconds(speed)}",
                f"apad=whole_dur={seconds(audio_duration)}",
                f"atrim=duration={seconds(audio_duration)}",
                "asetpts=PTS-STARTPTS",
            ]
        )
    main_effects, full_effects = [], []
    for step in steps:
        if step.enabled and step.kind == "audio_effects":
            effects = audio_effects(
                AudioParams.model_validate(step.params),
                duration if step.scope == "main" else (full_duration or duration),
            )
            (main_effects if step.scope == "main" else full_effects).extend(effects)
    return TimingResult(
        main_duration_ms=round(duration * 1000),
        main_duration=duration,
        audio_duration=audio_duration,
        trim_start=start,
        trim_end=end,
        speed=speed,
        video_filters=video,
        audio_filters=audio,
        main_audio_effects=main_effects,
        full_audio_effects=full_effects,
    )
