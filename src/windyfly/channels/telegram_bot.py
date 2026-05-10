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
import io
import logging
import os
import time
from typing import Any

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
# Slash-command parsers extracted to a channel-agnostic module
# (PR #130) so future Matrix / iMessage / WhatsApp adapters can
# reuse them without copy-paste. Telegram-specific reply text +
# side effects stay here; pure recognition lives in slash_commands.
from windyfly.channels.slash_commands import (
    is_lifeboat_status_message as _is_lifeboat_status_message,
    is_normal_message as _is_normal_message,
    is_panic_message as _is_panic_message,
    is_pause_message as _is_pause_message,
    is_resume_message as _is_resume_message,
    is_resurrect_message as _is_resurrect_message,
    is_spend_message as _is_spend_message,
    is_uptime_message as _is_uptime_message,
    is_version_message as _is_version_message,
    is_whoami_message as _is_whoami_message,
)
from windyfly.observability.recovery_hint import with_recovery_hint
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

_PANIC_REPLY = (
    "🆘 Got it. Resetting your agent now — give me about 30 seconds.\n\n"
    "Your memory, personality, and saved facts are all safe. "
    "Only this conversation thread will reset. I'll be right back."
)


def _parse_guest_command(text: str | None) -> tuple[bool, str | None]:
    """Parse /guest [on|off|status] (or bare /guest = status).

    Returns (is_guest_cmd, arg) where arg is one of:
      - None       → bare /guest → show status
      - "on"       → /guest on / /guest start / /demo
      - "off"      → /guest off / /guest end / /demo off
      - "invalid"  → unrecognized arg
    """
    if not text:
        return False, None
    t = text.strip().lower()
    # Convenience aliases — /demo on/off mirrors /guest on/off so
    # Grant can use whichever feels natural on stage.
    if t in ("/guest", "/demo"):
        return True, None
    if t in ("/guest on", "/guest start", "/demo on", "/demo start"):
        return True, "on"
    if t in ("/guest off", "/guest end", "/guest stop", "/demo off", "/demo end", "/demo stop"):
        return True, "off"
    if t.startswith("/guest") or t.startswith("/demo"):
        return True, "invalid"
    return False, None


# NOTE: is_pause_message / is_resume_message / is_spend_message
# moved to windyfly.channels.slash_commands (PR #130). Imported at
# the top of this file under the legacy underscore-prefix names.


def _parse_yolo_command(text: str | None) -> tuple[bool, str | int | None]:
    """Returns (is_yolo, arg) where arg is one of:
      - None         → bare /yolo (status or default-enable)
      - "off"        → /yolo off / /yolo disable / /yolo end
      - int hours    → /yolo 24 / /yolo 48 / /yolo 6
      - "invalid"    → unrecognized arg
    Not a yolo command → (False, None).
    """
    if not text:
        return False, None
    t = text.strip().lower()
    if t in ("/yolo", "/yolo24", "/yolo48"):
        # /yolo24 and /yolo48 are convenience shortcuts so the
        # Telegram menu can list them as separate tappable entries.
        if t == "/yolo24":
            return True, 24
        if t == "/yolo48":
            return True, 48
        return True, None
    if not t.startswith("/yolo"):
        return False, None
    rest = t[5:].strip()
    if rest in ("off", "disable", "end", "stop"):
        return True, "off"
    # /yolo 24, /yolo 48, /yolo 6h, /yolo 6
    rest_clean = rest.rstrip("h")
    try:
        hours = int(rest_clean)
        if hours <= 0:
            return True, "invalid"
        return True, hours
    except ValueError:
        return True, "invalid"


def _format_spend_summary(summary: dict) -> str:
    """Friendly grandma-readable rendering of the spend summary."""
    lines = ["💰 *Today's spending*"]
    if summary.get("paused"):
        info = summary.get("pause_info") or {}
        when = (info.get("ts") or "").replace("T", " ")[:16]
        lines.append(f"⏸ *Paused* since {when} — *not spending anything right now.*")
        lines.append("")

    yolo = summary.get("yolo") or {}
    if yolo.get("active"):
        hrs = yolo.get("hours_remaining", 0)
        expires = (yolo.get("expires_at") or "").replace("T", " ")[:16]
        lines.append(
            f"🚀 *YOLO mode active* — auto-pause off for "
            f"{hrs:.1f} more hour{'s' if hrs != 1 else ''} "
            f"(until {expires} UTC). Say /yolo off to end early."
        )
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


