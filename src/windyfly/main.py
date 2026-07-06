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
    import asyncio

    from windyfly.memory.database import Database
    from windyfly.memory.decay import run_decay
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.personality.versioning import run_periodic_drift_check

    def _decay_loop() -> None:
        # Use a dedicated DB connection for the decay thread
        decay_db = Database(db_path)
        decay_wq = WriteQueue()
        decay_wq.start()
        logger.info("Decay + drift + backup scheduler started (interval: 24h)")
        while True:
            try:
                counts = run_decay(decay_db, decay_wq, config)
                logger.info("Decay cycle complete: %s", counts)
            except Exception as e:
                logger.error("Decay cycle failed: %s", e)
            try:
                drift = run_periodic_drift_check(decay_db, decay_wq)
                if drift.get("drift_detected"):
                    logger.warning("Personality drift detected: %s", drift["drift_report"])
                else:
                    logger.info("Drift check complete: no drift detected")
            except Exception as e:
                logger.error("Drift check failed: %s", e)
            # Skill-library curation (Sprint 3): demote failing skills,
            # cap the promoted playbook library (LRU). Demote-only.
            try:
                from windyfly.skills.curator import run_curation
                cstats = run_curation(decay_db)
                logger.info("Skill curation complete: %s", cstats)
            except Exception as e:
                logger.error("Skill curation failed: %s", e)
            # Cloud backup check
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
            time.sleep(_DECAY_INTERVAL_SECONDS)

    t = threading.Thread(target=_decay_loop, daemon=True, name="decay-scheduler")
    t.start()
    return t


def _run_bot_channel(
    config: dict,
    db_path: str,
    platform: str,
    adapter_factory,
    preflight=None,
) -> None:
    """Generic runner for manager-based, BYO-token chat channels.

    Discord, Slack, and any future "paste-a-token-and-go" channel share this:
    it boots the agent stack, registers the channel adapter with the
    ChannelManager, wires runtime state, sends systemd READY once the channel
    is up, and runs until SIGTERM/SIGINT. Each channel runs as its own process
    (``--channel <name>``) and claims its own per-(passport, source) runtime
    slot, so an agent can be live on several channels at once.

    The telegram branch keeps its own bespoke runner (owner-gating, guest
    mode, post-panic greeting); everything else shares this path.

    - ``adapter_factory(db, write_queue)`` returns a ready ``ChannelAdapter``.
    - ``preflight()`` is an optional env-var check that may ``sys.exit(1)``.
    """
    import asyncio
    import signal as _signal_mod

    from windyfly.agent.boot import (
        BootContext, BootSequence,
        default_capability_registration_sequence,
    )
    from windyfly.agent.capabilities import capability_registry
    from windyfly.agent.loop import agent_respond
    from windyfly.channels.manager import ChannelManager, ChannelStartupError
    from windyfly.commands.core import wire_runtime
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.observability.sd_notify import notify_ready, notify_stopping
    from windyfly.tools.registry import ToolRegistry

    if preflight is not None:
        preflight()

    db = Database(db_path)
    write_queue = WriteQueue()
    write_queue.start()
    tool_registry = ToolRegistry()

    # Same canonical capability + tool registration the telegram/matrix
    # branches run, so every channel exposes identical tools by construction.
    BootSequence(default_capability_registration_sequence()).run(
        BootContext(
            config=config, db=db, write_queue=write_queue,
            tool_registry=tool_registry,
            capability_registry=capability_registry,
        )
    )

    async def _respond(
        text: str, session_id: str, band=None,
    ) -> str:
        # Respect the global guest-mode flag on every channel (demo audiences
        # get GRANDMA MODE), same as telegram.
        from windyfly.agent.capabilities import Band
        from windyfly.agent.executor import run_turn
        if band is None:
            # Fallback for non-manager callers; the manager resolves
            # sender→band via channels.identity (guest capping included).
            from windyfly.agent.guest_mode import is_guest_active
            band = Band.USER if is_guest_active() else Band.OWNER
        # Off-loop: a long turn must not starve heartbeats or the other
        # channels this process serves (see agent/executor.py).
        return await run_turn(
            agent_respond,
            config, db, write_queue, text, session_id, tool_registry,
            band=band,
        )

    manager = ChannelManager(_respond)
    manager.register(adapter_factory(db, write_queue))
    wire_runtime(db=db, channel_manager=manager)

    async def _run() -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _on_signal(signum: int) -> None:
            logger.info("Received signal %s — initiating shutdown", signum)
            stop_event.set()

        for sig in (_signal_mod.SIGTERM, _signal_mod.SIGINT):
            try:
                loop.add_signal_handler(sig, _on_signal, sig)
            except (NotImplementedError, RuntimeError):
                pass

        # Start channels BEFORE notify_ready(): a failed start must exit
        # non-zero so the supervisor restarts clean, not send READY from a
        # zombie (the 2026-05-17 outage pattern).
        try:
            await manager.start_all()
        except ChannelStartupError as exc:
            logger.critical(
                "FATAL: %s. Refusing to send READY=1. Exiting non-zero so "
                "the supervisor restarts from a clean slate.", exc,
            )
            raise SystemExit(1) from exc

        notify_ready()
        logger.info("Channel '%s' ready", platform)
        try:
            await stop_event.wait()
        finally:
            notify_stopping()
            logger.info("Stopping channels...")
            await manager.stop_all()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Flushing write queue and closing database...")
        write_queue.stop()
        db.close()
        logger.info("Windy Fly shut down cleanly")


