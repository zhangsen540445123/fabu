"""Typed local preset and asset management; only the backend decides pipeline validity."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from fabu.assets import MediaType
from fabu.media.models import OutputProfile
from fabu.media.registry import PARAM_SCHEMAS, validate_steps
from fabu.presets import PresetDocument

router = APIRouter(prefix="/api")
LABELS = {
    "trim": "截取片段",
    "speed": "调整速度",
    "crop": "裁切画面",
    "flip": "水平翻转",
    "color": "亮度与色彩",
    "layout": "画布与装饰区",
    "image_layer": "图片图层",
    "text_layer": "文字图层",
    "subtitles": "导入字幕",
    "intro": "片头",
    "outro": "片尾",
    "audio_effects": "音量与淡入淡出",
}


class AssetImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    media_type: MediaType


def _issues(document):
    messages = {
        "duplicate_id": "步骤标识重复",
        "unknown_step": "不支持此处理方式",
        "invalid_params": "处理参数不完整或超出范围",
        "invalid_scope": "该步骤不能用于所选作用范围",
        "duplicate_step": "同一作用范围内不能重复添加此步骤",
        "invalid_order": "步骤顺序不符合处理阶段，请手动调整",
        "missing_asset": "请选择所需素材",
        "duplicate_asset": "重复的素材引用",
        "asset_mismatch": "参数与素材引用不一致",
    }
    issues = [
        {**i.model_dump(), "message": messages.get(i.code, i.message)}
        for i in validate_steps(document.steps)
    ]
    try:
        OutputProfile.model_validate(document.output)
    except ValueError:
        issues.append(
            {"step_id": "output", "code": "invalid_output", "message": "输出参数不受支持"}
        )
    return issues


def _save(request, identifier, document):
    issues = _issues(document)
    if issues:
        raise HTTPException(422, {"issues": issues})
    try:
        return request.app.state.presets.save_draft(identifier, document.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "Preset not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(
            422,
            {
                "issues": [
                    {
                        "step_id": "assets",
                        "code": "asset_missing",
                        "message": "素材缺失或完整性检查失败，请重新选择",
                    }
                ]
            },
        ) from exc


@router.get("/presets/catalog")
def catalog():
    result = []
    for kind, schema in PARAM_SCHEMAS.items():
        main_only = kind in {"trim", "crop", "flip", "color", "speed", "layout"}
        scopes = (
            ["main"]
            if main_only
            else (["full"] if kind in {"intro", "outro"} else ["main", "full"])
        )
        result.append(
            {
                "kind": kind,
                "label": LABELS[kind],
                "parameters": schema.model_json_schema(),
                "scopes": scopes,
                "multiple": kind in {"image_layer", "text_layer", "subtitles", "audio_effects"},
            }
        )
    return result


@router.post("/presets/validate")
def validate(document: PresetDocument):
    issues = _issues(document)
    return {"valid": not issues, "issues": issues}


@router.get("/presets")
def list_presets(request: Request):
    with request.app.state.store.connection() as db:
        rows = db.execute(
            "SELECT v.id FROM preset_versions v WHERE v.revision=(SELECT MAX(revision) "
            "FROM preset_versions WHERE preset_id=v.preset_id) ORDER BY v.created_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        version = request.app.state.presets.get_version(row[0])
        try:
            refs = {
                a: request.app.state.assets.get(a)
                for s in version.steps
                if s.enabled
                for a in s.asset_ids
            }
            approved = request.app.state.approvals.approval_matches(
                version, refs, request.app.state.approvals.current_engine()
            )
        except (KeyError, ValueError, OSError):
            approved = False
        result.append({"version": version, "approved": approved})
    return result


@router.post("/presets", status_code=201)
def create_preset(document: PresetDocument, request: Request):
    return _save(request, None, document)


@router.get("/presets/{preset_id}/versions")
def versions(preset_id: str, request: Request):
    with request.app.state.store.connection() as db:
        rows = db.execute(
            "SELECT id FROM preset_versions WHERE preset_id=? ORDER BY revision DESC", (preset_id,)
        ).fetchall()
    if not rows:
        raise HTTPException(404, "Preset not found")
    return [request.app.state.presets.get_version(row[0]) for row in rows]


@router.post("/presets/{preset_id}/versions", status_code=201)
def new_version(preset_id: str, document: PresetDocument, request: Request):
    return _save(request, preset_id, document)


def _asset_view(request, ref):
    with request.app.state.store.connection() as db:
        row = db.execute(
            "SELECT display_name FROM asset_labels WHERE asset_id=?", (ref.id,)
        ).fetchone()
    return {
        **ref.model_dump(mode="json"),
        "display_name": row[0] if row else f"{ref.media_type} {ref.id[:8]}",
    }


@router.post("/assets", status_code=201)
def import_asset(command: AssetImport, request: Request):
    try:
        ref = request.app.state.assets.import_file(command.path, command.media_type)
        with request.app.state.store.transaction() as db:
            db.execute("INSERT INTO asset_labels VALUES (?,?)", (ref.id, command.path.name))
        return _asset_view(request, ref)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, "无法导入素材：检查路径、格式和可用空间") from exc


@router.get("/assets")
def list_assets(request: Request):
    with request.app.state.store.connection() as db:
        rows = db.execute("SELECT id FROM assets ORDER BY created_at DESC LIMIT 500").fetchall()
    return [_asset_view(request, request.app.state.assets.get(row[0])) for row in rows]


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: str, request: Request):
    try:
        ref = request.app.state.assets.get(asset_id)
        if ref.media_type not in {"image", "video"} or not request.app.state.assets.verify(
            asset_id
        ):
            raise ValueError("not a viewable verified asset")
        mime = {
            ".mp4": "video/mp4",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }[ref.local_path.suffix.lower()]
        return FileResponse(ref.local_path, media_type=mime)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(404, "Viewable asset not found") from exc
