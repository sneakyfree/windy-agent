"""Windy Fly — entry point.

Launch the agent on its one channel, Windy Chat (Matrix).
Usage: uv run python -m windyfly.main --channel matrix
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
# The cloud-backup due-check runs this often against cloud.backup_interval (default 24 h), so a restart can
# delay a backup by at most this much (it used to reset a 24 h sleep: gaps of 34-48 h on Windy 0, 10-21).
_BACKUP_CHECK_SECONDS = 60 * 60

# The launchable channels (`--channel <name>`). The lean Windy Fly has ONE: Windy Chat (Matrix). The other chat
# channels and the local CLI chat were cut (Grant 2026-11-02: one agent, one channel).
CHANNEL_CHOICES = ["matrix"]


def _backs_up(channel: str | None) -> bool:
    """Whether THIS process runs the cloud-backup check. An agent's telegram and matrix processes share one
    database: only the maintenance runner (agent.maintenance.maintenance_runner) uploads, so one backup is not
    sent twice (Windy 0, 10-15 and 10-20: the same backup twice, using two of Cloud's 5 slots). Any other
    process (cli, other channels) keeps backing up as before."""
    if channel not in ("telegram", "matrix"):
        return True
    from windyfly.agent.maintenance import maintenance_runner

    return channel == maintenance_runner()


def _start_decay_scheduler(
    config: dict,
    db_path: str,
    channel: str | None = None,
) -> threading.Thread:
    """Start a daemon thread: decay, drift, curation and retention every 24 hours; the cloud-backup
    due-check every hour (in the backup-running process only, see _backs_up)."""
    import asyncio

    from windyfly.memory.database import Database
    from windyfly.memory.decay import run_decay
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.personality.versioning import run_periodic_drift_check

    def _daily_cycle(decay_db, decay_wq) -> None:
        try:
            counts = run_decay(decay_db, decay_wq, config)
            logger.info("Decay cycle complete: %s", counts)
        except Exception as e:
            logger.error("Decay cycle failed: %s", e)
        # (run_decay also decays intents: one cycle for the job, /decay and decay.run)
        try:
            drift = run_periodic_drift_check(decay_db, decay_wq)
            if drift.get("drift_detected"):
                logger.warning("Personality drift detected: %s", drift["drift_report"])
            else:
                logger.info("Drift check complete: no drift detected")
        except Exception as e:
            logger.error("Drift check failed: %s", e)
        # Retention: prune high-churn bookkeeping tables (soul_history
        # heartbeat dupes + stale events) and VACUUM if it freed real
        # space. Runs BEFORE the backup so the uploaded DB is already
        # compacted — this is the fix for the 122 MB / 363k-row bloat
        # that broke cloud backup on Windy 0.
        try:
            from windyfly.memory.retention import run_retention
            rstats = run_retention(decay_db, config)
            logger.info("Retention complete: %s", rstats)
        except Exception as e:
            logger.error("Retention failed: %s", e)

    def _backup_check() -> None:
        try:
            from windyfly.cloud_backup import run_backup_if_due
            result = asyncio.run(run_backup_if_due(config))
            if result and not result.get("success"):
                # error is guaranteed non-empty by cloud_backup's
                # _describe_error; the `or` is a last-ditch guard so a
                # future caller can never reintroduce the blank line.
                logger.warning(
                    "Scheduled backup failed: %s",
                    result.get("error") or "unknown (no error detail)",
                )
        except Exception as e:
            # A raised (not returned) failure used to log at DEBUG —
            # invisible in prod, another way this stayed unexplained.
            logger.warning("Backup check raised: %s: %s", type(e).__name__, e)

    def _decay_loop() -> None:
        # Use a dedicated DB connection for the decay thread
        decay_db = Database(db_path)
        try:  # a memory file set aside at startup is announced at once, by one process (collapse_alert)
            from windyfly.memory.collapse_alert import announce
            announce(db_path)
        except Exception as e:  # noqa: BLE001
            logger.error("memory collapse alert failed: %s", type(e).__name__)
        decay_wq = WriteQueue()
        decay_wq.start()
        backs_up = _backs_up(channel)
        logger.info("Decay + drift scheduler started (24h); backup check %s",
                    "hourly in this process" if backs_up else "off here (the maintenance runner does it)")
        next_cycle = 0.0
        while True:
            if time.monotonic() >= next_cycle:
                next_cycle = time.monotonic() + _DECAY_INTERVAL_SECONDS
                _daily_cycle(decay_db, decay_wq)
            # The due-check runs hourly against cloud.backup_interval (default 24 h): a restart no longer
            # resets a 24 h sleep, so a backup is late by at most _BACKUP_CHECK_SECONDS.
            if backs_up:
                _backup_check()
            time.sleep(_BACKUP_CHECK_SECONDS)

    t = threading.Thread(target=_decay_loop, daemon=True, name="decay-scheduler")
    t.start()
    return t


def _configure_logging(channel: str, log_level: str) -> None:
    """Root logging for this process.

    Interactive chat (``--channel cli``): INFO chatter (runtime claim, tool
    registration, memory…) interleaved with the conversation made it
    unreadable, so the full log goes to data/cli.log and the terminal only
    shows WARNING and above. Every other channel logs to stderr as before
    (systemd/launchd capture it).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    if channel == "cli":
        from windyfly.platform import get_data_dir, get_project_root

        file_handler = logging.FileHandler(
            get_data_dir(get_project_root()) / "cli.log", encoding="utf-8"
        )
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S",
                            handlers=[file_handler, console_handler])
    else:
        logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")

