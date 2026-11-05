"""Only client diagnostics/startup are available until milestone 1 passes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .client import ClientLauncher, ClientState, result
from .windows import (
    ClientBusyError,
    ConfigurationError,
    WindowsClientHost,
    client_mutex,
    discover_shortcut,
    resolve_shortcut,
)


def bounded_timeout(text: str) -> float:
    try:
        value = float(text)
        if not 0 < value <= 90:
            raise ValueError
        return value
    except ValueError as error:
        raise argparse.ArgumentTypeError("Timeout must be greater than 0 and at most 90") from error


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Fabu 蚁小二客户端诊断（未启用投稿）")
    commands = parser.add_subparsers(dest="command", required=True)
    client_parser = commands.add_parser("client", help="客户端状态检查与启动")
    actions = client_parser.add_subparsers(dest="action", required=True)
    for action in ("inspect", "start"):
        subparser = actions.add_parser(action)
        subparser.add_argument("--shortcut", type=Path, help="明确指定桌面 .lnk 快捷方式")
        subparser.add_argument("--timeout", type=bounded_timeout, default=90)
    args = parser.parse_args(argv)
    try:
        shortcut = resolve_shortcut(discover_shortcut(args.shortcut))
        host = WindowsClientHost()
        if args.action == "inspect":
            from .safety import diagnostic_report

            report = diagnostic_report(host.observe(shortcut, timeout=min(12, args.timeout)))
        else:
            with client_mutex():
                readiness = ClientLauncher(host, timeout=args.timeout).ensure_ready(shortcut)
            report = {
                "state": readiness.state.value,
                "reason": readiness.reason,
                "scene": readiness.scene,
                "publication_enabled": False,
            }
    except ClientBusyError:
        report = {
            "state": "controller_busy",
            "reason": "另一个任务正在控制蚁小二。",
            "publication_enabled": False,
        }
    except ConfigurationError:
        readiness = result(ClientState.CONFIG_ERROR)
        report = {
            "state": readiness.state.value,
            "reason": readiness.reason,
            "publication_enabled": False,
        }
    except Exception:
        readiness = result(ClientState.INSPECTION_FAILED)
        report = {
            "state": readiness.state.value,
            "reason": readiness.reason,
            "publication_enabled": False,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["state"] == ClientState.READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
