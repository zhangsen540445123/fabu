"""Reuse synthetic publication fixtures; no live services are configured."""

import test_publish_api
from publishing import conftest as source

publishing_context = source.publishing_context
publish_app = test_publish_api.publish_app
