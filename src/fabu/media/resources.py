"""Materialize only compiler-owned resources in a newly created render directory."""

import re
import shutil
from pathlib import Path

from fabu.assets import assert_no_reparse, sha256_file
from fabu.media.models import MediaPlan, PlanInvalid, verify_plan_digest

_NAME = re.compile(r"[a-zA-Z0-9_-]+\.(txt|ttf|otf)")


def materialize_resources(plan: MediaPlan, directory: Path) -> Path:
    verify_plan_digest(plan)
    if not directory.is_absolute() or not plan.video_graph:
        raise PlanInvalid("Render resources need an absolute directory and compiled graph")
    names = list(plan.text_files) + list(plan.font_copies)
    if len(names) != len(set(names)) or any(not _NAME.fullmatch(n) for n in names):
        raise PlanInvalid("Unsafe or duplicate generated resource filename")
    assert_no_reparse(directory)
    directory.mkdir(exist_ok=False)
    for name, text in plan.text_files.items():
        with (directory / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    for name, identifier in plan.font_copies.items():
        asset = plan.asset_refs.get(identifier)
        if not asset or asset.media_type != "font":
            raise PlanInvalid("A required font asset is missing")
        assert_no_reparse(asset.local_path)
        with asset.local_path.open("rb") as src, (directory / name).open("xb") as dest:
            shutil.copyfileobj(src, dest)
        if sha256_file(directory / name) != plan.asset_hashes.get(identifier):
            raise PlanInvalid("Font snapshot integrity failed")
    graph = directory / "filtergraph.txt"
    with graph.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(plan.video_graph)
    return graph
