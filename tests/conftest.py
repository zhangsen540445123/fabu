"""Live side effects are never implicitly authorized by the normal test suite."""

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("live"):
            raise pytest.UsageError(
                "Live operations require a separate scope-bound controlled-verification runner. "
                "This test runner cannot authorize uploads or publication."
            )
