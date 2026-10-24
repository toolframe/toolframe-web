"""Private build adapter and standalone author-time staging command."""

import argparse
import json
import sys
from pathlib import Path

from .prepare import stage


def main(argv=None):
    parser = argparse.ArgumentParser(prog="toolframe-web")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("stage")
    prepare.add_argument("source", type=Path)
    prepare.add_argument("destination", type=Path)
    prepare.add_argument("--cache", type=Path, required=True)
    prepare.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                stage(args.source, args.destination, args.cache, offline=args.offline)
            )
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    return 0
