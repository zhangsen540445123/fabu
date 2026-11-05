import importlib

import pytest


class MissingModule:
    def __getattr__(self, name):
        pytest.fail(f"Client module not implemented: {name}", pytrace=False)


@pytest.fixture
def client():
    try:
        return importlib.import_module("fabu.client")
    except ModuleNotFoundError as error:
        if error.name == "fabu.client":
            return MissingModule()
        raise
