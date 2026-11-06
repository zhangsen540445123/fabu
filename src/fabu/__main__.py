"""Run the loopback service. Normal startup cannot enable real publishing."""

import argparse
import threading
import webbrowser

import uvicorn

from fabu.api.app import create_app
from fabu.settings import check_environment, load_settings
from fabu.store import JobStore


def main():
    parser = argparse.ArgumentParser(description="Fabu local video workflow")
    parser.add_argument("--check", action="store_true", help="Read-only local path diagnostics")
    parser.add_argument("--open-browser", action="store_true", help="Open authenticated local UI")
    args = parser.parse_args()
    settings = load_settings()
    if args.check:
        print(check_environment(settings).model_dump_json(indent=2))
        return
    app = create_app(settings, JobStore(settings.db_path), enable_worker=True)
    if args.open_browser:
        url = f"http://127.0.0.1:{settings.port}/#bootstrap={app.state.security.bootstrap_token}"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # Access logs are disabled: bootstrap tokens are never printed or stored in request logs.
    uvicorn.run(app, host=settings.bind_host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
