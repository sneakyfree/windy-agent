"""Telegram adapter using python-telegram-bot.

Env vars: TELEGRAM_BOT_TOKEN
Get token from @BotFather on Telegram.
Install: pip install windyfly[telegram]

Resilience model (parity with matrix_bot.py):
  - Exponential backoff on initial-connect failures (1s → 60s max)
  - Error handler logs every polling error so one bad update can't
    masquerade as a system failure
  - 5-minute heartbeat: polling-loop liveness (truthful health) +
    last-message age (user activity, not health)
  - Reconnect events written to the observability ledger when a
    Database + WriteQueue are wired in

Signal-driven shutdown lives at the application layer (main.py). The
channel only exposes ``stop()``; the application orchestrates the
lifecycle so that ``write_queue.stop()`` and ``db.close()`` actually
get to run before the process exits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from windyfly.observability.restart_greeting import set_pending_greeting
from windyfly.observability.sanitize import sanitize_outgoing, split_for_telegram
from windyfly.observability.sd_notify import notify_watchdog

logger = logging.getLogger(__name__)

_INITIAL_BACKOFF_S = 1
_MAX_BACKOFF_S = 60
_HEARTBEAT_INTERVAL_S = 300  # 5 minutes

# Telegram per-message hard limit is 4096; we chunk at 4000 to leave
# headroom for any auto-appended chars (link previews, bot signature).
_REPLY_CHUNK_SIZE = 4000

# Telegram's typing indicator times out after ~5s. Refresh every 4s
# while a reply is being computed so grandma sees the bot is working.
_TYPING_REFRESH_S = 4.0


# ── Nuclear reset / panic button ───────────────────────────────────
#
# Grandma scenario: bot is acting weird — stuck, wrong answers,
# broken tool, anything. She types one of these phrases and the bot
# resets itself. Long-term memory, identity, and credentials all
# survive; only the current conversation context is lost.
#
# The panic check runs at the very TOP of _handle, before any LLM
# dispatch or DB write — the chance the simple-text-match path is
# itself broken is essentially zero. If the polling loop is dead,
# the systemd watchdog (PR #88) handles that case independently.

# Whole-message exact matches (after lower/strip). No ambiguity:
# "/reset" alone triggers; "/reset my password" doesn't.
_PANIC_EXACT = frozenset({
    "/reset", "/panic", "/nuclear", "🆘",
})

# Phrase matches anywhere in the message (after lower).
_PANIC_PHRASES = (
    "reset my agent",
    "nuclear reset",
    "factory reset",
    "bring my agent back",
    "bring back my agent",
    "my agent is broken",
    "my bot is broken",
    "agent is stuck",
    "bot is stuck",
)

_PANIC_REPLY = (
    "🆘 Got it. Resetting your agent now — give me about 30 seconds.\n\n"
    "Your memory, personality, and saved facts are all safe. "
    "Only this conversation thread will reset. I'll be right back."
)


def _is_panic_message(text: str | None) -> bool:
    if not text:
        return False
    low = text.strip().lower()
    if low in _PANIC_EXACT:
        return True
    return any(p in low for p in _PANIC_PHRASES)


# ── Spend pause / resume — kill-switch UX ──────────────────────────
#
# Distinct from the panic /reset: pause/resume keeps the bot alive
# on Telegram but stops all LLM calls. Useful when the user can
# tell the bot is burning tokens and wants to STOP the spending
# without losing the bot itself.

_PAUSE_EXACT = frozenset({"/pause", "/stop-spending", "/stop"})
_RESUME_EXACT = frozenset({"/resume", "/wake-up", "/wake"})
_SPEND_EXACT = frozenset({"/spend", "/usage", "/burn"})


def _is_pause_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in _PAUSE_EXACT


def _is_resume_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in _RESUME_EXACT


def _is_spend_message(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in _SPEND_EXACT


def _format_spend_summary(summary: dict) -> str:
    """Friendly grandma-readable rendering of the spend summary."""
    lines = ["💰 *Today's spending*"]
    if summary.get("paused"):
        info = summary.get("pause_info") or {}
        when = (info.get("ts") or "").replace("T", " ")[:16]
        lines.append(f"⏸ *Paused* since {when} — *not spending anything right now.*")
        lines.append("")

    def _section(label: str, rate: dict) -> None:
        cost = rate.get("total_cost_usd", 0.0)
        calls = rate.get("total_calls", 0)
        lines.append(f"*{label}:* ${cost:.4f} ({calls} call{'s' if calls != 1 else ''})")
        for provider, slot in (rate.get("by_provider") or {}).items():
            lines.append(
                f"  • {provider}: ${slot['cost_usd']:.4f} "
                f"({slot['calls']} call{'s' if slot['calls'] != 1 else ''})"
            )

    _section("Last 5 minutes", summary.get("last_5_min", {}))
    lines.append("")
    _section("Last hour",      summary.get("last_hour", {}))
    lines.append("")
    _section("Last 24 hours",  summary.get("last_day", {}))

    hourly_now = summary.get("last_hour", {}).get("estimated_hourly_burn_usd", 0.0)
    lines.append("")
    lines.append(f"*Current burn rate:* ~${hourly_now:.3f}/hour")
    if not summary.get("paused"):
        lines.append("")
        lines.append("_Say /pause to stop spending. Say /resume when you want me back._")
    return "\n".join(lines)


class TelegramChannel(ChannelAdapter):
    """Windy Fly on Telegram."""

    name = "telegram"

    def __init__(
        self,
        allowed_user_ids: list[str] | None = None,
        dm_policy: str = "open",
        db: Any = None,
        write_queue: Any = None,
    ) -> None:
        # python-telegram-bot is an optional extra — mypy can't resolve
        # ApplicationBuilder on a baseline install, so type as Any.
        self._app: Any = None
        self._connected = False
        # When allowed_user_ids is non-empty, silently drop messages from any
        # other sender. Mirrors the fleet allowFrom convention (see
        # ACCESS_LOCKBOX §5 — Kit/Herm/Windy bots all gate by Grant's
        # Telegram ID to avoid the 2026-02-10 BlueBubbles incident).
        self._allowed_user_ids: set[str] = set(allowed_user_ids or [])
        self._dm_policy = dm_policy

        # Observability hooks — optional so callers without a DB still work.
        self._db = db
        self._write_queue = write_queue

        # Resilience state
        self._backoff = _INITIAL_BACKOFF_S
        self._shutting_down = False
        # Time of last successful user-message round-trip. Reflects USER
        # ACTIVITY, not bot health — a long age here just means no one
        # has texted, which is fine.
        self._last_message_at: float = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set — get one from @BotFather")

        from telegram.ext import ApplicationBuilder, MessageHandler, filters

        # Outer connect loop — survives transient network failures during
        # initialize/start_polling. PTB's polling is itself resilient once
        # running, so this loop is mostly about getting off the ground when
        # the network is flaky at startup.
        last_error: Exception | None = None
        while not self._shutting_down:
            try:
                self._app = ApplicationBuilder().token(token).build()
                self._app.add_handler(MessageHandler(filters.TEXT, self._handle))
                self._app.add_error_handler(self._on_polling_error)

                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling()
                self._connected = True
                self._last_message_at = time.time()
                self._backoff = _INITIAL_BACKOFF_S
                logger.info("Telegram bot started")
                break
            except Exception as exc:
                last_error = exc
                self._connected = False
                logger.warning(
                    "Telegram start failed: %s. Reconnecting in %ds...",
                    exc, self._backoff,
                )
                self._log_reconnect_event(str(exc), self._backoff)
                await self._safe_app_shutdown()
                if self._shutting_down:
                    break
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF_S)
        else:
            # Loop only exits via break or shutdown — if shutdown won, surface
            # the last error so the caller can act on it.
            if last_error is not None:
                raise last_error

        # Heartbeat task runs until stop() is called.
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _on_polling_error(self, update: Any, context: Any) -> None:
        """Catch errors raised during update processing.

        Without a registered error handler, PTB writes a traceback to stderr
        and otherwise carries on; with one we can both silence the noise and
        log it through our observability path.
        """
        err = getattr(context, "error", None)
        logger.warning("Telegram polling error: %s", err)
        self._log_reconnect_event(str(err), 0)

    def _polling_alive(self) -> bool:
        """True iff PTB's polling loop is currently running.

        The pre-fix heartbeat conflated "connected at startup" with
        "still polling now," so a silently-dead polling loop looked
        healthy. This delegates to PTB's own state.
        """
        try:
            return bool(
                self._app
                and getattr(self._app, "updater", None)
                and self._app.updater.running
            )
        except Exception:
            return False

    async def _heartbeat_loop(self) -> None:
        """Log a heartbeat every 5 minutes with TRUE polling health."""
        while not self._shutting_down:
            try:
                alive = self._polling_alive()
                age = (
                    time.time() - self._last_message_at
                    if self._last_message_at
                    else -1.0
                )
                if alive:
                    logger.info(
                        "♥ Telegram heartbeat: polling=alive, last_message_age=%.0fs",
                        age,
                    )
                    # Pet the systemd watchdog. If polling is dead we
                    # deliberately DON'T pet it so systemd's
                    # WatchdogSec= timer fires and restarts us. No-op
                    # when NOTIFY_SOCKET is unset (dev / tests).
                    notify_watchdog()
                else:
                    logger.warning(
                        "✗ Telegram heartbeat: polling=DEAD, last_message_age=%.0fs"
                        " — PTB updater not running; restart needed",
                        age,
                    )
                    self._connected = False
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

    def _log_reconnect_event(self, error: str, backoff_seconds: int) -> None:
        """Best-effort write to the observability ledger."""
        if self._db is None or self._write_queue is None:
            return
        try:
            from windyfly.observability.events import log_event
            log_event(self._db, self._write_queue, "telegram.reconnect", {
                "error": error[:200],
                "backoff_seconds": backoff_seconds,
            })
        except Exception as e:
            logger.debug("Reconnect event logging failed: %s", e)

    async def _safe_app_shutdown(self) -> None:
        """Tear down a partially-built app without raising."""
        if self._app is None:
            return
        for step in (
            lambda: self._app.updater.stop() if self._app.updater else None,
            lambda: self._app.stop(),
            lambda: self._app.shutdown(),
        ):
            try:
                result = step()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug("App shutdown step failed: %s", e)
        self._app = None

    async def _handle(self, update, context) -> None:
        if not update.message or not update.message.text:
            return

        sender_id = str(update.message.from_user.id)
        if self._allowed_user_ids and sender_id not in self._allowed_user_ids:
            logger.warning("Dropping Telegram message from unauthorized sender %s", sender_id)
            return

        text = update.message.text

        # ── PAUSE / RESUME / SPEND — process BEFORE panic check
        # so the kill-switch is even faster than nuclear reset. No
        # LLM, no DB, no tools — just file ops + cost ledger reads.
        from windyfly.agent.spend_monitor import (
            is_paused, pause as _pause_spending,
            resume as _resume_spending, get_spend_summary,
        )
        if _is_pause_message(text):
            result = _pause_spending(reason="user requested via /pause", actor=sender_id)
            ack = (
                "⏸ *Paused.* I won't make any LLM calls until you "
                "say /resume. I'll still respond to /resume, /reset, "
                "/spend — I just won't spend money on thinking."
            ) if result.get("ok") else (
                "⚠ Couldn't write the pause flag — please use /reset instead."
            )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("pause-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        if _is_resume_message(text):
            result = _resume_spending()
            if result.get("ok"):
                ack = (
                    "▶️ *Awake.* I'm thinking again. What can I help with?"
                    if result.get("was_paused")
                    else "I wasn't paused — just say what you need."
                )
            else:
                ack = "⚠ Couldn't clear the pause flag — try /reset."
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("resume-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        if _is_spend_message(text):
            try:
                summary = get_spend_summary(self._db) if self._db else {}
                ack = _format_spend_summary(summary)
            except Exception as e:
                logger.warning("spend summary failed: %s", e)
                ack = "⚠ Couldn't read the cost ledger right now."
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("spend-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # ── NUCLEAR RESET — must be FIRST, before agent loop ──
        # If the bot is stuck/confused, this short-circuits BEFORE
        # any LLM / DB / tool dispatch. Long-term memory is safe.
        if _is_panic_message(text):
            logger.warning("PANIC: nuclear reset requested by %s", sender_id)
            # Record the chat_id so the post-restart process knows
            # who to greet when it comes back online.
            set_pending_greeting(
                chat_id=str(update.message.chat_id),
                platform="telegram",
                reason="panic_reset",
            )
            try:
                await self._send_long_reply(update.message, _PANIC_REPLY)
            except Exception as e:
                logger.warning("panic-ack reply failed: %s", e)
            self._last_message_at = time.time()
            asyncio.create_task(self._trigger_self_restart())
            return

        # Unified command detection
        from windyfly.channels.base import handle_incoming
        was_command, cmd_response = await handle_incoming(text, {"platform": "telegram"})
        if was_command:
            await self._send_long_reply(update.message, cmd_response)
            self._last_message_at = time.time()
            return

        msg = IncomingMessage(
            platform="telegram",
            channel_id=str(update.message.chat_id),
            sender_id=str(update.message.from_user.id),
            sender_name=update.message.from_user.first_name or "User",
            text=text,
        )
        # on_message wired by the channel manager before start().
        assert self.on_message is not None
        # Run the agent with a "typing…" indicator so grandma never
        # sees an unresponsive gap. Cancellation in finally guarantees
        # the typing task ends even if on_message raises.
        typing_task = asyncio.create_task(
            self._keep_typing(update.message.chat_id),
        )
        try:
            response = await self.on_message(msg)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass
        await self._send_long_reply(update.message, response)
        self._last_message_at = time.time()

    async def send(self, message: OutgoingMessage) -> None:
        if self._app:
            await self._app.bot.send_message(
                chat_id=message.channel_id,
                text=sanitize_outgoing(message.text),
            )

    async def _keep_typing(self, chat_id: Any) -> None:
        """Keep Telegram's typing indicator showing while the agent
        is thinking. Telegram auto-times-out after ~5s; refresh
        every 4s. Cancelled by the caller in a finally block."""
        if not self._app:
            return
        try:
            while True:
                try:
                    await self._app.bot.send_chat_action(
                        chat_id=chat_id, action="typing",
                    )
                except Exception as e:
                    # Don't let a transient send_chat_action failure
                    # kill the loop — typing indicator is cosmetic.
                    logger.debug("send_chat_action failed: %s", e)
                await asyncio.sleep(_TYPING_REFRESH_S)
        except asyncio.CancelledError:
            return

    async def _send_long_reply(self, message: Any, text: str | None) -> None:
        """Sanitize + chunk + send a reply.

        Single-message replies fall through unchanged. Long replies
        get split at paragraph/sentence boundaries into multiple
        Telegram messages so nothing is lost to the 4096-char limit.

        Each chunk is sanitized again as a safety net (idempotent).
        Send failures on individual chunks don't abort the rest —
        grandma gets as much of the reply as we can deliver.
        """
        # Sanitize without truncation (we'll chunk instead).
        clean = sanitize_outgoing(text, max_length=10**9)
        chunks = split_for_telegram(clean, max_chunk=_REPLY_CHUNK_SIZE)
        if not chunks:
            chunks = [sanitize_outgoing(None)]  # fallback message
        for i, chunk in enumerate(chunks):
            try:
                await message.reply_text(sanitize_outgoing(chunk))
            except Exception as e:
                logger.warning(
                    "reply chunk %d/%d failed: %s",
                    i + 1, len(chunks), e,
                )

    async def _trigger_self_restart(self) -> None:
        """Schedule a graceful self-restart after the panic-ack reply
        has flushed. Reuses main.py's existing SIGTERM handler so
        write_queue.stop() / db.close() get to run before exit, then
        ``Restart=always`` revives us with fresh in-memory state.

        We sleep briefly so reply_text has time to round-trip to
        Telegram before we die. 2 seconds is enough on any reachable
        connection; the TimeoutStopSec further bounds total downtime.
        """
        import os
        import signal
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        logger.warning("PANIC: sending SIGTERM for nuclear reset")
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            logger.error("panic SIGTERM failed (%s) — hard-exiting", e)
            # If even SIGTERM fails (process is wedged at a syscall
            # level), os._exit bypasses cleanup. Restart=always still
            # revives us. Worst case we lose write-queue flush.
            os._exit(75)

    async def stop(self) -> None:
        self._shutting_down = True
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
        await self._safe_app_shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._polling_alive()
