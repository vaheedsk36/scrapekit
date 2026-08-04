"""Command-line entry point.  `python -m apartment_hunter run [--verbose]`"""
from __future__ import annotations

import argparse
from typing import Optional

from . import pipeline
from .config import load_config


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="apartment_hunter",
        description="AI apartment hunter — a Hermes-style scrape → match → alert pipeline.",
    )
    sub = parser.add_subparsers(dest="cmd")

    run_cmd = sub.add_parser("run", help="run one hunt cycle (CLI)")
    run_cmd.add_argument("--config", default=None,
                         help="path to a config file (json or yaml). "
                              "Defaults to the bundled demo config.json.")
    run_cmd.add_argument("--demo", action="store_true",
                         help="explicitly use the bundled demo data")
    run_cmd.add_argument("-v", "--verbose", action="store_true",
                         help="show per-listing scoring")

    serve_cmd = sub.add_parser("serve", help="launch the interactive web UI")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--config", default=None,
                           help="config file used for matcher/currency defaults")

    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return

    if args.cmd == "run":
        # --demo is just the default config.json; kept for a friendly CLI.
        cfg = load_config(args.config)
        pipeline.run(cfg, verbose=args.verbose)
    elif args.cmd == "serve":
        from . import server
        server.serve(port=args.port, config=args.config)


if __name__ == "__main__":
    main()
