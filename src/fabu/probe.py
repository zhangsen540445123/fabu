"""Bounded read-only UIA reader, isolated from the launcher and publishing process."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .client import Control


class IncompleteTree(RuntimeError):
    pass


def capture_control(wrapper, *, depth: int = 0, budget: list[int] | None = None) -> Control:
    if budget is None:
        budget = [8000]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise IncompleteTree("Tree exceeds safe limit")
    info = wrapper.element_info
    name, kind, automation_id = info.name or "", info.control_type or "", info.automation_id or ""
    value = ""
    visible = wrapper.is_visible()
    enabled = wrapper.is_enabled()
    # Edit values may contain credentials. Only visible Document source URLs are needed.
    if kind == "Document" and visible and enabled:
        from pywinauto.uia_defines import NoPatternInterfaceError

        try:
            value = wrapper.iface_value.CurrentValue or ""
        except NoPatternInterfaceError:
            try:
                value = wrapper.legacy_properties().get("Value", "") or ""
            except NoPatternInterfaceError:
                pass
    if any(len(text) > 2048 for text in (name, kind, automation_id, value)):
        raise IncompleteTree("Control metadata exceeds safe limit")
    children = (
        tuple(
            capture_control(child, depth=depth + 1, budget=budget) for child in wrapper.children()
        )
        if visible
        else ()
    )
    return Control(name, kind, automation_id, value, enabled, visible, children)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Internal read-only UIA probe")
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    output = {"hwnd": args.hwnd, "pid": args.pid, "controls": [], "error": "", "truncated": False}
    try:
        if sys.platform != "win32":
            raise RuntimeError("Windows required")
        import pythoncom
        import win32process

        pythoncom.CoInitialize()
        try:
            _, owner = win32process.GetWindowThreadProcessId(args.hwnd)
            if owner != args.pid:
                raise RuntimeError("Stale window owner")
            from pywinauto import Desktop

            wrapper = Desktop(backend="uia").window(handle=args.hwnd).wrapper_object()
            output["controls"] = [asdict(capture_control(wrapper))]
        finally:
            pythoncom.CoUninitialize()
    except IncompleteTree:
        output["truncated"] = True
        output["error"] = "tree_incomplete"
    except Exception:
        output["error"] = "uia_read_failed"
    print(json.dumps(output, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
