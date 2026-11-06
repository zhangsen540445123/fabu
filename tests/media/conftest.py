import os
from pathlib import Path

import pytest


@pytest.fixture
def media_tools():
    from fabu.media.runtime import MediaTools

    paths = [os.environ.get("FABU_TEST_FFMPEG"), os.environ.get("FABU_TEST_FFPROBE")]
    if not all(paths):
        pytest.fail("Real media acceptance requires FABU_TEST_FFMPEG/FABU_TEST_FFPROBE; no skip")
    return MediaTools(ffmpeg_path=Path(paths[0]), ffprobe_path=Path(paths[1]))
