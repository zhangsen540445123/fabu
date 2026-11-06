"""Live side effects are never implicitly authorized by the normal test suite."""

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("live"):
            raise pytest.UsageError(
                "Live operations require a separate scope-bound controlled-verification runner. "
                "This test runner cannot authorize uploads or publication."
            )


@pytest.fixture
def app(tmp_path):
    from fabu.api.app import create_app
    from fabu.settings import load_settings
    from fabu.store import JobStore

    settings = load_settings(
        {"LOCALAPPDATA": str(tmp_path), "FABU_DATA_DIR": str(tmp_path / "media")}
    )
    return create_app(settings, JobStore(settings.db_path))
