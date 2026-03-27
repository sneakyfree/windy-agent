"""Windy Fly — entry point.

Launch the agent via CLI or Matrix channel.
Usage: uv run python -m windyfly.main [--channel cli|matrix]
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from dotenv import load_dotenv

from windyfly.config import load_config

logger = logging.getLogger(__name__)

_DECAY_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours


def _start_decay_scheduler(
    config: dict,
    db_path: str,
) -> threading.Thread:
    """Start a daemon thread that runs cognitive decay every 24 hours."""
    from windyfly.memory.database import Database
    from windyfly.memory.decay import run_decay
    from windyfly.memory.write_queue import WriteQueue

    def _decay_loop() -> None:
        # Use a dedicated DB connection for the decay thread
        decay_db = Database(db_path)
        decay_wq = WriteQueue()
        decay_wq.start()
        logger.info("Decay scheduler started (interval: 24h)")
        while True:
            try:
                counts = run_decay(decay_db, decay_wq, config)
                logger.info("Decay cycle complete: %s", counts)
            except Exception as e:
                logger.error("Decay cycle failed: %s", e)
            time.sleep(_DECAY_INTERVAL_SECONDS)

    t = threading.Thread(target=_decay_loop, daemon=True, name="decay-scheduler")
    t.start()
    return t


def main() -> None:
    """Main entry point for Windy Fly."""
    load_dotenv()

    parser = argparse.ArgumentParser(description="Windy Fly — AI agent brain")
    parser.add_argument(
        "--channel",
        choices=["cli", "matrix", "sms"],
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

    # Start background decay scheduler (G7)
    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
    _start_decay_scheduler(config, db_path)

    # Launch channel
    if args.channel == "cli":
        from windyfly.channels.cli import run_cli
        run_cli(config)
    elif args.channel == "matrix":
        import asyncio

        from windyfly.channels.matrix_bot import WindyFlyMatrixBot
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.tools.registry import ToolRegistry
        from windyfly.tools.web_search import register_web_search_tool
        from windyfly.tools.windy_api import register_windy_tools

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()

        tool_registry = ToolRegistry()
        register_windy_tools(tool_registry)
        register_web_search_tool(tool_registry)

        # Register sub-agent tool (G11)
        from windyfly.agent.sub_agents import register_sub_agent_tool
        register_sub_agent_tool(tool_registry, config, db, write_queue)

        bot = WindyFlyMatrixBot(config, db, write_queue, tool_registry)
        try:
            asyncio.run(bot.start())
        except KeyboardInterrupt:
            asyncio.run(bot.stop())
        finally:
            write_queue.stop()
            db.close()
    elif args.channel == "sms":
        import asyncio
        from windyfly.channels.sms import WindyFlySMS
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()

        sms = WindyFlySMS(config, db, write_queue)
        logger.info("SMS channel initialized with number %s", sms.phone_number)
        # SMS channel runs via gateway webhooks, not a standalone loop.
        # Keep the process alive for the UDS bridge.
        try:
            asyncio.get_event_loop().run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            write_queue.stop()
            db.close()


if __name__ == "__main__":
    main()
