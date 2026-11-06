from pathlib import Path

import pytest
from pydantic import ValidationError


def test_paths_resolve_for_actual_user(tmp_path):
    from fabu.settings import load_settings

    local = tmp_path / "用户配置"
    settings = load_settings({"LOCALAPPDATA": str(local)})
    assert settings.state_dir == local / "Fabu"
    assert settings.db_path == local / "Fabu" / "fabu.sqlite3"
    assert settings.data_dir == Path("D:/FabuData/jobs")
    assert settings.bind_host == "127.0.0.1"
    assert settings.test_mode == "simulation"
    assert not local.exists(), "Reading configuration must not create directories"


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.168.1.2", "::"])
def test_public_bind_is_rejected(tmp_path, host):
    from fabu.settings import load_settings

    with pytest.raises((ValidationError, ValueError)):
        load_settings({"LOCALAPPDATA": str(tmp_path), "FABU_BIND_HOST": host})


def test_explicit_paths_with_spaces_are_preserved(tmp_path):
    from fabu.settings import load_settings

    data = tmp_path / "视频 [1] & 日常"
    state = tmp_path / "私人 设置"
    settings = load_settings(
        {
            "LOCALAPPDATA": str(tmp_path),
            "FABU_DATA_DIR": str(data),
            "FABU_STATE_DIR": str(state),
        }
    )
    assert settings.data_dir == data
    assert settings.db_path == state / "fabu.sqlite3"


@pytest.mark.parametrize("field", ["FABU_DATA_DIR", "FABU_STATE_DIR", "FABU_TOOLS_DIR"])
def test_existing_file_cannot_be_a_directory(tmp_path, field):
    from fabu.settings import load_settings

    bad = tmp_path / "not_a_directory"
    bad.write_text("keep me", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        load_settings({"LOCALAPPDATA": str(tmp_path), field: str(bad)})
    assert bad.read_text() == "keep me"


def test_relative_paths_rejected(tmp_path):
    from fabu.settings import load_settings

    with pytest.raises(ValueError, match="absolute"):
        load_settings({"LOCALAPPDATA": str(tmp_path), "FABU_DATA_DIR": "relative/data"})


def test_missing_localappdata_not_guessed():
    from fabu.settings import load_settings

    with pytest.raises(ValueError, match="LOCALAPPDATA"):
        load_settings({})


def test_environment_report_is_read_only_and_contains_no_secret(tmp_path):
    from fabu.settings import check_environment, load_settings

    settings = load_settings(
        {
            "LOCALAPPDATA": str(tmp_path),
            "FABU_DATA_DIR": str(tmp_path / "media"),
            "YIXIAOER_API_KEY": "never-include-this-value",
        }
    )
    before = set(tmp_path.iterdir())
    report = check_environment(settings)
    assert report.python_version.startswith("3.12")
    assert report.platform == "Windows"
    assert report.paths["data_dir"].exists is False
    assert report.paths["data_dir"].free_bytes > 0
    assert report.paths["data_dir"].writable is None  # access check is not a write test
    assert "never-include-this-value" not in report.model_dump_json()
    assert set(tmp_path.iterdir()) == before


def test_unknown_execution_mode_cannot_enable_real_writes(tmp_path):
    from fabu.settings import load_settings

    with pytest.raises((ValidationError, ValueError)):
        load_settings({"LOCALAPPDATA": str(tmp_path), "FABU_TEST_MODE": "production"})
