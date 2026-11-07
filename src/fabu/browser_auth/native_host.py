"""Chrome native host entry point. stdout is protocol-only; no secret logging."""

import argparse
import os
import queue
import sys
import threading
import time
from pathlib import Path

from fabu.browser_auth.bridge import HostSession, _read
from fabu.browser_auth.cookies import BrowserSessionError
from fabu.browser_auth.protocol import decode_message, read_message, write_message


def run_host(root, origin, allowed_origin, input_stream, output_stream):
    messages = queue.Queue(maxsize=16)
    stop = threading.Event()

    def receive():
        try:
            while not stop.is_set():
                value = read_message(input_stream)
                messages.put(value, timeout=1)
                if value is None:
                    return
        except (OSError, ValueError, queue.Full):
            stop.set()

    try:
        with HostSession(root, origin, allowed_origin) as session:
            write_message(output_stream, {"type": "status", "version": 1, "status": "connected"})
            reader = threading.Thread(target=receive, daemon=True)
            reader.start()
            last_heartbeat = time.monotonic()
            while not stop.is_set():
                try:
                    message = messages.get(timeout=0.1)
                except queue.Empty:
                    message = False
                if message is None:
                    return 0
                if message is not False:
                    session.accept_response(message)
                if time.monotonic() - last_heartbeat >= 2:
                    session.heartbeat()
                    last_heartbeat = time.monotonic()
                for request in session.pending_requests():
                    write_message(output_stream, request)
            return 1
    except (OSError, ValueError):
        return 1
    finally:
        stop.set()


def main():
    parser = argparse.ArgumentParser(description="Fabu YouTube native session host")
    parser.add_argument("--config", required=True)
    parser.add_argument("origin")
    parser.add_argument("--parent-window")
    args = parser.parse_args()
    try:
        config_path = Path(args.config)
        if not config_path.is_absolute():
            return 1
        config = decode_message(_read(config_path))
        if (
            set(config) != {"version", "root", "allowed_origin"}
            or type(config["version"]) is not int
            or config["version"] != 1
        ):
            return 1
        root = Path(config["root"])
        if os.name == "nt":
            import msvcrt

            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        return run_host(
            root, args.origin, config["allowed_origin"], sys.stdin.buffer, sys.stdout.buffer
        )
    except (OSError, TypeError, BrowserSessionError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
