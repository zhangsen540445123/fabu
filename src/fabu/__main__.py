"""Default entrypoint shares the owned Windows runtime lifecycle."""

import argparse
import json

from fabu.launcher import check_installation, launch, stop_owned_runtime
from fabu.settings import load_settings


def main():
    parser = argparse.ArgumentParser(description="Fabu local video workflow")
    parser.add_argument("--check", action="store_true", help="Read-only installation diagnostics")
    parser.add_argument(
        "--stop", action="store_true", help="Gracefully stop only the owned runtime"
    )
    parser.add_argument(
        "--open-browser", action="store_true", help="Open authenticated UI (default)"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Start background runtime without opening UI"
    )
    args = parser.parse_args()
    settings = load_settings()
    try:
        if args.check:
            result = check_installation(settings)
        elif args.stop:
            result = {"stopped": stop_owned_runtime(settings)}
        else:
            result = launch(settings, open_browser=not args.no_browser).model_dump(mode="json")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