def _wav_to_ogg_opus(wav_bytes: bytes, timeout_s: int = 20) -> bytes | None:
    """Convert WAV bytes → OGG/Opus bytes via ffmpeg subprocess.

    Telegram's ``send_voice`` requires OGG/Opus; ``send_audio``
    accepts WAV but renders as a file attachment instead of the
    voice-bubble UX that makes voice-out worth doing for grandma.

    Returns None when ffmpeg is missing or conversion fails. Caller
    falls back to send_audio with the raw WAV in that case.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error",
                "-i", "pipe:0",
                "-c:a", "libopus", "-b:a", "32k",
                "-ar", "48000", "-ac", "1",
                "-f", "ogg", "pipe:1",
            ],
            input=wav_bytes,
            capture_output=True,
            timeout=timeout_s,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        logger.debug("ffmpeg WAV→OGG failed exit=%s stderr=%s",
                     proc.returncode, proc.stderr[:200].decode("utf-8", "replace"))
        return None
    except FileNotFoundError:
        logger.debug("ffmpeg not on PATH; voice-out falls back to send_audio")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg WAV→OGG timed out after %ss", timeout_s)
        return None
    except Exception as e:
        logger.debug("ffmpeg WAV→OGG unexpected: %s", e)
        return None


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
                # Voice + audio: filters.VOICE is for tap-and-hold voice
                # notes (Telegram's primary speech UX); filters.AUDIO
                # catches forwarded audio files and music shares. Both
                # route to _handle_voice which transcribes (when
                # faster-whisper is installed) and dispatches the text
                # to the agent — same pipeline as a typed message.
                # Without faster-whisper, the user gets a polite "I
                # can't hear yet, please type" reply instead of the
                # silent drop the bot used to do.
                self._app.add_handler(MessageHandler(
                    filters.VOICE | filters.AUDIO, self._handle_voice,
                ))
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
        """Log a heartbeat every 5 minutes with TRUE polling health.

        Three jobs per tick:
          1. Log polling state (alive / dead) for operator visibility
          2. Pet the systemd watchdog when alive (so a polling-dead
             zombie gets killed and restarted automatically)
          3. Auto-pause check — if the bot's burn rate exceeds the
             configured threshold, pause itself and DM the owner.
             Solves the "8 agents and one is in a zombie loop"
             scenario: zombie loops self-quarantine within ~5 min.
        """
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
                    notify_watchdog()
                    await self._maybe_auto_pause()
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

    async def _maybe_auto_pause(self) -> None:
        """Check the burn rate; if breached, pause + DM the owner.

        The DB query runs in the default executor so we don't block
        the asyncio loop on a sqlite read. Failure is non-fatal —
        the heartbeat keeps ticking even if the check fails.
        """
        if self._db is None:
            return
        from windyfly.agent.spend_monitor import maybe_auto_pause
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, maybe_auto_pause, self._db,
            )
        except Exception as e:
            logger.warning("auto-pause check failed: %s", e)
            return
        if result.get("action") != "paused":
            return
        # Breached — DM the owner so they know.
        if not self._app or not self._allowed_user_ids:
            return
        hourly = result.get("current_hourly", 0)
        threshold = result.get("threshold", 0)
        msg = (
            f"⚠️ *Auto-paused — burn rate too high*\n\n"
            f"My burn rate hit *${hourly:.2f}/hour* "
            f"(threshold ${threshold:.2f}/hr).\n\n"
            f"I've stopped making LLM calls to protect your wallet. "
            f"Say */resume* when you want me thinking again, or "
            f"*/spend* to see the breakdown."
        )
        for owner_id in self._allowed_user_ids:
            try:
                await self._app.bot.send_message(
                    chat_id=owner_id, text=msg, parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("auto-pause DM to %s failed: %s", owner_id, e)
        logger.warning(
            "AUTO-PAUSED: burn=$%.2f/hr exceeds $%.2f/hr — owner DM'd",
            hourly, threshold,
        )

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

    async def _handle_voice(self, update, context) -> None:
        """Voice / audio message ingestion.

        Pre-PR #129 this didn't exist — voice notes silently dropped.
        Closes the accessibility gap for grandma demos: voice in,
        text reply out (with explicit "here's what I heard" so the
        user knows the bot caught it correctly).

        Three paths:
          1. Voice transcription unavailable (faster-whisper not
             installed) → polite text reply explaining how to type
             instead. NEVER silently drop.
          2. Transcription works but produces empty / unintelligible
             output → polite "I tried but couldn't make out the words"
             reply.
          3. Transcription works → dispatch the transcript through
             the same agent_respond as a typed message, send the
             reply as text (voice synthesis deferred to follow-up).

        Same auth gate as text: messages from non-allowlisted senders
        are dropped (they're dropped silently in text path too — same
        rule applies here)."""
        if not update.message:
            return

        # Auth gate — same as text path.
        sender_id = str(update.message.from_user.id)
        if self._allowed_user_ids and sender_id not in self._allowed_user_ids:
            logger.warning(
                "Dropping Telegram voice from unauthorized sender %s",
                sender_id,
            )
            return

        # Telegram surfaces voice as `update.message.voice`, audio file
        # uploads as `update.message.audio`. Either is fine for our
        # transcription pipeline.
        media = update.message.voice or update.message.audio
        if media is None:
            return

        # Path 1: transcription stack absent → guide the user to type.
        try:
            from windyfly.voice import is_available as _voice_avail
        except Exception:
            _voice_avail = lambda: False  # noqa: E731 - graceful degrade
        if not _voice_avail():
            try:
                await self._send_long_reply(
                    update.message,
                    "👋 I got your voice message but I can't hear it "
                    "yet — voice support isn't installed on this bot. "
                    "Please type your message and I'll respond right "
                    "away.\n\n"
                    "_(Operator: install voice support with "
                    "`pip install windyfly[voice]` and set "
                    "`WINDY_VOICE_ENABLED=1`.)_",
                )
            except Exception as e:
                logger.warning("voice-unavailable reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # Path 2 + 3: transcribe, dispatch, reply. Saving the file
        # under /tmp so we don't accumulate audio on disk; we delete
        # it after transcription regardless of outcome.
        import tempfile
        import os as _os
        tmp_path = None
        try:
            file = await context.bot.get_file(media.file_id)
            with tempfile.NamedTemporaryFile(
                prefix="windy-voice-", suffix=".ogg", delete=False,
            ) as tmpf:
                tmp_path = tmpf.name
            await file.download_to_drive(tmp_path)
        except Exception as e:
            logger.warning("voice download failed: %s", e)
            try:
                await self._send_long_reply(
                    update.message,
                    "I tried to listen but couldn't download your "
                    "voice message. Could you try again or type the "
                    "message?",
                )
            except Exception:
                pass
            if tmp_path and _os.path.exists(tmp_path):
                try:
                    _os.unlink(tmp_path)
                except Exception:
                    pass
            self._last_message_at = time.time()
            return

        # Run transcription on the executor so the async loop can keep
        # serving other messages while Whisper crunches.
        loop = asyncio.get_running_loop()
        transcript: str | None = None
        try:
            from windyfly.voice import transcribe as _transcribe
            transcript = await loop.run_in_executor(
                None, _transcribe, tmp_path,
            )
        except Exception as e:
            logger.warning("voice transcribe failed: %s", e)
        finally:
            if tmp_path and _os.path.exists(tmp_path):
                try:
                    _os.unlink(tmp_path)
                except Exception:
                    pass

        if not transcript:
            try:
                await self._send_long_reply(
                    update.message,
                    "I tried to listen but couldn't make out the words. "
                    "Could you try again, maybe a bit louder or in a "
                    "quieter spot? Or feel free to type — I'll respond "
                    "either way.",
                )
            except Exception as e:
                logger.warning("voice-empty reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # Dispatch transcript through the same path as text message.
        # We prefix the reply with what we heard so the user knows we
        # caught it correctly — accessibility insurance.
        msg = IncomingMessage(
            platform="telegram",
            channel_id=str(update.message.chat_id),
            sender_id=sender_id,
            sender_name=update.message.from_user.first_name or "User",
            text=transcript,
        )
        assert self.on_message is not None
        typing_task = asyncio.create_task(
            self._keep_typing(update.message.chat_id),
        )
        try:
            outgoing = await self.on_message(msg)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass

        # on_message returns a str (per ChannelAdapter protocol), not
        # an OutgoingMessage — same pattern the text-path _handle uses.
        body = (
            f"🎙 _Heard:_ \"{transcript[:200]}\"\n\n"
            f"{outgoing if outgoing else ''}"
        )
        try:
            await self._send_long_reply(update.message, body)
        except Exception as e:
            logger.warning("voice-reply send failed: %s", e)

        # Voice-OUT (PR #143): if Piper TTS is installed, also send
        # a spoken version of the reply. Symmetric with voice-IN —
        # grandma who sent voice gets voice back. The text reply
        # above is the "Heard:" confirmation + answer; this is just
        # the answer spoken aloud. Failure here is silent (we
        # already sent the text).
        try:
            from windyfly.voice import (
                is_synthesize_available as _tts_avail,
                synthesize as _tts_synth,
            )
        except Exception:
            _tts_avail = lambda: False  # noqa: E731
            _tts_synth = None  # type: ignore[assignment]
        if (
            _tts_avail()
            and outgoing
            and os.environ.get("WINDY_VOICE_OUT", "1") != "0"
        ):
            await self._send_synth_voice_reply(update.message, outgoing)

        self._last_message_at = time.time()

    async def _send_synth_voice_reply(self, message: Any, text: str) -> None:
        """Synthesize ``text`` to a voice note and send it.

        Best-effort: any failure is logged and swallowed because the
        text reply already shipped above. The voice version is bonus.

        Pipeline:
          1. Piper synthesize → WAV bytes (in a thread executor so
             the event loop keeps serving)
          2. ffmpeg convert WAV → OGG/Opus (Telegram's voice-note
             native format; falls back to send_audio if ffmpeg is
             missing)
          3. ``send_voice`` so the message renders as a voice bubble
             with playback waveform — the UX win that makes this
             worth doing for grandma.
        """
        try:
            from windyfly.voice import synthesize as _tts_synth
        except Exception:
            return

        loop = asyncio.get_running_loop()
        try:
            wav_bytes = await loop.run_in_executor(None, _tts_synth, text)
        except Exception as e:
            logger.warning("voice-out synth failed: %s", e)
            return
        if not wav_bytes:
            return

        # Convert WAV → OGG/Opus (Telegram voice-note native format).
        # ffmpeg shells out so we don't take a python audio dep.
        ogg_bytes = await loop.run_in_executor(
            None, _wav_to_ogg_opus, wav_bytes,
        )
        try:
            if ogg_bytes:
                await message.reply_voice(voice=io.BytesIO(ogg_bytes))
            else:
                # Fallback: send the WAV as audio (works without ffmpeg
                # but appears as audio file rather than voice bubble).
                await message.reply_audio(audio=io.BytesIO(wav_bytes))
        except Exception as e:
            logger.warning("voice-out send failed: %s", e)

    async def _handle(self, update, context) -> None:
        if not update.message or not update.message.text:
            return

        sender_id = str(update.message.from_user.id)
        if self._allowed_user_ids and sender_id not in self._allowed_user_ids:
            logger.warning("Dropping Telegram message from unauthorized sender %s", sender_id)
            return

        text = update.message.text

        # ── PAUSE / RESUME / SPEND / YOLO — process BEFORE panic
        # check so the spend controls are even faster than nuclear
        # reset. No LLM, no DB, no tools — just file ops + cost
        # ledger reads.
        from windyfly.agent.spend_monitor import (
            pause as _pause_spending,
            resume as _resume_spending, get_spend_summary,
            yolo_enable, yolo_disable, yolo_status,
        )

        is_yolo, yolo_arg = _parse_yolo_command(text)
        if is_yolo:
            ack = ""
            if yolo_arg is None:
                # bare /yolo — show status, or enable default 24h if not active
                status = yolo_status()
                if status.get("active"):
                    hrs = status.get("hours_remaining", 0)
                    expires = (status.get("expires_at") or "").replace("T", " ")[:16]
                    ack = (
                        f"🚀 *YOLO mode is active*\n\n"
                        f"Auto-pause is off for {hrs:.1f} more "
                        f"hour{'s' if hrs != 1 else ''} (until {expires} UTC).\n\n"
                        f"_Say /yolo off to end early, or /yolo 48 to extend to 48 hours._"
                    )
                else:
                    result = yolo_enable(hours=24, actor=sender_id)
                    if result.get("ok"):
                        expires = (result.get("expires_at") or "").replace("T", " ")[:16]
                        ack = (
                            f"🚀 *YOLO mode ON for 24 hours*\n\n"
                            f"Auto-pause is off until {expires} UTC. I'll cook hard.\n\n"
                            f"_Say /yolo off to end early. Say /pause to stop spending right now._"
                        )
                    else:
                        ack = with_recovery_hint(
                            f"⚠ Could not enable YOLO: {result.get('error', 'unknown error')}"
                        )
            elif yolo_arg == "off":
                result = yolo_disable()
                ack = (
                    "🛑 *YOLO mode off.* Auto-pause is armed again."
                    if result.get("was_active")
                    else "YOLO wasn't active — nothing to turn off."
                )
            elif yolo_arg == "invalid":
                ack = (
                    "Try one of these:\n"
                    "• `/yolo` — status, or enable for 24 hours\n"
                    "• `/yolo 24` — enable for 24 hours\n"
                    "• `/yolo 48` — enable for 48 hours\n"
                    "• `/yolo off` — disable"
                )
            else:
                # int hours — type narrowed: the prior branches handled
                # None / "off" / "invalid", so by elimination yolo_arg
                # is an int here. Assert documents that for mypy.
                assert isinstance(yolo_arg, int)
                result = yolo_enable(hours=yolo_arg, actor=sender_id)
                if result.get("ok"):
                    expires = (result.get("expires_at") or "").replace("T", " ")[:16]
                    ack = (
                        f"🚀 *YOLO mode ON for {yolo_arg} hours*\n\n"
                        f"Auto-pause is off until {expires} UTC.\n\n"
                        f"_Say /yolo off to end early. Say /pause to stop right now._"
                    )
                else:
                    ack = with_recovery_hint(
                        f"⚠ Could not enable YOLO: {result.get('error', 'unknown error')}"
                    )

            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("yolo-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return
        is_guest, guest_arg = _parse_guest_command(text)
        if is_guest:
            from windyfly.agent.guest_mode import (
                guest_off as _guest_off,
                guest_on as _guest_on,
                guest_status as _guest_status,
            )
            if guest_arg is None:
                status = _guest_status()
                if status.get("active"):
                    when = (status.get("enabled_at") or "").replace("T", " ")[:16]
                    ack = (
                        f"👵 *Guest mode is ON* (since {when} UTC).\n\n"
                        f"I'm replying in plain English with no tech jargon. "
                        f"Say /guest off to switch back."
                    )
                else:
                    ack = (
                        "👵 *Guest mode is OFF.* I reply in my normal voice.\n\n"
                        "Say /guest on before a demo to switch into "
                        "grandma-mode (short, plain English, no tech jargon)."
                    )
            elif guest_arg == "on":
                result = _guest_on(actor=sender_id)
                ack = (
                    "👵 *Guest mode ON.* Until you say /guest off, I'll keep "
                    "replies short and plain — no IP addresses, no Docker / "
                    "WireGuard / SSH talk. Good for demos."
                ) if result.get("ok") else with_recovery_hint(
                    f"⚠ Could not enable guest mode: {result.get('error', 'unknown')}"
                )
            elif guest_arg == "off":
                result = _guest_off()
                ack = (
                    "🎩 *Guest mode OFF.* Back to my normal voice."
                    if result.get("was_active")
                    else "Guest mode wasn't on — nothing to switch off."
                )
            else:  # invalid
                ack = (
                    "Try one of these:\n"
                    "• `/guest` — show whether guest mode is on\n"
                    "• `/guest on` — switch into grandma-mode (for demos)\n"
                    "• `/guest off` — switch back to normal"
                )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("guest-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

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
                ack = with_recovery_hint(
                    "⚠ Couldn't read the cost ledger right now."
                )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("spend-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # ── /resurrect / /normal — lifeboat recovery (PR #133) ──
        # User-triggerable bridge to a free local Ollama model when
        # paid creds are dead. Pure file-flag op, no LLM, no DB —
        # works even when the agent_loop wouldn't (every paid
        # provider 401'ing). The agent_loop reads the flag at the top
        # of agent_respond and routes through Ollama with the chosen
        # model.
        if _is_resurrect_message(text):
            from windyfly.agent.resurrect import (
                resurrect as _resurrect, resurrection_state as _r_state,
            )
            existing = _r_state()
            if existing.get("active"):
                # Already resurrected — refresh ack so the user knows
                # what state they're in.
                model = existing.get("model") or "(unknown)"
                ack = (
                    f"🛟 *Lifeboat is on* — running on Ollama: "
                    f"`{model}`.\n\n"
                    f"My memory is intact. Quality won't be as sharp "
                    f"as Claude / GPT — this is the bridge model "
                    f"keeping me talkable until your usual API key "
                    f"works again.\n\n"
                    f"_Type /normal to switch back._"
                )
            else:
                # Try to flip into lifeboat. Get the previous model
                # from config so /normal can speak its name.
                prev_model = (
                    self._db
                    and (lambda: None)()  # placeholder for future config read
                ) or os.environ.get("DEFAULT_MODEL") or "your usual model"
                result = _resurrect(actor=sender_id, previous_model=str(prev_model))
                if result.get("ok"):
                    model = result.get("model")
                    ack = (
                        f"🛟 *Lifeboat mode activated.*\n\n"
                        f"I switched to a free local model: "
                        f"`{model}` (running on this machine via "
                        f"Ollama). My long-term memory and personality "
                        f"are intact. Quality won't be as sharp as "
                        f"your usual Claude / GPT, but I can keep "
                        f"helping while you fix your API key.\n\n"
                        f"_When your normal model is working again, "
                        f"say /normal to switch back._"
                    )
                elif result.get("reason") == "ollama_not_running":
                    hint = result.get("install_hint", "")
                    ack = (
                        f"🆘 I tried to switch to a free local model "
                        f"but Ollama isn't installed on this server.\n\n"
                        f"Run this once on the bot's host:\n"
                        f"```\n{hint}\n```\n"
                        f"Then say /resurrect again — I'll come "
                        f"back online."
                    )
                elif result.get("reason") == "no_models_installed":
                    hint = result.get("install_hint", "")
                    ack = (
                        f"🆘 Ollama is here but I can't find any "
                        f"models. Pull one and try again:\n"
                        f"```\n{hint}\n```"
                    )
                else:
                    err = result.get("error", "unknown error")
                    ack = (
                        f"⚠ I tried to enter lifeboat mode but "
                        f"couldn't write the flag file: {err}.\n\n"
                        f"Try /reset for a full restart, or fix your "
                        f"normal API key."
                    )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("resurrect-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # /auto-resurrect [on|off|status] (PR #145) — toggles whether
        # the bot auto-flips into lifeboat mode when paid creds die
        # mid-conversation. Default ON; user can opt-out.
        from windyfly.channels.slash_commands import (
            parse_auto_resurrect_command as _parse_auto_resurrect,
        )
        is_ar, ar_arg = _parse_auto_resurrect(text)
        if is_ar:
            from windyfly.agent.resurrect import (
                auto_resurrect_status as _ar_status,
                set_auto_resurrect as _set_ar,
            )
            if ar_arg is None:
                # Status
                state = _ar_status()
                badge = "🟢 ON" if state["enabled"] else "🔴 OFF"
                cooldown = (
                    f" (in cooldown — wait up to "
                    f"{state['cooldown_seconds']:.0f}s)"
                    if state["in_cooldown"] else ""
                )
                ack = (
                    f"🚨 *Auto-resurrect:* {badge}{cooldown}\n\n"
                    f"_When my usual model hits a rate limit "
                    f"mid-chat, auto-resurrect switches me to a free "
                    f"local model so we can keep talking. I'll "
                    f"always tell you when this happens — never "
                    f"silent. Toggle with /auto-resurrect on or off._"
                )
            elif ar_arg == "on":
                result = _set_ar(True, actor=sender_id)
                if result.get("ok"):
                    ack = (
                        "🟢 *Auto-resurrect ON.*\n\n"
                        "If my usual model hits a rate limit mid-chat, "
                        "I'll auto-switch to a free local model and "
                        "tell you about it. /auto-resurrect off to "
                        "disable."
                    )
                else:
                    ack = f"⚠ Couldn't enable: {result.get('error', 'unknown')}"
            elif ar_arg == "off":
                result = _set_ar(False, actor=sender_id)
                if result.get("ok"):
                    ack = (
                        "🔴 *Auto-resurrect OFF.*\n\n"
                        "If my usual model fails mid-chat, you'll get "
                        "the standard offline message. Type "
                        "/resurrect to manually switch to a free "
                        "local model when you want."
                    )
                else:
                    ack = f"⚠ Couldn't disable: {result.get('error', 'unknown')}"
            else:  # invalid
                ack = (
                    "Try one of these:\n"
                    "• `/auto-resurrect` — show current setting\n"
                    "• `/auto-resurrect on` — enable auto-switch\n"
                    "• `/auto-resurrect off` — disable auto-switch"
                )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("auto-resurrect-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        if _is_normal_message(text):
            from windyfly.agent.resurrect import normalize as _normalize
            result = _normalize()
            if not result.get("ok"):
                ack = (
                    f"⚠ I couldn't clear the lifeboat flag: "
                    f"{result.get('error', 'unknown')}. "
                    f"Try /reset."
                )
            elif result.get("was_resurrected"):
                prior = result.get("prior_model") or "the local model"
                normal_model = (
                    os.environ.get("DEFAULT_MODEL") or "your usual brain"
                )
                ack = (
                    f"✨ *Back to normal.* I'm using `{normal_model}` "
                    f"again.\n\n"
                    f"_Was running on `{prior}` in lifeboat mode. "
                    f"If your usual model fails, say /resurrect "
                    f"anytime._"
                )
            else:
                ack = (
                    "I wasn't in lifeboat mode — already running on "
                    "my usual brain."
                )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("normal-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # /lifeboat — read-only status. Distinct from /resurrect
        # (which TOGGLES into lifeboat) and /normal (which toggles
        # OUT). Surfaces every piece of state a user/operator needs
        # to debug "is the bot wobbly right now?" without opening
        # logs.
        if _is_lifeboat_status_message(text):
            try:
                from windyfly.agent.resurrect import format_lifeboat_status
                ack = format_lifeboat_status()
            except Exception as e:
                logger.warning("lifeboat-status build failed: %s", e)
                ack = f"⚠ Couldn't read lifeboat status: {e}"
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("lifeboat-status reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # ── /version /uptime /whoami — pure-read introspection ──
        # v13 stress 2026-05-02 caught the bot improvising "I don't
        # have a /version command available" because the menu listed
        # them but no channel handler existed. These return a hard
        # answer with the live git SHA + uptime so the user can
        # verify "am I running the latest?" in one tap.
        if _is_version_message(text) or _is_uptime_message(text) or _is_whoami_message(text):
            try:
                from windyfly.observability.version_info import (
                    format_uptime_reply, format_version_reply, format_whoami_reply,
                )
                if _is_version_message(text):
                    ack = format_version_reply()
                elif _is_uptime_message(text):
                    ack = format_uptime_reply()
                else:
                    ack = format_whoami_reply()
            except Exception as e:
                logger.warning("version/uptime/whoami reply failed: %s", e)
                ack = with_recovery_hint(
                    "⚠ Couldn't gather version info right now."
                )
            try:
                await self._send_long_reply(update.message, ack)
            except Exception as e:
                logger.warning("version-ack reply failed: %s", e)
            self._last_message_at = time.time()
            return

        # ── NUCLEAR RESET — must be FIRST, before agent loop ──
        # If the bot is stuck/confused, this short-circuits BEFORE
        # any LLM / DB / tool dispatch. Long-term memory is safe.
        if _is_panic_message(text):
            logger.warning("PANIC: nuclear reset requested by %s", sender_id)
            # /reset is the panic button: user wants a CLEAN slate.
            # Clear the resurrect flag — without this, /reset only
            # cleared the conversation thread but left the bot in
            # lifeboat mode, so every subsequent message routed
            # through Ollama and timed out. (Surfaced 2026-05-10:
            # bot stuck in resurrection for 2h, every reply
            # returned "Local model error: timed out".)
            #
            # Pause / yolo / guest flags are NOT cleared — those are
            # explicit persistent state the user opted into.
            try:
                from windyfly.agent.resurrect import normalize as _r_normalize
                _r_normalize()
            except Exception as e:
                logger.warning("panic /reset: failed to clear resurrect flag: %s", e)
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
