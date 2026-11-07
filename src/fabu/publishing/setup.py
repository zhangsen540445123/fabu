"""Explicit operator setup for automatic NEW jobs; never submits existing jobs."""

import argparse
import json
import os

from fabu.api.app import create_app
from fabu.launcher import _effective, _profile_environment
from fabu.publishing.defaults import set_default_preset
from fabu.settings import load_settings
from fabu.store import JobStore


def main():
    parser = argparse.ArgumentParser(description="Authorize new local automatic publishing jobs")
    parser.add_argument("--enable-new-jobs", action="store_true", required=True)
    parser.add_argument("--acknowledge-three-tasks", action="store_true", required=True)
    parser.add_argument("--default-preset")
    args = parser.parse_args()
    settings = _effective(load_settings(_profile_environment(os.environ, require_existing=True)))
    app = create_app(settings, JobStore(settings.db_path), enable_worker=False)
    app.state.live_policy.enable()
    if args.default_preset:
        set_default_preset(app, args.default_preset)
    print(json.dumps(app.state.live_policy.readiness(), ensure_ascii=False))


if __name__ == "__main__":
    main()
