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
        import asyncio

        from windyfly.channels.matrix_bot import WindyFlyMatrixBot
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue

        db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()

        bot = WindyFlyMatrixBot(config, db, write_queue)
        try:
            asyncio.run(bot.start())
        except KeyboardInterrupt:
            asyncio.run(bot.stop())
        finally:
            write_queue.stop()
            db.close()


if __name__ == "__main__":
    main()