def main() -> None:
    """Main entry point for Windy Fly."""
    # The agent's own .env lives in its project folder (where `windy go` /
    # `windy bring-home` wrote it). A bare load_dotenv() searches upward
    # from THIS module's directory, which on a pip install is
    # site-packages, so the project file must be named explicitly. Neither
    # call overrides variables already set (systemd EnvironmentFile wins).
    from windyfly.platform import get_project_root

    load_dotenv(get_project_root() / ".env")
    load_dotenv()
    # Synthetic marking again, now that the .env is in: a test-born agent's passport may live only there (windy
    # bring-home writes it), so the CLI's earlier install() could not see it yet (Hub on #647). Idempotent.
    from windyfly.observability import synthetic

    synthetic.install()

    parser = argparse.ArgumentParser(description="Windy Fly — AI agent brain")
    sub = parser.add_subparsers(dest="subcommand")

    # Legacy --channel mode
    parser.add_argument(
        "--channel",
        choices=CHANNEL_CHOICES,
        default="matrix",
        help="Channel to run (default: matrix, the only one)",
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

    # Subcommands
    sub.add_parser("status", help="Show agent status")
    sub.add_parser("doctor", help="Diagnose installation")
    sub.add_parser("test", help="Run self-test")

    args = parser.parse_args()

    # Handle subcommands first
    if args.subcommand == "status":
        from windyfly.cli_status import print_status
        print_status()
        return
    elif args.subcommand == "doctor":
        from windyfly.commands import cmd_doctor
        cmd_doctor(args)
        return
    elif args.subcommand == "test":
        from windyfly.cli_selftest import run_self_test
        run_self_test()
        return

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(
            f"Error: {e}\n"
            "Run 'windy go' to create one, or pass --config <path>.",
            file=sys.stderr,
        )
        sys.exit(1)
    if config.get("_config_error"):
        logger.error(
            "windyfly.toml could not be parsed (%s) — running on SAFE "
            "DEFAULTS. Custom settings are NOT active until the file is "
            "fixed or regenerated with 'windy go'.",
            config["_config_error"],
        )

    # Initialize Sentry error reporting (if configured)
    import os
    sentry_dsn = os.environ.get("SENTRY_DSN", "")
    if sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(
                dsn=sentry_dsn,
                environment=os.environ.get("SENTRY_ENV", "production"),
                traces_sample_rate=0.1,
                release=f"windyfly@{__import__('windyfly').__version__}",
            )
            logger.info("Sentry initialized")
        except ImportError:
            logger.debug("sentry-sdk not installed — skipping error reporting")

    # Initialize unified command registry (140 commands)
    from windyfly.commands.setup import init_all_commands
    init_all_commands(config=config)

    # Configure logging
    log_level = args.log_level or config.get("log_level", "INFO")
    _configure_logging(args.channel, log_level)
    try:  # the transport name on HQ's service.boot / service.health rows (Admin 10-29: alarm per channel)
        from windyfly.observability import agent_health

        agent_health.set_channel(args.channel)
    except Exception:  # noqa: BLE001  (telemetry never blocks a boot)
        pass

    # Install secret-redaction filter on the root handler so httpx /
    # telegram.ext / etc. don't leak bot tokens or API keys into the
    # log file (~/Library/Logs/windy-0.log under launchd).
    from windyfly.observability.redact import install_root_redaction
    install_root_redaction()

    # Install Wave 14 tracing-spine log filter so every record carries
    # the originating request_id (8-char short form) for trace lookups.
    # Format string left as-is for now to avoid changing log shape; the
    # filter runs anyway and request_id is available on every record
    # for any future formatter that wants %(request_id)s.
    from windyfly.agent.tracing import install_log_filter
    install_log_filter()

    # Wave 3: provider keys live only in the env file. Move any key the retired
    # provider files still hold into it, once, before anything reads providers.
    # Never fatal: a failed write keeps those keys working for this run and
    # retries at the next start; an unexpected error is logged and deletes nothing.
    from windyfly.agent.provider_salvage import salvage_provider_keys
    try:
        salvage_provider_keys()
    except Exception as exc:  # noqa: BLE001 - startup must not die on cleanup
        logger.warning("provider salvage skipped (%s); retrying at next start", type(exc).__name__)

    # Start background decay scheduler (G7)
    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
    _start_decay_scheduler(config, db_path, args.channel)
    from windyfly.memory.split_check import start_split_watch
    start_split_watch(db_path)
    # A restore's encrypted undo copy is wiped at 24 hours, also across a restart.
    try:
        from windyfly.backup_restore import sweep_undo as _sweep_restore_undo

        _sweep_restore_undo(db_path)
    except Exception as e:  # noqa: BLE001
        logger.debug("restore undo sweep skipped: %s", e)

    # ADR-051 Phase A.5 — single-runtime claim invariant.
    # Try to claim the agent's runtime slot on Mind before dispatching
    # to a channel. On CONFLICT, exit cleanly with a friendly message
    # so the user knows their agent is already running elsewhere. On
    # GRANTED, start the 30s heartbeat + register the atexit release.
    # On DEGRADED/SKIPPED, proceed without claim discipline (fail-open).
    from windyfly import runtime_claim

    # The claim source IS the channel, so Mind gives each channel its own
    # per-(passport, source) slot — this agent can run one runtime per channel
    # (Telegram + Windy Chat + Discord …) at the same time without them fighting
    # over a single lock. Two runtimes of the SAME channel still conflict (no
    # double replies). Heartbeat/release resolve the slot from runtime_id, so
    # they need no source. (`cli` keeps the shared base <passport> slot.)
    _claim_outcome = runtime_claim.acquire_runtime_slot(source=args.channel)
    if _claim_outcome == runtime_claim.ClaimOutcome.CONFLICT:
        # Per ADR-051 §"A.5 acceptance": the losing runtime logs the
        # conflict and goes idle. Idle == exit-clean for the CLI runtime,
        # since there's no UI to "stay idle but visible" the way a Word
        # status pill (A.6) would offer. Surface the holder to the user.
        print(
            f"Another Windy Fly runtime is already hosting this agent "
            f"({runtime_claim.conflict_holder_summary()}). Exiting.\n"
            f"If that's your background agent on this machine, run: "
            f"windy stop && windy chat",
            file=sys.stderr,
        )
        sys.exit(0)
    elif _claim_outcome == runtime_claim.ClaimOutcome.GRANTED:
        runtime_claim.start_heartbeat()
        runtime_claim.register_atexit_release()

    # Launch channel
    if args.channel == "matrix":
        import asyncio

        from windyfly.agent.boot import (
            BootContext, BootSequence,
            default_capability_registration_sequence,
        )
        from windyfly.agent.capabilities import capability_registry
        from windyfly.channels.matrix_bot import WindyFlyMatrixBot
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.tools.registry import ToolRegistry

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()
        # Every LLM call in this process lands in this ledger.
        from windyfly.memory.cost_ledger import install_cost_sink
        install_cost_sink(db, write_queue)

        tool_registry = ToolRegistry()

        # Wave 14b: canonical capability + tool registration sequence.
        # Same call from both matrix and telegram branches so they can
        # never drift the way they did pre-Wave 14 (the bug that
        # motivated this abstraction — capabilities present in matrix
        # but missing in telegram).
        BootSequence(default_capability_registration_sequence()).run(
            BootContext(
                config=config, db=db, write_queue=write_queue,
                tool_registry=tool_registry,
                capability_registry=capability_registry,
            )
        )

        # Inject the live DB into the command registry. init_all_commands
        # ran early in main() with config only (no db yet), so without this
        # every command gated on the module-level `_db` — /budget, /tokens,
        # /sliders, /facts, /history, /intents … — returns "Database not
        # available" on the matrix channel. The telegram + generic-channel
        # branches already wire this; matrix was the one that didn't
        # (surfaced 2026-07-06 by a live Windy Chat command sweep).
        from windyfly.commands.core import wire_runtime
        wire_runtime(db=db)

        from windyfly.channels.matrix_bot import MatrixCredentialsError, MatrixIdentityError

        bot = WindyFlyMatrixBot(config, db, write_queue, tool_registry)
        try:
            asyncio.run(bot.start())
        except KeyboardInterrupt:
            asyncio.run(bot.stop())
        except MatrixCredentialsError:
            # Grandma-friendly: this channel needs a hatched agent (its
            # Eternitas passport) or a pasted Matrix token. Don't dump a
            # traceback — tell the user what to do and exit non-zero so a
            # supervisor doesn't hot-loop a mis-configured channel.
            sys.stderr.write(
                "\n  💬  Windy Chat isn't set up yet.\n"
                "  This channel needs your agent's passport (run `windy go` to "
                "hatch it) or a Matrix token in .env (MATRIX_BOT_TOKEN).\n"
            )
            sys.exit(1)
        except MatrixIdentityError as e:
            # Transient (the homeserver did not answer whoami): exit non-zero
            # so the supervisor starts the channel again shortly.
            sys.stderr.write(f"\n  💬  Windy Chat did not start: {e}\n")
            sys.exit(1)
        finally:
            write_queue.stop()
            db.close()


if __name__ == "__main__":
    main()
