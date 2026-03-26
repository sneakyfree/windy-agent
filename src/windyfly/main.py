"""Windy Fly — entry point.

Launch the agent via CLI or Matrix channel.
Usage: uv run python -m windyfly.main [--channel cli|matrix]
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from windyfly.config import load_config


def main() -> None:
    """Main entry point for Windy Fly."""
    load_dotenv()

    parser = argparse.ArgumentParser(description="Windy Fly — AI agent brain")
    parser.add_argument(
        "--channel",
        choices=["cli", "matrix"],
        default="cli",
        help="Channel to run (default: cli)",
    )
    parser.add_argument(
        "--config",
        default="windyfly.toml",
        help="Path to config file (default: windyfly.toml)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (DEBUG, INFO, WARNING, ERROR)",
    )
    args = parser.parse_args()

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Configure logging
    log_level = args.log_level or config.get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Launch channel
    if args.channel == "cli":
        from windyfly.channels.cli import run_cli
        run_cli(config)
    elif args.channel == "matrix":
        # Matrix bot will be implemented in Phase 1
        print("Matrix channel not yet implemented. Coming in Phase 1.")
        sys.exit(0)


if __name__ == "__main__":
    main()
