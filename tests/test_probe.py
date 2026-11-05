from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest

from fabu.probe import capture_control


class Document:
    element_info = SimpleNamespace(name="页面", control_type="Document", automation_id="doc")

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def children(self):
        return []


def test_document_uses_actual_supported_legacy_contract():
    pytest.importorskip("pywinauto")
    from unittest.mock import Mock

    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_defines import NoPatternInterfaceError

    assert hasattr(UIAWrapper, "legacy_properties")
    wrapper = Mock(spec=UIAWrapper)
    wrapper.element_info = Document.element_info
    wrapper.is_visible.return_value = True
    wrapper.is_enabled.return_value = True
    wrapper.children.return_value = []
    wrapper.legacy_properties.return_value = {"Value": "https://www.yixiaoer.cn/web"}
    with patch.object(
        type(wrapper), "iface_value", new_callable=PropertyMock, create=True
    ) as value:
        value.side_effect = NoPatternInterfaceError()
        captured = capture_control(wrapper)
    assert captured.value == "https://www.yixiaoer.cn/web"
    wrapper.legacy_properties.assert_called_once()


def test_edit_value_is_never_collected():
    class Edit(Document):
        element_info = SimpleNamespace(name="验证码", control_type="Edit", automation_id="otp")

        @property
        def iface_value(self):
            pytest.fail("Do not read credentials from Edit")

    control = capture_control(Edit())
    assert control.value == ""


def test_invisible_document_value_is_not_read():
    class Hidden(Document):
        def is_visible(self):
            return False

        @property
        def iface_value(self):
            pytest.fail("Do not read hidden document values")

    assert capture_control(Hidden()).value == ""