def main() -> None:
    """Main entry point for Windy Fly."""
    load_dotenv()

    parser = argparse.ArgumentParser(description="Windy Fly — AI agent brain")
    sub = parser.add_subparsers(dest="subcommand")

    # Legacy --channel mode
    parser.add_argument(
        "--channel",
        choices=["cli", "matrix", "sms", "telegram", "discord", "slack"],
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
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

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

    # Start background decay scheduler (G7)
    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
    _start_decay_scheduler(config, db_path)

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
            f"({runtime_claim.conflict_holder_summary()}). Exiting.",
            file=sys.stderr,
        )
        sys.exit(0)
    elif _claim_outcome == runtime_claim.ClaimOutcome.GRANTED:
        runtime_claim.start_heartbeat()
        runtime_claim.register_atexit_release()

    # Launch channel
    if args.channel == "cli":
        from windyfly.channels.cli import run_cli
        run_cli(config)
    elif args.channel == "matrix":
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

        from windyfly.channels.matrix_bot import MatrixCredentialsError

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
        finally:
            write_queue.stop()
            db.close()
    elif args.channel == "telegram":
        import asyncio
        import signal as _signal_mod

        from windyfly.agent.boot import (
            BootContext, BootSequence,
            default_capability_registration_sequence,
        )
        from windyfly.agent.capabilities import capability_registry
        from windyfly.agent.loop import agent_respond
        from windyfly.channels.manager import ChannelManager
        from windyfly.channels.telegram_bot import TelegramChannel
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.tools.registry import ToolRegistry

        if not os.environ.get("TELEGRAM_BOT_TOKEN"):
            print(
                "Error: TELEGRAM_BOT_TOKEN not set. Get one from @BotFather.",
                file=sys.stderr,
            )
            sys.exit(1)

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()
        tool_registry = ToolRegistry()

        # Wave 14b: canonical capability + tool registration sequence.
        # Same call from both matrix and telegram branches — additions
        # made here propagate to both channels by construction.
        BootSequence(default_capability_registration_sequence()).run(
            BootContext(
                config=config, db=db, write_queue=write_queue,
                tool_registry=tool_registry,
                capability_registry=capability_registry,
            )
        )

        # allowFrom defaults to Grant's Telegram ID per ACCESS_LOCKBOX §5
        # (fleet convention; never set to '*' on personal bots).
        owner_id = os.environ.get("AGENT_OWNER_TELEGRAM_ID", "8545546994")
        dm_policy = config.get("telegram", {}).get("dm_policy", "pairing")

        async def _respond(
            text: str, session_id: str, band=None,
        ) -> str:
            # Guest-mode toggle: when Grant has flipped /guest on (file
            # flag at ~/.windy/.guest), demote every message to
            # Band.USER so prompt assembly engages GRANDMA MODE for
            # demo audiences. Default OWNER otherwise.
            from windyfly.agent.capabilities import Band
            from windyfly.agent.executor import run_turn
            if band is None:
                from windyfly.agent.guest_mode import is_guest_active
                band = Band.USER if is_guest_active() else Band.OWNER
            # Off-loop: keeps the watchdog heartbeat + /panic path
            # responsive during long turns (see agent/executor.py).
            return await run_turn(
                agent_respond,
                config, db, write_queue, text, session_id, tool_registry,
                band=band,
            )

        manager = ChannelManager(_respond)
        manager.register(TelegramChannel(
            allowed_user_ids=[owner_id],
            dm_policy=dm_policy,
            db=db,
            write_queue=write_queue,
        ))

        # Inject runtime state into the command registry so /pulse can
        # see the live DB + channel manager state.
        from windyfly.commands.core import wire_runtime
        wire_runtime(db=db, channel_manager=manager)

        async def _run() -> None:
            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _on_signal(signum: int) -> None:
                sig_name = (
                    _signal_mod.Signals(signum).name
                    if hasattr(_signal_mod, "Signals")
                    else str(signum)
                )
                logger.info("Received %s — initiating shutdown", sig_name)
                stop_event.set()

            for sig in (_signal_mod.SIGTERM, _signal_mod.SIGINT):
                try:
                    loop.add_signal_handler(sig, _on_signal, sig)
                except (NotImplementedError, RuntimeError):
                    # Windows or non-main-thread — KeyboardInterrupt below
                    # still catches SIGINT for ctrl-c interactive runs.
                    pass

            # Start channels BEFORE notify_ready(). If any registered
            # channel can't start, `start_all()` raises
            # ChannelStartupError — surface it as a fatal exit so
            # systemd's Restart=always retries from a clean process
            # rather than getting a READY=1 from a zombie. Prevents
            # the 2026-05-17 outage pattern: channel ImportError →
            # swallowed → notify_ready() → no heartbeat → watchdog
            # SIGABRT every 10 min, forever.
            from windyfly.channels.manager import ChannelStartupError
            from windyfly.observability.sd_notify import (
                notify_ready, notify_stopping,
            )
            try:
                await manager.start_all()
            except ChannelStartupError as exc:
                logger.critical(
                    "FATAL: %s. Refusing to send READY=1. Exiting "
                    "non-zero so the supervisor restarts us from a "
                    "clean slate.", exc,
                )
                # Use sys.exit so the asyncio loop unwinds cleanly,
                # giving systemd a normal exit status (1) instead of
                # a SIGABRT'd watchdog-kill 10 min from now.
                raise SystemExit(1) from exc
            # Tell systemd we're ready (Type=notify). No-op outside
            # systemd. Watchdog pings come from the heartbeat loop in
            # each channel adapter.
            notify_ready()

            # If we're starting up after a panic-reset, the previous
            # process left a flag with the chat_id to greet. Send a
            # one-line "I'm back!" so grandma knows the reset worked.
            from windyfly.channels.base import OutgoingMessage
            from windyfly.observability.restart_greeting import (
                consume_pending_greeting, GREETING_TEXT,
            )
            pending = consume_pending_greeting()
            if pending and pending.get("platform") == "telegram":
                chat_id = pending.get("chat_id")
                if chat_id:
                    try:
                        await manager.send("telegram", OutgoingMessage(
                            text=GREETING_TEXT,
                            channel_id=chat_id,
                        ))
                        logger.info(
                            "post-panic greeting sent to chat %s", chat_id,
                        )
                    except Exception as e:
                        logger.warning(
                            "post-panic greeting send failed: %s", e,
                        )
            try:
                await stop_event.wait()
            finally:
                notify_stopping()
                logger.info("Stopping channels...")
                await manager.stop_all()

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Flushing write queue and closing database...")
            write_queue.stop()
            db.close()
            logger.info("Windy Fly shut down cleanly")
    elif args.channel == "discord":
        from windyfly.channels.discord_bot import DiscordChannel

        def _discord_preflight() -> None:
            if not os.environ.get("DISCORD_BOT_TOKEN"):
                logger.error(
                    "DISCORD_BOT_TOKEN not set. Create a bot at "
                    "discord.com/developers → Bot → Reset Token, enable the "
                    "Message Content intent, and invite it to your server."
                )
                sys.exit(1)

        _run_bot_channel(
            config, db_path, "discord",
            lambda db, wq: DiscordChannel(),
            preflight=_discord_preflight,
        )
    elif args.channel == "slack":
        from windyfly.channels.slack_bot import SlackChannel

        def _slack_preflight() -> None:
            if not (
                os.environ.get("SLACK_BOT_TOKEN")
                and os.environ.get("SLACK_APP_TOKEN")
            ):
                logger.error(
                    "SLACK_BOT_TOKEN and SLACK_APP_TOKEN not set. "
                    "Create a Slack app at api.slack.com/apps with Socket "
                    "Mode on (App-Level Token → SLACK_APP_TOKEN, Bot User "
                    "OAuth Token → SLACK_BOT_TOKEN)."
                )
                sys.exit(1)

        _run_bot_channel(
            config, db_path, "slack",
            lambda db, wq: SlackChannel(),
            preflight=_slack_preflight,
        )
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
