"""Matrix/Synapse bot channel for Windy Fly.

Connects to the Windy Chat homeserver as the agent's OWN Matrix id
(``@agent_<passport>``, minted at hatch from its Eternitas passport and
confirmed via whoami at login), handles DMs, auto-accepts invites,
shows typing indicators and presence.

Windy Fly does not do Matrix end-to-end encryption: the default build
has no olm, so messages in an encrypted room cannot be read. Such a
message is logged once per event with a plain explanation.

Production hardening:
  - Exponential backoff on sync failures (1s → 60s max)
  - 5-minute heartbeat logging (connected, rooms, pending queue)
  - Graceful shutdown on SIGTERM/SIGINT (offline presence, flush, close)
  - Automatic re-login on expired access token
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import signal
import time
from types import SimpleNamespace
from typing import Any

import nio

from windyfly.agent.loop import agent_respond
from windyfly.channels.base import ChannelAdapter, OutgoingMessage
from windyfly.channels.matrix_self import set_confirmed
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Chat's marker (Windy Chat #319): a runtime's COMMAND REPLY (/status, /whoami, ...) carries this key so
# another agent never feeds it to its model. Same key, value true, set only when replying to a command.
COMMAND_REPLY_KEY = "ai.windy.command_reply"


def _is_command_reply(event: Any) -> bool:
    source = getattr(event, "source", None)
    content = source.get("content") if isinstance(source, dict) else None
    return isinstance(content, dict) and content.get(COMMAND_REPLY_KEY) is True


def _room_session(bot: Any, room_id: str) -> str:
    """The room's rolling session id, ``matrix:<room_id>:v<N>`` (the shape Telegram and the catch-up use).

    It is read from the reset counter on every call, so it survives a restart and a ``/new`` moves it on.
    The thread itself lives in this agent's own database, so two agents (two owners) never share one.
    """
    from windyfly.agent.session_reset import next_session_id

    sid = next_session_id("matrix", room_id)
    bot._room_sessions[room_id] = sid
    return sid


def _agent_name(room: Any, mxid: str) -> str:
    """An agent's name: the room's display name, else the name Windy Chat lists for it (a room that has not
    loaded member names yet gave only the id, and ask_agent could not find "p9b089866": P9 run 1 re-run)."""
    from windyfly.agent import teams as _teams

    shown = room.user_name(mxid) if hasattr(room, "user_name") else None
    if isinstance(shown, str) and shown.strip() and shown != mxid:
        return shown.strip()
    return _teams.sibling_name(mxid) or mxid


def _bound_turn(team_room: Any, fn: Any) -> Any:
    """``fn`` run with the teamwork room bound in the agent-turn thread (ask_agent reads it there)."""
    from windyfly.channels import teamwork as _tw

    def call(*args: Any, **kwargs: Any) -> Any:
        _tw.bind(team_room)
        try:
            return fn(*args, **kwargs)
        finally:
            _tw.bind(None)

    return call


def _bound_origin(origin: Any, fn: Any) -> Any:
    """``fn`` run with the owner's turn bound in the agent-turn thread (message_agent records its asks there)."""
    from windyfly.channels import report_back as _rb

    def call(*args: Any, **kwargs: Any) -> Any:
        _rb.bind(origin)
        try:
            return fn(*args, **kwargs)
        finally:
            _rb.bind(None)

    return call


def _no_tools_turn(fn: Any) -> Any:
    """``fn`` run with NO tools offered or allowed (report-back: the turn only tells the owner what came back)."""
    from windyfly.tools.bands import task_turn_tools

    def call(*args: Any, **kwargs: Any) -> Any:
        with task_turn_tools(frozenset()):
            return fn(*args, **kwargs)

    return call


def _task_tools_turn(fn: Any) -> Any:
    """``fn`` run with the task-turn allowlist set in the agent-turn thread (public reads only)."""
    from windyfly.tools.bands import task_turn_tools

    def call(*args: Any, **kwargs: Any) -> Any:
        with task_turn_tools():
            return fn(*args, **kwargs)

    return call


def _teamwork_backstage(team_room: Any) -> dict[str, Any]:
    """The owner turn sent requests: its own reply goes backstage (m.notice in the task's thread)."""
    from windyfly.channels import teamwork as _tw

    root = _tw.root_for_event(team_room.owner_event_id)
    if root is None or not _tw.children(root, team_room.me):
        return {}
    out: dict[str, Any] = {"msgtype": "m.notice"}
    if root.thread:
        out["m.relates_to"] = {"rel_type": "m.thread", "event_id": root.thread}
    return out


class MatrixCredentialsError(RuntimeError):
    """No usable Matrix identity (no passport session, no token/password).

    Distinct type so the launcher can show a friendly, grandma-readable
    message instead of dumping a raw traceback.
    """


class MatrixIdentityError(RuntimeError):
    """Logged in, but this agent's own Matrix user id is still unknown.

    Without it the agent cannot tell its own messages apart and would answer
    itself, so the channel refuses to start (the supervisor retries).
    """


# Pauses between whoami retries when no user id is configured.
_WHOAMI_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)


class MatrixSyncDead(RuntimeError):
    """Raised from the SyncError callback after a run of consecutive
    failed /sync calls, to escape nio's sync_forever and hand control back
    to the reconnect loop in start(). The message carries the last HTTP
    status so _is_token_expired_error can route a 401 to re-login."""


# Backoff constants
_INITIAL_BACKOFF_S = 1
_MAX_BACKOFF_S = 60
_HEARTBEAT_INTERVAL_S = 300  # 5 minutes
# nio's sync_forever retries a failed /sync with NO sleep. Measured on
# Windy 0 2026-09-03 → 09-05: one failed sync every ~135 ms (811k log
# lines in a day) against a homeserver returning 502, while the heartbeat
# still said polling=true. The SyncError callback below sleeps with
# exponential backoff and, after this many consecutive failures, raises
# out of sync_forever so the outer reconnect loop (re-login on 401,
# full_state resync) gets a turn.
_SYNC_FAILS_BEFORE_RECONNECT = 8
# A sync that has not succeeded for this long is reported as sync_ok=false
# in the heartbeat file. It does NOT flip `polling` — the loop is alive and
# backing off correctly; a restart would not help a dead homeserver.
_SYNC_STALE_S = 600.0
# An outage older than this is reported as sync_ok=false even though the
# per-run failure counter resets on every reconnect cycle.
_SYNC_OUTAGE_S = 120.0
# Kept replies are retried on a good /sync at most this often, so a reply the
# homeserver keeps refusing is not re-sent on every sync.
_PENDING_RETRY_EVERY_S = 30.0
# One pass from the sync callback sends at most this many kept replies, so a
# hung send cannot hold the next /sync for long; the rest wait for the next pass.
_PENDING_PER_SYNC = 3
# A reply still kept after this long is dropped (one WARNING, counted in the heartbeat).
_PENDING_MAX_AGE_S = 24 * 3600.0
# Teamwork expiry: the stop look-back reads this many events, and allows the server's clock to lag ours by this.
_STOP_LOOKBACK_EVENTS = 100
_STOP_SKEW_MS = 60_000


class WindyFlyMatrixBot(ChannelAdapter):
    """Windy Fly Matrix bot — lives inside Windy Chat as a contact."""

    name = "matrix"

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        write_queue: WriteQueue,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.write_queue = write_queue
        self.tool_registry = tool_registry

        matrix_config = config.get("matrix", {})
        # `windy bring-home` writes the handed-over identity to the env file
        # (MATRIX_HOMESERVER / MATRIX_BOT_USER); env wins over config.
        homeserver = (os.environ.get("MATRIX_HOMESERVER", "").strip()
                      or matrix_config.get("homeserver", "https://chat.windychat.ai"))
        # Each agent's Matrix id is its own @agent_<passport>, learned at login
        # (passport session or whoami). A configured bot_user is only a starting
        # hint (still read so an old windyfly.toml keeps working) and is required
        # only for the self-hosted password login.
        bot_user = (os.environ.get("MATRIX_BOT_USER", "").strip()
                    or str(matrix_config.get("bot_user", "") or "").strip())

        self.bot_user_id = bot_user

        self.client = nio.AsyncClient(homeserver, bot_user)
        self.client.device_id = "WindyFlyAgent"

        # Map room_id → session_id for session continuity per room
        self._room_sessions: dict[str, str] = {}

        # DM room from the hatch, if the EPT session reports it
        self._hatch_dm_room_id: str | None = None

        # Pending responses for offline queue
        self._pending_responses: list[tuple[str, str]] = []
        # The maintenance loop, when this process is the agent's maintenance runner (strand N4.30).
        self._maintenance_task: asyncio.Task[None] | None = None
        self._maintenance_stop_event: asyncio.Event | None = None
        self._last_pending_retry = 0.0
        # When each kept reply was first kept; replies dropped for age are counted.
        self._pending_kept_at: dict[tuple[str, str], float] = {}
        self._pending_dropped = 0
        # Rooms the owner just stopped by a typed stop word (agent-teamwork.v1: the one quiet line).
        self._stop_followups: list[str] = []
        # Rooms THIS process has handled a stop for (cancel its tasks, maybe confirm) since the last resume.
        self._stopped_here: set[str] = set()
        # Teamwork: the server's joined members per room, (fetched at, ids).
        self._members_cache: dict[str, tuple[float, set[str]]] = {}

        # Track boot time to ignore old messages
        self._boot_time = time.time()
        self._report_timers: set[Any] = set()      # report-back deadline and settle timers (cancelled on stop)

        # Shutdown flag for graceful termination
        self._shutting_down = False
        # The in-flight sync_forever future, so a shutdown signal can cancel
        # it directly. nio's sync_forever has its own internal retry loop, so
        # closing the client from a side task does NOT unblock it — it just
        # logs "Timed out, sleeping for 0s" forever and the process never
        # exits, forcing systemd to SIGKILL (prod windy-0@matrix, 2026-07-04).
        self._sync_task: asyncio.Task | None = None
        # Guard so graceful shutdown runs exactly once (signal + finally).
        self._graceful_done = False

        # Connection state tracking
        self._connected = False
        self._last_sync_success: float = 0.0

        # Backoff state
        self._backoff = _INITIAL_BACKOFF_S
        # Consecutive failed /sync responses inside the current sync_forever.
        self._sync_failures = 0
        # Invites the invite gate could not decide yet (Chat 5xx/unreachable): room_id -> (room, event, first seen).
        self._invite_rechecks: dict[str, tuple[Any, Any, float]] = {}
        self._last_sync_error: str = ""
        # When the current outage began (first failed sync with no success
        # since). Survives the per-sync_forever reset of _sync_failures so
        # the heartbeat stays honest across reconnect cycles.
        self._sync_failed_since: float | None = None

    async def login(self) -> None:
        """Log into the Matrix homeserver.

        Preferred path (one-soul, 2026-07-05): mint a session for the
        agent's OWN ``@agent_<passport>`` identity from its Eternitas
        passport token (chat_session.py → windy-chat #111) — the
        identity the hatch provisioned and the one the midwife yields
        to. Falls back to the legacy MATRIX_BOT_TOKEN / PASSWORD envs
        for self-hosted setups without a passport.
        """
        from windyfly.chat_session import fetch_agent_chat_session

        # A body brought home (`windy bring-home`) holds the Matrix device
        # Windy Chat handed over (MATRIX_BOT_TOKEN + MATRIX_DEVICE_ID). Use it
        # as is: asking chat for a fresh session would mint a second device
        # for the same agent.
        handed_over = bool(os.environ.get("MATRIX_BOT_TOKEN") and os.environ.get("MATRIX_DEVICE_ID"))
        session = None if handed_over else await fetch_agent_chat_session(self.config)
        if session and session.get("access_token"):
            self.bot_user_id = session["matrix_user_id"]
            self.client.user = self.bot_user_id
            self.client.user_id = self.bot_user_id
            self.client.access_token = session["access_token"]
            # Use the minted device: the token belongs to it, and a
            # hardcoded id would not match the session Chat issued.
            if session.get("device_id"):
                self.client.device_id = session["device_id"]
            if session.get("dm_room_id"):
                self._hatch_dm_room_id = session["dm_room_id"]
            logger.info(
                "Windy Fly logged in as its own identity %s (one-soul)",
                self.bot_user_id,
            )
            set_confirmed(self.bot_user_id)
            return

        token = os.environ.get("MATRIX_BOT_TOKEN")
        password = os.environ.get("MATRIX_BOT_PASSWORD")

        if token:
            how = "token"
            self.client.access_token = token
            self.client.user_id = self.bot_user_id
            if os.environ.get("MATRIX_DEVICE_ID"):
                self.client.device_id = os.environ["MATRIX_DEVICE_ID"]
            if os.environ.get("MATRIX_DM_ROOM_ID"):
                self._hatch_dm_room_id = os.environ["MATRIX_DM_ROOM_ID"]
        elif password:
            how = "password"
            if not self.bot_user_id:
                raise MatrixCredentialsError(
                    "MATRIX_BOT_PASSWORD is set but no Matrix user id is: set "
                    "MATRIX_BOT_USER (or [matrix] bot_user) to the account it "
                    "belongs to."
                )
            response = await self.client.login(password)
            if isinstance(response, nio.LoginError):
                raise RuntimeError(f"Matrix login failed: {response.message}")
        else:
            raise MatrixCredentialsError(
                "No Matrix credentials found. Set MATRIX_BOT_TOKEN or "
                "MATRIX_BOT_PASSWORD in your .env file."
            )

        # The access token is the source of truth for WHO we are — not the
        # config `matrix.bot_user`, which drifts. Windy 0 shipped with a
        # MATRIX_BOT_TOKEN for @agent_<passport> but config still said
        # @windyfly on the legacy Windy Word chat host; the mismatch meant _on_invite
        # (state_key == bot_user_id) ignored every invite addressed to the
        # real identity, so the agent silently never joined a grandma's room
        # and never replied on Windy Chat (2026-07-06). Resolve the true
        # user id from the token and realign self-identity to it.
        confirmed = await self._resolve_identity_from_token()
        # With no configured id, whoami is the only source. Never run with an
        # empty id: `event.sender == self.bot_user_id` would never match and
        # the agent would answer its own messages in a loop.
        for delay in _WHOAMI_RETRY_DELAYS:
            if self.bot_user_id:
                break
            await asyncio.sleep(delay)
            confirmed = await self._resolve_identity_from_token()
        if not self.bot_user_id:
            raise MatrixIdentityError(
                "Logged in to Matrix, but the homeserver did not confirm this "
                "agent's user id (whoami failed). Not starting the channel."
            )
        logger.info(
            "Windy Fly logged in via %s as %s (%s)", how, self.bot_user_id,
            "confirmed by the homeserver" if confirmed else "as configured; whoami unavailable",
        )
        # Teams (pair rooms) and phone tools read this instead of MATRIX_BOT_USER.
        set_confirmed(self.bot_user_id)

    async def _resolve_identity_from_token(self) -> bool:
        """Correct self.bot_user_id to the access token's real owner.

        Returns True when whoami answered with a user id. Never raises — an
        identity we can't verify is left as configured (login() refuses to
        go on only when there is none at all).
        """
        try:
            resp = await self.client.whoami()
            real_id = getattr(resp, "user_id", None)
            if not real_id:
                return False
            if real_id != self.bot_user_id:
                if self.bot_user_id:
                    logger.warning(
                        "Matrix identity mismatch: token is %s but config said "
                        "%s — using the token's identity (invite-accept and "
                        "self-filtering depend on it).",
                        real_id, self.bot_user_id,
                    )
                self.bot_user_id = real_id
                self.client.user_id = real_id
                self.client.user = real_id
            return True
        except Exception as exc:
            logger.warning("whoami identity resolution failed: %s", exc)
            return False

    async def _relogin_with_password(self) -> bool:
        """Attempt to re-login with password when the access token expires.

        Returns True if re-login succeeded, False otherwise.
        """
        password = os.environ.get("MATRIX_BOT_PASSWORD")
        if not password:
            logger.error(
                "Access token expired and no MATRIX_BOT_PASSWORD set — "
                "cannot re-authenticate. Set MATRIX_BOT_PASSWORD in .env."
            )
            return False

        try:
            logger.info("Access token expired — attempting re-login with password...")
            response = await self.client.login(password)
            if isinstance(response, nio.LoginError):
                logger.error("Re-login failed: %s", response.message)
                return False

            logger.info("Re-login successful. New token acquired.")
            return True
        except Exception as exc:
            logger.error("Re-login attempt failed: %s", exc)
            return False

    async def _turn_budget_hit(self, room: nio.MatrixRoom, other_name: str) -> None:
        """Once per budget episode: one event, and one plain line to the owner in their own room
        (never in the looping room, where it would be one more message for the other agent)."""
        from windyfly.channels import turn_budget as _budget
        from windyfly.observability.events import log_event

        ref = _budget.room_ref(room.room_id)
        log_event(self.db, self.write_queue, "agent.turn_budget_hit",
                  {"room": ref, "limit": _budget.limit(), "window_s": int(_budget.window_s())})
        owner_room = self._hatch_dm_room_id
        if not owner_room or owner_room == room.room_id:
            logger.warning("turn budget reached in room %s; no separate owner room to tell", ref)
            return
        try:
            await self.client.room_send(owner_room, "m.room.message", {
                "msgtype": "m.text", "windy_original": True,
                "body": _budget.owner_notice(other_name, getattr(room, "display_name", None))})
        except Exception as e:
            logger.error("turn budget owner notice failed: %s", e)

    def _stop_or_unaddressed(self, room: Any, event: Any, room_id: str, sender: str, body: str,
                             from_agent: bool) -> bool:
        """True when this message must not become a turn: the owner's stop, a stopped room, or another
        agent's message that does not address this agent (agent-teamwork.v1 P1+P5)."""
        from windyfly.channels import room_stop as _stop
        from windyfly.channels import turn_budget as _budget

        # The owner's stop: silences replies to other agents in this room, gets no reply and no model
        # call, and never resets the budget. The owner's next normal message resumes.
        if not from_agent and _stop.is_owner(sender, self.config):
            from windyfly.channels import teamwork as _tw

            # Teamwork on (agent-teamwork.v1): in a room with another of the owner's agents, the
            # contract's wider rule (a short message with a stop word in it) is a stop too.
            if _stop.is_stop_word(body) or (_tw.enabled() and _tw.is_contract_stop(body)
                                            and self._wide_stop_room(room)):
                _stop.stop(room_id)
                from windyfly.channels import report_back as _rb_stop

                if _rb_stop.cancel_room(room_id):
                    logger.info("owner stop: report-back for room %s cancelled", _budget.room_ref(room_id))
                logger.info("owner stop in room %s: replies to agents silenced", _budget.room_ref(room_id))
                if room_id not in self._stopped_here:      # this process's own record: the shared file
                    self._stopped_here.add(room_id)        # may already say stopped (Hub on #602)
                    self._stop_followups.append(room_id)
                return True
            _stop.resume(room_id)
            self._stopped_here.discard(room_id)
            if _tw.enabled():
                # A PERSON MESSAGE (the owner's, not a stop): the room's pseudo-roots start fresh. Only this
                # resets them (agent-teamwork.v1 PSEUDO-ROOT; never time, a stop, a resume or another sender).
                _tw.person_spoke(room_id)
        if from_agent:
            if _stop.stopped(room_id):
                logger.info("owner stop: agent %s in room %s not answered", sender, _budget.room_ref(room_id))
                return True
            src = getattr(event, "source", None)
            content = src.get("content") if isinstance(src, dict) else None
            if not _stop.addressed_to(content, self.bot_user_id or getattr(self.client, "user_id", None),
                                      int(getattr(room, "joined_count", 0) or 0)):
                logger.info("agent %s in room %s did not address me: no turn", sender, _budget.room_ref(room_id))
                return True
        return False

    async def _on_stop_event(self, room: nio.MatrixRoom, event: Any) -> None:
        """``ai.windy.stop`` from the room's owner (the phone's Stop button): silence replies to agents.
        With teamwork on, also ``ai.windy.resume`` and the owner's ``ai.windy.agents_stopped`` state.
        Any fault is logged and dropped, never raised into nio's sync loop (Hub on #599)."""
        try:
            await self._stop_event(room, event)
        except Exception as e:  # noqa: BLE001
            logger.warning("stop event handling failed (%s); event dropped", type(e).__name__)

    async def _stop_event(self, room: nio.MatrixRoom, event: Any) -> None:
        from windyfly.channels import room_stop as _stop
        from windyfly.channels import teamwork as _tw
        from windyfly.channels import turn_budget as _budget

        etype = getattr(event, "type", None)
        if etype not in (_stop.STOP_EVENT, _tw.RESUME_EVENT, _tw.STOPPED_STATE):
            return
        if _tw.enabled():
            await self._refresh_members(room.room_id)
        if not _stop.is_owner(getattr(event, "sender", None), self.config):
            if etype == _stop.STOP_EVENT:
                logger.info("stop event from a non-owner in room %s: ignored", _budget.room_ref(room.room_id))
            return
        if etype == _stop.STOP_EVENT:
            stopping = True
        elif not _tw.enabled():
            return
        elif etype == _tw.RESUME_EVENT:
            stopping = False
        else:
            src = getattr(event, "source", None)
            content = src.get("content") if isinstance(src, dict) else None
            if not (isinstance(src, dict) and src.get("state_key") == "" and isinstance(content, dict)
                    and isinstance(content.get("stopped"), bool)):
                return
            stopping = content["stopped"]
        if not stopping:
            _stop.resume(room.room_id)
            self._stopped_here.discard(room.room_id)
            logger.info("owner resume in room %s", _budget.room_ref(room.room_id))
            return
        _stop.stop(room.room_id)
        from windyfly.channels import report_back as _rb_stop

        _rb_stop.cancel_room(room.room_id)
        logger.info("owner stop event in room %s: replies to agents silenced", _budget.room_ref(room.room_id))
        if room.room_id not in self._stopped_here:
            self._stopped_here.add(room.room_id)
            await self._after_stop(room)

    def _owner_agents_in_room(self, room: Any) -> set[str]:
        """The owner's OTHER agents joined to this room (sibling ids from Windy Chat), by Matrix id. The room's
        member list plus the server's joined members (_refresh_members): after a restart the client knows only
        the members it has seen lately (P9: a restarted chief saw no team and ask_agent found nobody)."""
        from windyfly.agent import teams as _teams

        users = set(getattr(room, "users", None) or {})
        users |= self._members_cache.get(getattr(room, "room_id", ""), (0.0, set()))[1]
        sibs = _teams.sibling_ids()
        me = self.bot_user_id or getattr(self.client, "user_id", None)
        return {u for u in users if isinstance(u, str) and u != me and u in sibs}

    def _wide_stop_room(self, room: Any) -> bool:
        """Does the contract's wide stop rule apply here (2+ of the owner's agents joined)? While the owner's
        agent list is still unknown (right after a restart, before the first good read) any other agent joined
        counts: the wide rule wins, as in Chat's roster (Hub on #605)."""
        from windyfly.agent import teams as _teams
        from windyfly.channels import parity as _parity

        if self._owner_agents_in_room(room):
            return True
        if _teams.siblings_known():
            return False
        users = set(getattr(room, "users", None) or {})
        users |= self._members_cache.get(getattr(room, "room_id", ""), (0.0, set()))[1]
        me = self.bot_user_id or getattr(self.client, "user_id", None)
        return any(isinstance(u, str) and u != me and _parity.passport_of(u) for u in users)

    async def _refresh_members(self, room_id: str, max_age_s: float = 120.0) -> None:
        """Teamwork on: ask the server who is joined (cached for two minutes). Not logged in: nothing."""
        if not getattr(self.client, "access_token", None):
            return
        hit = self._members_cache.get(room_id)
        if hit and time.time() - hit[0] < max_age_s:
            return
        try:
            resp = await self.client.joined_members(room_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("joined members not read: %s", type(e).__name__)
            return
        if isinstance(resp, nio.JoinedMembersResponse):
            self._members_cache[room_id] = (time.time(), {m.user_id for m in resp.members})

    def _person_turn_is_mine(self, room: Any, event: Any, body: str, agents_here: set[str]) -> bool:
        """In a team room, is a person's message this agent's turn? agent-teamwork.v1 (#158 at 0362388): a
        message whose m.mentions.user_ids names any of the owner's agents is a turn ONLY for those agents;
        a message that mentions none is a turn for every agent (a designated head agent is a v1.1 item)."""
        me = self.bot_user_id or getattr(self.client, "user_id", None) or ""
        team = {a for a in agents_here if a} | {me}
        src = getattr(event, "source", None)
        content = src.get("content") if isinstance(src, dict) else None
        mentions = content.get("m.mentions") if isinstance(content, dict) else None
        ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
        addressed = {u for u in ids if isinstance(u, str) and u in team} if isinstance(ids, list) else set()
        return me in addressed if addressed else True

    async def _after_stop(self, room: Any) -> None:
        """Teamwork on: cancel the room's open tasks; the owner's agent with the smallest id in the room
        sends the one quiet line (m.notice, never pushed). Teamwork off: nothing (the stop is silent)."""
        from windyfly.channels import teamwork as _tw

        if not _tw.enabled():
            return
        n = _tw.cancel_room(room.room_id)
        me = self.bot_user_id or getattr(self.client, "user_id", None) or ""
        if n:
            logger.info("owner stop: %d open task(s) cancelled", n)
        if me and _tw.confirms_stop(me, self._owner_agents_in_room(room)):
            try:
                await self.client.room_send(room.room_id, "m.room.message", {
                    "msgtype": "m.notice", "body": _tw.STOP_LINE, "windy_original": True})
            except Exception as e:
                logger.error("stop line failed: %s", e)

    # ── report-back for message_agent (windyfly.channels.report_back) ─────────────────────────

    def _report_origin(self, room_id: str, session_id: str, event: Any, band: Any) -> Any:
        """The owner's own turn: where message_agent's answers come back to. None for anyone else."""
        from windyfly.agent.capabilities.descriptor import Band
        from windyfly.channels import report_back as _rb
        from windyfly.channels import silence as _silence

        if not (_silence.enabled() and _rb.enabled()) or band is None or int(band) < int(Band.OWNER):
            return None
        loop = asyncio.get_running_loop()
        event_id = getattr(event, "event_id", None)

        def on_first_ask(rnd: Any) -> None:
            loop.call_soon_threadsafe(self._schedule_report, rnd)

        return _rb.Origin(room=room_id, session=session_id, event_id=event_id if isinstance(event_id, str) else None,
                          started_ms=int(time.time() * 1000), on_first_ask=on_first_ask)

    def _catch_answer(self, room_id: str, sender: str, event: Any, body: str) -> bool:
        """The asked partner's plain message in a pair room this agent is waiting on, strictly after the ask on the
        server clock: a part of its answer (no turn). A reply to the ask completes it; else it completes after the
        partner goes quiet. The last answer of a round sends its report."""
        from windyfly.agent import teams as _teams
        from windyfly.channels import report_back as _rb

        if not _rb.waiting_in(room_id) or sender == self.bot_user_id or sender not in _teams.sibling_ids():
            return False
        src = getattr(event, "source", None)
        content = src.get("content") if isinstance(src, dict) else None
        if not isinstance(content, dict) or content.get("msgtype") != "m.text":
            return False                                   # an agent's notice or typed task is not an answer
        rel = content.get("m.relates_to")
        rel = rel if isinstance(rel, dict) else {}
        if rel.get("rel_type") == "m.replace":
            return False                                   # an edit is never the answer
        reply = rel.get("m.in_reply_to")
        reply_to = reply.get("event_id") if isinstance(reply, dict) else None
        got = _rb.add_part(room_id, sender, body, int(getattr(event, "server_timestamp", 0) or 0),
                           reply_to if isinstance(reply_to, str) else None)
        if got is None:
            return False
        rnd, ask = got
        logger.info("report-back: a message from the asked agent caught in a pair room (%d part(s))", len(ask.parts))
        self._settle_later(rnd, ask, 0.0)
        return True

    def _later(self, delay: float, fn: Any) -> None:
        """A timer this bot cancels on shutdown."""
        loop = asyncio.get_running_loop()
        handle: Any = None

        def run() -> None:
            self._report_timers.discard(handle)
            fn()

        handle = loop.call_later(delay, run)
        self._report_timers.add(handle)

    def _settle_later(self, rnd: Any, ask: Any, delay: float) -> None:
        from windyfly.channels import report_back as _rb

        def check() -> None:
            wait = _rb.settle(ask)
            if wait is not None:
                self._settle_later(rnd, ask, wait)
            elif rnd.complete():
                asyncio.ensure_future(self._report_round(rnd))

        if delay <= 0:
            check()
        else:
            self._later(delay, check)

    def _schedule_report(self, rnd: Any) -> None:
        delay = max(1.0, rnd.deadline - time.time()) + 1.0
        self._later(delay, lambda: asyncio.ensure_future(self._report_round(rnd)))

    async def _report_round(self, rnd: Any) -> None:
        """ONE report in the chat the owner asked from: a model turn on the fenced answers with NO tools, or the
        plain line when nobody answered, the model fails or it says nothing. An owner stop drops it."""
        from windyfly.agent.capabilities.descriptor import Band
        from windyfly.agent.executor import run_turn
        from windyfly.channels import report_back as _rb
        from windyfly.channels import room_stop as _stop

        if not _rb.claim(rnd):
            return
        room = self.client.rooms.get(rnd.origin_room) or SimpleNamespace(room_id=rnd.origin_room, users={})
        # A stop on the server that this process hasn't read yet: look back over the room since the round began,
        # by time (no anchor, which a busy room could push out of the window). A failed read drops the report.
        if _stop.stopped(rnd.origin_room) or await self._owner_stopped_since(
                room, rnd.started_ms, limit=_STOP_LOOKBACK_EVENTS, fail_closed=True):
            logger.info("report-back: the owner stopped the room (or the look-back failed); no report")
            return
        text = ""
        if any(a.answer is not None for a in rnd.asks):
            started_ms = int(time.time() * 1000)
            try:
                text = str(await run_turn(_no_tools_turn(agent_respond), self.config, self.db, self.write_queue,
                                          _rb.report_turn_text(rnd), rnd.origin_session, self.tool_registry,
                                          band=Band.USER) or "").strip()   # other agents' words: no tools at all
            except Exception as e:  # noqa: BLE001
                from windyfly.channels.errors import classify

                logger.error("report-back turn failed: %s", classify(e).log_message)
                text = ""
            if _stop.stopped(rnd.origin_room) or await self._owner_stopped_since(room, started_ms):
                logger.info("report-back: the owner stopped the room during the report turn; dropped")
                return
        body = text or _rb.plain_line(rnd)
        content = {"msgtype": "m.text", "body": body, "windy_original": True}
        content["windy_lang"] = self._detect_lang(body)
        try:
            await self.client.room_send(rnd.origin_room, "m.room.message", content)
        except Exception as e:
            logger.error("report-back send failed: %s", e)

    # ── agent teamwork (agent-teamwork.v1; windyfly.channels.teamwork) ───────────────────────

    async def _team_room_for(self, room: Any, event: Any, body: str, band: Any) -> Any:
        """The owner's own turn in a room with other agents of the owner: what ask_agent needs, or None."""
        from windyfly.agent import teams as _teams
        from windyfly.agent.capabilities.descriptor import Band
        from windyfly.channels import teamwork as _tw

        if not _tw.enabled() or band is None or int(band) < int(Band.OWNER):
            return None
        if _teams.siblings_stale():
            await asyncio.to_thread(_teams.refresh_siblings)
        agents = self._owner_agents_in_room(room)
        if not agents:
            return None
        loop = asyncio.get_running_loop()
        room_id = room.room_id

        def send(content: dict[str, Any]) -> bool:
            fut = asyncio.run_coroutine_threadsafe(self.client.room_send(room_id, "m.room.message", content), loop)
            try:
                return isinstance(fut.result(timeout=20), nio.RoomSendResponse)
            except Exception as e:  # noqa: BLE001
                logger.error("teamwork request send failed: %s", type(e).__name__)
                return False

        def on_request(root: Any) -> None:
            loop.call_soon_threadsafe(self._schedule_expiry, root)

        started_ms = int(time.time() * 1000)

        def stopped() -> bool:
            fut = asyncio.run_coroutine_threadsafe(self._owner_stopped_since(room, started_ms), loop)
            try:
                return bool(fut.result(timeout=10))
            except Exception:  # noqa: BLE001
                return False

        event_id = getattr(event, "event_id", None)
        return _tw.TurnRoom(room=room_id, me=self.bot_user_id or "",
                            owner_event_id=event_id if isinstance(event_id, str) else None,
                            owner_text=body, agents={a: _agent_name(room, a) for a in sorted(agents)},
                            send=send, on_request=on_request, stopped=stopped)

    async def _task_turn(self, room: Any, sender: str, display_name: str, kind: str, task: Any) -> None:
        """One turn on typed task traffic: a request addressed to this agent (its ONE result goes back),
        or every result in for a task this agent gave out (its ONE answer goes to the owner)."""
        from windyfly.agent.capabilities.descriptor import Band
        from windyfly.agent.executor import run_turn
        from windyfly.channels import room_stop as _stop
        from windyfly.channels import teamwork as _tw
        from windyfly.channels.identity import resolve_band

        room_id = room.room_id
        me = self.bot_user_id or ""

        def names(mx: str) -> str:
            return room.user_name(mx) or mx

        if kind == "results_ready" and _tw.over_budget(task):
            task.reason = _tw.over_budget(task)            # the root's own budget ran out
            await self._send_owner_result(room_id, task, _tw.timeout_line(task, me, names), *_tw.report_status(task, me))
            return
        # AUTHORITY: a turn another agent triggered never has outward tools (TRUSTED at most).
        band = min(Band(int(resolve_band("matrix", sender, config=self.config))), Band.TRUSTED)
        if kind == "request":
            # The ask and deliverable are another agent's data: fenced, flattened; the name is capped (Hub on #605).
            turn_body = _tw.request_turn_text(display_name, task)
        else:
            turn_body = _tw.results_turn_text(task, me, names)
        status = "done"
        started_ms = int(time.time() * 1000)
        try:
            await self.client.room_typing(room_id, True, timeout=15000)
        except Exception as e:
            logger.debug("Failed to set typing indicator: %s", e)
        try:
            text = await run_turn(_task_tools_turn(agent_respond), self.config, self.db, self.write_queue,
                                  turn_body, _room_session(self, room_id), self.tool_registry, band=band)
        except Exception as e:
            from windyfly.channels.errors import classify

            logger.error("teamwork turn failed: %s", classify(e).log_message)
            # A specialist's one result still goes back (blocked, error); the chief's report falls back to the
            # plain line about what came back (no model call).
            text = "I could not finish this." if kind == "request" else _tw.timeout_line(task, me, names, timed_out=False)
            status = "blocked"
        finally:
            try:
                await self.client.room_typing(room_id, False)
            except Exception as e:
                logger.debug("Failed to clear typing indicator: %s", e)
        # nio hands this process one event at a time, so an owner's stop sent while this turn ran has not been
        # read yet: look at the room's latest events before anything goes out (P9 run 4: two results went out
        # 11-12 s after the owner's stop). The contract: a stop drops any reply being generated.
        if _stop.stopped(room_id) or await self._owner_stopped_since(room, started_ms):
            from windyfly.channels import turn_budget as _budget

            logger.info("teamwork: the owner stopped room %s during the turn; reply dropped",
                        _budget.room_ref(room_id))
            return
        if kind == "request":
            content = _tw.result_content(task, str(text or ""), status, "error" if status == "blocked" else None)
            try:
                await self.client.room_send(room_id, "m.room.message", content)
                _tw.answered(task, status)
            except Exception as e:
                logger.error("teamwork result send failed: %s", e)
            return
        # needs_input when a part asks the owner; blocked only when NO part is done (with the first blocking
        # child's reason); else done (contract NEEDS_INPUT / REASONS). Whether the model turn failed does not
        # change what came back.
        await self._send_owner_result(room_id, task, str(text or ""), *_tw.report_status(task, me))

    async def _owner_stopped_since(self, room: Any, since_ms: int, *, anchor: str | None = None, limit: int = 20,
                                   skew_ms: int = 0, fail_closed: bool = False) -> bool:
        """Did the room's owner stop the agents after ``since_ms``? Reads the room's latest ``limit`` events (the
        stop callback may still be queued behind this turn). ``anchor``: an event id; the timeline older than it
        is not looked at (timeline order, not clocks). ``skew_ms``: how far the server's clock may lag ours.
        Not logged in: False. The read fails: False, or True with ``fail_closed`` (the caller drops its message)."""
        from windyfly.channels import room_stop as _stop
        from windyfly.channels import teamwork as _tw

        token = getattr(self.client, "access_token", None)
        homeserver = str(getattr(self.client, "homeserver", "") or "").rstrip("/")
        if not token or not homeserver:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, trust_env=False) as http:
                resp = await http.get(f"{homeserver}/_matrix/client/v3/rooms/{room.room_id}/messages",
                                      params={"dir": "b", "limit": str(limit)},
                                      headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                raise RuntimeError(f"status {resp.status_code}")
            chunk = resp.json().get("chunk", [])
            if not isinstance(chunk, list):
                raise RuntimeError("no chunk")
        except Exception as e:  # noqa: BLE001
            logger.debug("stop look-back failed: %s", type(e).__name__)
            return fail_closed
        team = self._wide_stop_room(room)
        found = False
        for ev in chunk:                        # newest first (dir=b)
            if not isinstance(ev, dict):
                continue
            if anchor and ev.get("event_id") == anchor:
                found = True
                break                           # everything older came before the anchor
            if int(ev.get("origin_server_ts") or 0) < since_ms - skew_ms:
                continue
            if not _stop.is_owner(ev.get("sender"), self.config):
                continue
            etype, content = ev.get("type"), ev.get("content")
            content = content if isinstance(content, dict) else {}
            if etype == _stop.STOP_EVENT:
                _stop.stop(room.room_id)
                return True
            if (etype == _tw.STOPPED_STATE and _tw.enabled() and ev.get("state_key") == ""
                    and content.get("stopped") is True):
                _stop.stop(room.room_id)
                return True
            rel = content.get("m.relates_to")
            if isinstance(rel, dict) and rel.get("rel_type") == "m.replace":
                continue                        # an edit is never a stop
            body = content.get("body") if etype == "m.room.message" else None
            if isinstance(body, str) and (_stop.is_stop_word(body)
                                          or (team and _tw.enabled() and _tw.is_contract_stop(body))):
                _stop.stop(room.room_id)        # the queued stop event still cancels tasks and confirms
                return True
        if anchor and fail_closed and not found:
            return True                         # the anchor wasn't in the window: unknown = stopped (Hub on #605)
        return False

    async def _send_owner_result(self, room_id: str, root: Any, text: str, status: str,
                                 reason: str | None = None) -> None:
        from windyfly.channels import teamwork as _tw

        content = _tw.owner_result_content(root, text, status, reason)
        content["windy_lang"] = self._detect_lang(content["body"])
        try:
            await self.client.room_send(room_id, "m.room.message", content)
        except Exception as e:
            logger.error("teamwork answer to the owner failed: %s", e)
        _tw.close_root(root, status)

    def _schedule_expiry(self, root: Any) -> None:
        from windyfly.channels import teamwork as _tw

        delay = max(1.0, _tw.deadline(root) - time.time()) + 1.0
        asyncio.get_running_loop().call_later(delay, lambda: asyncio.ensure_future(self._expire_root(root)))

    async def _expire_root(self, root: Any) -> None:
        """A task's deadline: if answers are still missing, the owner gets one plain line (no model)."""
        from windyfly.channels import room_stop as _stop
        from windyfly.channels import teamwork as _tw

        if root.status != "open":
            return
        if not _tw.expire(root):
            if root.status == "open" and _tw.deadline(root) > time.time():
                self._schedule_expiry(root)
            return
        if _stop.stopped(root.room):
            return
        room = self.client.rooms.get(root.room)
        # A stop already on the server but not processed here yet (nio reads one event at a time): look back
        # over the timeline since the ROOT began (its owner message), wide enough for a busy room, with room
        # for the server's clock to lag ours; a failed read drops the report (Hub on #605: fail closed).
        if await self._owner_stopped_since(room if room is not None else SimpleNamespace(room_id=root.room, users={}),
                                           int(root.created * 1000), anchor=root.thread, limit=_STOP_LOOKBACK_EVENTS,
                                           skew_ms=_STOP_SKEW_MS, fail_closed=True):
            logger.info("teamwork: the owner stopped the room before the deadline (or the look-back failed); "
                        "no report")
            return

        def names(mx: str) -> str:
            return (room.user_name(mx) if room is not None else None) or mx

        me = self.bot_user_id or ""
        await self._send_owner_result(root.room, root, _tw.timeout_line(root, me, names),
                                      *_tw.report_status(root, me))

    async def _on_message(
        self,
        room: nio.MatrixRoom,
        event: nio.RoomMessageText,
    ) -> None:
        """Handle incoming text messages (unencrypted rooms; see _on_encrypted_event)."""
        # Ignore our own messages (report-back reads the server time of its own ask here, when it had to wait)
        if event.sender == self.bot_user_id:
            from windyfly.channels import report_back as _rb_own

            if isinstance(getattr(event, "event_id", None), str):
                _rb_own.note_own_event(room.room_id, event.event_id, int(getattr(event, "server_timestamp", 0) or 0))
            return
        # An m.notice is never a turn, except a typed task message (agent-teamwork.v1) with teamwork on.
        if isinstance(event, nio.RoomMessageNotice):
            from windyfly.channels import teamwork as _tw_notice

            from windyfly.channels import parity as _parity_notice

            src = getattr(event, "source", None)
            notice_content = src.get("content") if isinstance(src, dict) else None
            if not (_tw_notice.enabled() and _parity_notice.passport_of(event.sender)
                    and isinstance(notice_content, dict) and _tw_notice.TASK_FIELD in notice_content):
                return            # a notice from a person, or untyped, stays dropped (Hub on #602)

        # Skip stale messages so a reconnect doesn't replay a backlog.
        #
        # The window is 120s, not 30s: nio serializes sync callbacks, and a
        # single turn that hits a web tool can run ~45-60s on the worker
        # thread. During that time nio holds the NEXT message (e.g. a
        # grandma's "oh wait, I meant air fryers!" correction fired 3s
        # after her first request) until the current callback returns — by
        # which point that follow-up is already ~45s old. At 30s it was
        # silently dropped (this early return is *above* the "Message from"
        # log, so it never even showed up), and the grandma got a confident
        # answer to the request she'd just corrected. 120s comfortably
        # covers the slowest single turn while still filtering a real
        # reconnect backlog (messages minutes old). Rapid double-texting —
        # extremely common for normies — no longer loses the second message.
        event_age = time.time() - (event.server_timestamp / 1000)
        if event_age > 120:
            return

        # Agent teams: a message from another AGENT that arrived before this process started is never
        # replayed (a restart inside the 120 s window re-answered a sibling's backlog and extended a
        # live loop on 10-07). Humans keep the 120 s window.
        from windyfly.channels import silence as _silence_boot

        if (_silence_boot.enabled() and _silence_boot.sender_is_agent(event.sender)
                and event.server_timestamp / 1000 < self._boot_time):
            logger.info("silence: dropped a pre-start message from agent %s", event.sender)
            return

        room_id = room.room_id
        body = event.body
        sender = event.sender
        display_name = room.user_name(sender) or sender

        from windyfly.channels import teamwork as _tw_members

        if _tw_members.enabled():
            await self._refresh_members(room_id)
            _src = getattr(event, "source", None)
            _content = _src.get("content") if isinstance(_src, dict) else None
            from windyfly.channels import parity as _parity_invalid

            # INVALID IS NEVER A TURN (agent-teamwork.v1): an agent's message whose ai.windy.task fails the
            # contract is dropped, never the untyped path, and still counts for the backstops (Hub on #605).
            if _parity_invalid.passport_of(sender) and _tw_members.invalid(_content):
                from windyfly.channels import turn_budget as _budget_invalid

                _budget_invalid.record(room_id)
                logger.info("teamwork: an invalid typed message from %s in %s: no turn (counted)",
                            sender, _budget_invalid.room_ref(room_id))
                return
            # An edit (m.replace) of an earlier message is never a new turn (agent-teamwork.v1, Hub on #158).
            _rel = _content.get("m.relates_to") if isinstance(_content, dict) else None
            if isinstance(_rel, dict) and _rel.get("rel_type") == "m.replace":
                logger.info("an edit in room %s: not a new turn", room_id)
                return

        # Another agent's command reply (marked by its runtime) is not for the model: no turn, no history.
        # Only an AGENT's message can be skipped this way; the owner's or any human's never is.
        if _is_command_reply(event):
            from windyfly.channels import silence as _silence_cr

            if _silence_cr.sender_is_agent(sender):
                logger.info("command reply from agent %s in %s: skipped", sender, room_id)
                return

        # Revoked passport (dark: WINDY_PARITY_BANDS=1): a revoked or suspended
        # agent is refused outright, once per message, with one honest line.
        from windyfly.channels import parity as _parity
        if _parity.enabled() and _parity.verdict(sender) == "refused":
            try:
                await self.client.room_send(room_id, "m.room.message", {
                    "msgtype": "m.text", "body": _parity.REFUSED_LINE, "windy_original": True})
            except Exception as e:
                logger.error("parity refusal notice failed: %s", e)
            return

        # Report-back (Grant's Build-131 note 8): another agent's message in a pair room where this agent is waiting
        # for an answer IS that answer. It is stored for the report in the owner's chat; no turn here.
        if self._catch_answer(room_id, sender, event, body):
            return

        # /agents off: a PEER agent (not one of the owner's own) is not heard. Never stored, never fed to
        # the model; it gets one canned line per hour. Owner, humans and the owner's own agents never are.
        from windyfly.channels import agent_peers as _peers

        if _parity.passport_of(sender) and _peers.policy() == "off":
            from windyfly.channels import silence as _silence_gate

            if _silence_gate.enabled():
                from windyfly.agent import teams as _teams_gate

                if _teams_gate.siblings_stale():  # decide "your own agent" on fresh data
                    await asyncio.to_thread(_teams_gate.refresh_siblings)
            from windyfly.agent.capabilities.descriptor import Band as _GateBand
            from windyfly.channels.identity import resolve_band as _gate_band

            if _gate_band("matrix", sender, config=self.config) < _GateBand.TRUSTED:
                logger.info("agents off: peer %s in %s not heard", sender, room_id)
                if _peers.should_notify(sender, room_id):
                    try:
                        await self.client.room_send(room_id, "m.room.message", {
                            "msgtype": "m.text", "body": _peers.NOTICE, "windy_original": True})
                    except Exception as e:
                        logger.error("agents-off notice failed: %s", e)
                return

        # Agent-to-agent turn budget (10-11 loop incident): a human message resets it; past it, an
        # agent's message is dropped BEFORE any command or model work, and the owner is told once.
        from windyfly.channels import turn_budget as _budget

        from_agent = _parity.passport_of(sender) is not None
        if not from_agent and _tw_members.enabled():
            from windyfly.agent import teams as _teams_stop

            # Know the owner's agents BEFORE deciding whether the owner's message is a stop (Hub on #605: right
            # after a restart the list was empty and "everyone pause" became a normal model turn).
            if _teams_stop.siblings_stale():
                await asyncio.to_thread(_teams_stop.refresh_siblings)
        # Any fault in the stop/addressed decision drops the message (Hub on #596: a malformed event from
        # an agent must never raise out of this callback into nio's sync loop).
        try:
            decision = self._stop_or_unaddressed(room, event, room_id, sender, body, from_agent)
        except Exception as e:  # noqa: BLE001
            logger.warning("stop/addressed check failed (%s); message dropped", type(e).__name__)
            return
        if room_id in self._stop_followups:          # this room's only: other rooms' follow-ups stay (Hub on #158)
            self._stop_followups = [r for r in self._stop_followups if r != room_id]
            await self._after_stop(room)
        if decision:
            return
        # Typed task traffic (agent-teamwork.v1): budgets and the lineage guard decide, not the backstop.
        from windyfly.channels import teamwork as _tw

        # A person's message in a room with the owner's other agents (teamwork on): when it mentions any of
        # them, only the mentioned agents take the turn (P9 run 1: two agents acted as chief on a message
        # meant for one; the contract rule, #158).
        if not from_agent and _tw.enabled():
            from windyfly.agent import teams as _teams_person

            if _teams_person.siblings_stale():
                await asyncio.to_thread(_teams_person.refresh_siblings)
            agents_here = self._owner_agents_in_room(room)
            if agents_here and not self._person_turn_is_mine(room, event, body, agents_here):
                logger.info("teamwork: a person's message in team room %s is for another agent: no turn",
                            _budget.room_ref(room_id))
                return

        if from_agent and _tw.enabled():
            try:
                src = getattr(event, "source", None)
                task_content = src.get("content") if isinstance(src, dict) else None
                if _tw.parse(task_content) is not None:
                    from windyfly.agent import teams as _teams_tw

                    if _teams_tw.siblings_stale():
                        await asyncio.to_thread(_teams_tw.refresh_siblings)
                    if sender not in _teams_tw.sibling_ids():
                        # The contract: tasks come from an agent of the room's OWNER (Hub on #602).
                        logger.info("teamwork: typed task from %s, not one of the owner's agents: dropped",
                                    sender)
                        return
                verdict = _tw.on_agent_message(room_id, self.bot_user_id or "", sender, task_content)
            except Exception as e:  # noqa: BLE001
                logger.warning("teamwork check failed (%s); message dropped", type(e).__name__)
                return
            if verdict.kind == "ignore":
                logger.info("teamwork: no turn on a task message from %s in %s (%s)",
                            sender, _budget.room_ref(room_id), verdict.why)
                return
            if verdict.kind in ("request", "results_ready") and verdict.task is not None:
                await self._task_turn(room, sender, display_name, verdict.kind, verdict.task)
                return
        if not from_agent:
            _budget.human_spoke(room_id)
        elif not _budget.allow(room_id):
            logger.info("turn budget: no reply to agent %s in room %s (%d per %ds, no human since)",
                        sender, _budget.room_ref(room_id), _budget.limit(), int(_budget.window_s()))
            if _budget.first_block(room_id):
                await self._turn_budget_hit(room, display_name)
            return

        logger.info(
            "Message from %s in %s: %s",
            display_name, room_id, body[:100],
        )

        # Unified command detection (Matrix uses ! prefix)
        from windyfly.channels.base import handle_incoming
        was_command, cmd_response = await handle_incoming(body, {
            "platform": "matrix",
            "channel_id": room_id,
            "sender_id": sender,
            "write_queue": self.write_queue,
        })
        if was_command:
            try:
                await self.client.room_send(
                    room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": cmd_response, "windy_original": True,
                     COMMAND_REPLY_KEY: True},
                )
                if from_agent:
                    _budget.record(room_id)
            except Exception as e:
                logger.error("Failed to send command response to %s: %s", room_id, e)
            return

        # The room's rolling session (same shape as Telegram's): survives restarts, honours /new.
        session_id = _room_session(self, room_id)

        # Read receipt: the person sees the agent has read their message (strand P4.11.12). Best-effort.
        event_id = getattr(event, "event_id", None)
        if isinstance(event_id, str) and event_id:
            try:
                await self.client.room_read_markers(room_id, event_id, read_event=event_id)
            except Exception as e:
                logger.debug("Failed to send read receipt: %s", e)

        # Show typing indicator
        try:
            await self.client.room_typing(room_id, True, timeout=15000)
        except Exception as e:
            logger.debug("Failed to set typing indicator: %s", e)

        # Generate response (off-loop so the client keeps syncing and
        # the typing indicator can refresh — see agent/executor.py)
        from windyfly.channels import silence as _silence

        agent_turn = _silence.enabled() and _silence.sender_is_agent(sender)
        agent_sender = _parity.passport_of(sender) is not None  # independent of the teams flag

        # The OWNER's language, as their Windy app tags it (strand P2.11.13): the reply follows it. Another
        # person in a shared room can't flip it (Hub on #600).
        if not agent_sender:
            from windyfly.channels import sender_lang as _lang
            from windyfly.channels.identity import owner_ids as _owner_ids
            from windyfly.channels.identity import resolve_band as _lang_band
            from windyfly.agent.capabilities.descriptor import Band as _LangBand

            if (sender in (_owner_ids(self.config).get("matrix") or set())
                    or _lang_band("matrix", sender, config=self.config) >= _LangBand.OWNER):
                src = getattr(event, "source", None)
                _lang.note(session_id, _lang.from_content(src.get("content") if isinstance(src, dict) else None))
        if agent_sender:
            _silence.mark_agent_sender(session_id)
        if agent_turn:
            _silence.mark_agent_turn(session_id)
            from windyfly.agent import teams as _teams

            if _teams.siblings_stale():  # who counts as the owner's own agent, refreshed off the loop
                await asyncio.to_thread(_teams.refresh_siblings)
        turn_body = _silence.frame_agent_message(display_name, body) if agent_turn else body
        from windyfly.tools import mail as _mail_tool

        drafts_before = {d["draft_id"] for d in _mail_tool.pending_drafts()}
        from windyfly.channels import phone_tools as _phone

        team_room: Any = None
        try:
            from windyfly.agent.executor import run_turn
            from windyfly.channels.identity import resolve_band
            turn_band = resolve_band("matrix", sender, config=self.config)
            if _phone.enabled() and not agent_turn:
                await self._mark_phone(session_id, room_id, sender, turn_band)
            respond: Any = agent_respond
            if not agent_turn and not agent_sender:
                team_room = await self._team_room_for(room, event, body, turn_band)
                if team_room is not None:
                    respond = _bound_turn(team_room, agent_respond)
                origin = self._report_origin(room_id, session_id, event, turn_band)
                if origin is not None:
                    respond = _bound_origin(origin, respond)
            response_text = await run_turn(
                respond,
                self.config, self.db, self.write_queue,
                turn_body, session_id, self.tool_registry,
                band=turn_band,
            )
        except Exception as e:
            from windyfly.channels.errors import classify
            classified = classify(e)
            logger.error("Agent respond failed: %s", classified.log_message)
            response_text = classified.user_message
        finally:
            _phone.clear_turn(session_id)
            if agent_turn:
                _silence.clear_agent_turn(session_id)
            if agent_sender:
                _silence.clear_agent_sender(session_id)

        # An agent turn may choose silence (teams); an empty answer to ANY agent sender posts
        # nothing, whatever the teams flag (e.g. a stopped agent never answers another agent).
        if (agent_turn and _silence.is_silence(response_text)) or (
            agent_sender and not (response_text or "").strip()
        ):
            logger.info("silence: no reply to agent %s in %s", sender, room_id)
            try:
                await self.client.room_typing(room_id, False)
            except Exception as e:
                logger.debug("Failed to clear typing indicator: %s", e)
            return

        # Send response with Windy metadata
        try:
            content = {
                "msgtype": "m.text",
                "body": response_text,
                "windy_original": True,
                "windy_lang": self._detect_lang(response_text),
            }
            # Chat contract: the model Mind actually SERVED; absent otherwise.
            from windyfly.agent.loop import pop_served_model
            served = pop_served_model(session_id)
            if served:
                content["uk.windypro.model"] = served
            # A held email draft the OWNER's own message just caused: tappable [send, wait] under the
            # question (quick-reply.v1, no authority: a tap is the same word typed).
            if not agent_turn and _mail_tool.send_confirm_enabled():
                from windyfly.channels.identity import resolve_band as _rb
                from windyfly.agent.capabilities.descriptor import Band as _Band

                if ({d["draft_id"] for d in _mail_tool.pending_drafts()} - drafts_before
                        and _rb("matrix", sender, config=self.config) >= _Band.OWNER):
                    import secrets as _secrets

                    content["ai.windy.quick_reply"] = {"id": "qr_" + _secrets.token_hex(6),
                                                       "options": ["send", "wait"]}
            # Teamwork: when this turn sent requests, its reply is backstage (m.notice in the task's
            # thread, never pushed); the owner's ONE answer comes when the results are in.
            if team_room is not None and "ai.windy.quick_reply" not in content:   # a confirm card is for the person
                content.update(_teamwork_backstage(team_room))
            # A team room: if the owner said stop while this turn ran, the reply is dropped (P9 run 4).
            if team_room is not None and team_room.stopped is not None and await asyncio.to_thread(team_room.stopped):
                logger.info("teamwork: the owner stopped room %s during this turn; reply dropped",
                            _budget.room_ref(room_id))
                return
            await self.client.room_send(
                room_id,
                "m.room.message",
                content,
            )
            if agent_sender:
                _budget.record(room_id)
        except Exception as e:
            logger.error("Failed to send response to %s: %s", room_id, e)
            self._pending_responses.append((room_id, response_text))
            self._pending_kept_at.setdefault((room_id, response_text), time.time())

        # Turn off typing indicator
        try:
            await self.client.room_typing(room_id, False)
        except Exception as e:
            logger.debug("Failed to clear typing indicator: %s", e)

    async def _on_encrypted_event(
        self,
        room: nio.MatrixRoom,
        event: nio.MegolmEvent,
    ) -> None:
        """A message arrived in an end-to-end encrypted room.

        This build has no end-to-end encryption (no olm), so the message
        cannot be read. Say so plainly in the log rather than pretend.
        """
        logger.warning(
            "message in an encrypted room cannot be read: this build has no "
            "end-to-end encryption (room %s, sender %s)",
            room.room_id, event.sender,
        )

    async def _on_file(self, room: nio.MatrixRoom, event: nio.RoomMessageFile) -> None:
        """A file in a room: only the owner's awaited soul-import export is taken (strand P2.21.13).

        Anything else is ignored, exactly as before. The size the event declares is checked before
        any download, and the bytes are counted again while they are received."""
        if event.sender == self.bot_user_id:
            return
        from windyfly.soul_import import flow as _flow

        key = f"matrix:{room.room_id}"
        if not _flow.awaiting_file(key):
            return
        from windyfly.agent.capabilities.descriptor import Band
        from windyfly.channels.identity import resolve_band

        if resolve_band("matrix", event.sender, config=self.config) < Band.OWNER:
            return

        async def say(text: str) -> None:
            await self.client.room_send(room.room_id, "m.room.message",
                                        {"msgtype": "m.text", "body": text, "windy_original": True})

        info = ((event.source or {}).get("content") or {}).get("info") or {}
        declared = info.get("size") if isinstance(info.get("size"), int) else None
        refusal = _flow.too_big(declared, _flow.MATRIX_MAX_BYTES, "Windy Chat")
        if refusal:
            await say(refusal)
            return
        try:
            resp = await self.client.download(mxc=event.url)
            body = getattr(resp, "body", None)
            if not isinstance(body, (bytes, bytearray)):
                await say("I couldn't download that file. Nothing was kept. /soul import to try again.")
                return
            import io

            reply = await asyncio.to_thread(_flow.take_file, self.db, key, io.BytesIO(bytes(body)),
                                            str(event.body or ""), max_bytes=_flow.MATRIX_MAX_BYTES)
        except Exception as e:
            logger.warning("soul import download failed: %s", type(e).__name__)
            reply = "Something went wrong receiving that file. Nothing was kept."
        await say(reply)

    async def _on_invite(
        self,
        room: nio.MatrixRoom,
        event: nio.InviteMemberEvent,
    ) -> None:
        """Auto-accept room invites and send a welcome message."""
        if event.state_key != self.bot_user_id:
            return

        room_id = room.room_id
        logger.info("Received invite to room %s", room_id)

        from windyfly.channels import invite_gate as _gate

        if _gate.enabled():
            inviter = str(getattr(event, "sender", "") or "")
            decision = await asyncio.to_thread(_gate.decide, inviter, room_id, self.config)
            if decision == "retry":
                self._invite_rechecks.setdefault(room_id, (room, event, time.time()))
                return
            self._invite_rechecks.pop(room_id, None)
            if decision == "ignore":
                logger.info("invite gate: ignored invite to %s from %s", room_id, inviter)
                return

        try:
            await self.client.join(room_id)
            logger.info("Joined room %s", room_id)

            # Send welcome message (not into a team room: another agent would read it as a message)
            team_room = False
            from windyfly.channels import silence as _silence

            if _silence.enabled():
                from windyfly.agent import teams as _teams

                team_room = await asyncio.to_thread(_teams.room_has_other_agent, room_id, self.bot_user_id)
            if not team_room:
                await self.client.room_send(
                    room_id,
                    "m.room.message",
                    {
                        "msgtype": "m.text",
                        "body": "Hey! I'm Windy Fly, your personal AI companion. 🪰",
                        "windy_original": True,
                        "windy_lang": "en",
                    },
                )

        except Exception as e:
            logger.error("Failed to join room %s: %s", room_id, e)

    async def _flush_pending(self, limit: int | None = None) -> None:
        """Flush any pending responses that failed to send.

        ``limit`` caps the sends in this pass; the rest stay kept for the next one.
        A reply kept for _PENDING_MAX_AGE_S or longer is dropped, not sent.
        """
        if not self._pending_responses:
            return

        now = time.time()
        fresh: list[tuple[str, str]] = []
        for item in self._pending_responses:
            kept_for = now - self._pending_kept_at.setdefault(item, now)
            if kept_for >= _PENDING_MAX_AGE_S:
                self._pending_dropped += 1
                logger.warning(
                    "Dropped a reply to %s: kept %.0f h and never accepted by the homeserver",
                    item[0], kept_for / 3600,
                )
            else:
                fresh.append(item)
        to_flush = fresh if limit is None else fresh[:limit]
        self._pending_responses = [] if limit is None else fresh[limit:]

        for room_id, response_text in to_flush:
            try:
                await self.client.room_send(
                    room_id,
                    "m.room.message",
                    {
                        "msgtype": "m.text",
                        "body": response_text,
                        "windy_original": True,
                        "windy_lang": "en",
                    },
                )
                logger.info("Flushed pending response to %s", room_id)
            except Exception as e:
                logger.warning("Still can't send to %s: %s", room_id, e)
                self._pending_responses.append((room_id, response_text))
        kept = set(self._pending_responses)
        for item in [k for k in self._pending_kept_at if k not in kept]:
            del self._pending_kept_at[item]

    def _is_token_expired_error(self, error: Exception) -> bool:
        """Check if an error indicates an expired/invalid access token."""
        err_str = str(error).lower()
        return any(
            phrase in err_str
            for phrase in [
                "m_unknown_token",
                "access token",
                "token expired",
                "invalid token",
                "401",
                "unauthorized",
            ]
        )

    def _sync_healthy(self, now: float | None = None) -> bool:
        """True iff /sync has succeeded recently and is not in a failure run."""
        now = time.time() if now is None else now
        if self._sync_failures >= _SYNC_FAILS_BEFORE_RECONNECT:
            return False
        if self._sync_failed_since is not None:
            # An outage that has lasted longer than one backoff run is real,
            # whichever sync_forever incarnation we are in.
            if (now - self._sync_failed_since) >= _SYNC_OUTAGE_S:
                return False
        if self._last_sync_success <= 0:
            # Never synced yet: healthy only while the failure run is short.
            return self._sync_failures < 3
        return (now - self._last_sync_success) < _SYNC_STALE_S

    def _sync_fail_age_s(self, now: float | None = None) -> float | None:
        """Seconds since the current outage began, or since the last good
        sync when there is no outage; None before the first sync."""
        now = time.time() if now is None else now
        if self._sync_failed_since is not None:
            return round(now - self._sync_failed_since, 1)
        if self._last_sync_success > 0:
            return round(now - self._last_sync_success, 1)
        return None

    def _write_heartbeat_once(self) -> None:
        """One heartbeat tick: log + heartbeat file. Split out so it is testable."""
        rooms_joined = len(self.client.rooms) if hasattr(self.client, "rooms") else 0
        pending_count = len(self._pending_responses)
        sync_ok = self._sync_healthy()
        logger.info(
            "♥ Matrix heartbeat: connected=%s, sync_ok=%s, sync_failures=%d, "
            "last_sync_ok_age=%ss, rooms_joined=%d, pending_queue=%d",
            self._connected,
            sync_ok,
            self._sync_failures,
            self._sync_fail_age_s(),
            rooms_joined,
            pending_count,
        )
        # Cross-platform heartbeat file for the guardian. `polling` means
        # "the loop is alive and behaving"; `sync_ok` is the honest bit —
        # false while the homeserver is unreachable.
        try:
            from windyfly.supervisor.heartbeat import write_heartbeat
            write_heartbeat(
                "matrix",
                polling=bool(self._connected),
                extra={
                    "sync_ok": sync_ok,
                    "sync_failures": self._sync_failures,
                    "sync_fail_age_s": self._sync_fail_age_s(),
                    "rooms_joined": rooms_joined,
                    "pending_dropped": self._pending_dropped,
                    "last_error": self._last_sync_error[:120],
                },
            )
        except Exception:
            pass

    async def _heartbeat_loop(self) -> None:
        """Log a heartbeat every 5 minutes showing connection status."""
        while not self._shutting_down:
            try:
                self._write_heartbeat_once()
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)

            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

    # ── sync health callbacks (run INSIDE nio's sync_forever loop) ──────

    async def _on_sync_response(self, response: Any) -> None:
        """A /sync succeeded: clear the failure run and both backoffs."""
        if self._sync_failures:
            logger.info(
                "Matrix sync recovered after %d failed attempt(s)",
                self._sync_failures,
            )
        self._sync_failures = 0
        self._sync_failed_since = None
        self._last_sync_error = ""
        self._last_sync_success = time.time()
        self._connected = True
        self._backoff = _INITIAL_BACKOFF_S
        # Replies that failed to send go out now that the homeserver answers,
        # not only after a reconnect (strand P2.13.4).
        if self._pending_responses and time.time() - self._last_pending_retry >= _PENDING_RETRY_EVERY_S:
            self._last_pending_retry = time.time()
            await self._flush_pending(limit=_PENDING_PER_SYNC)
        if self._invite_rechecks:  # invites Chat could not answer yet: one more try per sync, 10 min max
            from windyfly.channels import invite_gate as _gate

            for rid, (room, event, first) in list(self._invite_rechecks.items()):
                if time.time() - first > _gate.RECHECK_FOR_S:
                    del self._invite_rechecks[rid]
                    logger.info("invite gate: gave up re-checking invite to %s", rid)
                else:
                    await self._on_invite(room, event)

    async def _on_sync_error(self, response: Any) -> None:
        """A /sync failed. Sleep with exponential backoff so nio's internal
        retry loop cannot hammer the homeserver; after a run of failures,
        raise so start()'s reconnect loop takes over."""
        self._sync_failures += 1
        if self._sync_failed_since is None:
            self._sync_failed_since = time.time()
        status = getattr(response, "status_code", None)
        message = str(getattr(response, "message", response))[:200]
        self._last_sync_error = f"status={status} {message}"
        delay = min(
            _INITIAL_BACKOFF_S * (2 ** (self._sync_failures - 1)), _MAX_BACKOFF_S,
        )
        # Log the first few and then every 20th — not every one.
        if self._sync_failures <= 3 or self._sync_failures % 20 == 0:
            logger.warning(
                "Matrix sync failed (#%d, %s) — backing off %ds",
                self._sync_failures, self._last_sync_error, delay,
            )
        if self._sync_failures >= _SYNC_FAILS_BEFORE_RECONNECT:
            raise MatrixSyncDead(
                f"{self._sync_failures} consecutive sync failures "
                f"({self._last_sync_error})"
            )
        await asyncio.sleep(delay)

    async def _join_hatch_dm_room(self) -> None:
        """Join the owner DM room the hatch created, if the passport session
        told us about it and we are not in it yet. Best effort: the invite
        callback still covers the normal path; this covers an agent whose
        invite was delivered before it ever logged in (rooms_joined=0 for
        months on Windy 0)."""
        room_id = self._hatch_dm_room_id
        if not room_id:
            return
        rooms = getattr(self.client, "rooms", {}) or {}
        if room_id in rooms:
            return
        try:
            await self.client.join(room_id)
            logger.info("Joined hatch DM room %s", room_id)
        except Exception as exc:
            logger.warning("Could not join hatch DM room %s: %s", room_id, exc)

    def _setup_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register SIGTERM and SIGINT handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    functools.partial(self._handle_shutdown_signal, sig, loop),
                )
            except (NotImplementedError, RuntimeError):
                # Windows or non-main-thread — use signal.signal fallback
                try:
                    signal.signal(sig, lambda s, f: self._handle_shutdown_signal(s, loop))
                except (OSError, ValueError):
                    pass

    def _handle_shutdown_signal(
        self, sig: int, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Handle shutdown signal by cancelling the sync loop.

        We do NOT run graceful shutdown from here — cancelling ``_sync_task``
        unblocks ``start()``'s reconnect loop, which then runs the graceful
        path inline in its ``finally`` and returns, so the process actually
        exits (instead of a side task racing a wedged sync_forever).
        """
        sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
        logger.info("Received %s — initiating graceful shutdown...", sig_name)
        self._shutting_down = True
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()

    async def _start_maintenance(self) -> None:
        """The in-process maintenance loop on Matrix (strand N4.30). Best-effort: a setup problem
        disables maintenance, never the channel."""
        from windyfly.agent.maintenance import default_jobs, maintenance_loop

        self._maintenance_stop_event = asyncio.Event()
        try:
            jobs = default_jobs(self.config)
            jobs.append(self._make_inbox_watch_job())
        except Exception as e:
            logger.warning("maintenance: job setup failed (%s) — defaults only", e)
            jobs = None
        try:
            await maintenance_loop(self.config, jobs=jobs, stop_event=self._maintenance_stop_event,
                                   first_delay_s=60.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("maintenance loop exited with error: %s", e)

    def _make_inbox_watch_job(self):
        """Inbox watch (agent/inbox_watch.py) on Matrix: new mail is told to the owner in their direct
        room. The job runs in a worker thread, so delivery hops back onto the event loop."""
        from windyfly.agent.inbox_watch import make_inbox_watch_job

        loop = asyncio.get_running_loop()
        agent_name = str((self.config.get("agent") or {}).get("name") or "")

        def notify(text: str) -> None:
            room = self._hatch_dm_room_id
            if not room:
                raise RuntimeError("no direct room with the owner yet")
            asyncio.run_coroutine_threadsafe(
                self.client.room_send(room, "m.room.message",
                                      {"msgtype": "m.text", "body": text, "windy_original": True}),
                loop,
            ).result(timeout=30)

        return make_inbox_watch_job(notify, agent_name=agent_name)

    async def _graceful_shutdown(self) -> None:
        """Graceful shutdown: set offline, flush messages, close connection.

        Idempotent: the shutdown signal path and start()'s finally can both
        reach here, but the body runs at most once.
        """
        if self._graceful_done:
            return
        self._graceful_done = True
        self._shutting_down = True
        if self._maintenance_stop_event is not None:
            self._maintenance_stop_event.set()
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
        # Leave a handoff for the next boot — same deterministic writer
        # as /new, so a deliberate stop never strands the next session
        # without context (Principle #7; the 2026-07-17 01:15 stop left
        # no letter). Best-effort by contract.
        try:
            from windyfly.agent.turnover import write_shutdown_turnovers
            write_shutdown_turnovers(self.db, "matrix")
        except Exception as e:
            logger.debug("shutdown turnover skipped: %s", e)
        logger.info("Graceful shutdown: setting presence to offline...")
        try:
            await self.client.set_presence("offline", status_msg="Windy Fly shutting down")
        except Exception as e:
            logger.debug("Could not set offline presence: %s", e)

        logger.info("Graceful shutdown: flushing %d pending messages...", len(self._pending_responses))
        await self._flush_pending()

        logger.info("Graceful shutdown: closing connection...")
        try:
            await self.client.close()
        except Exception as e:
            logger.debug("Error closing client: %s", e)

        self._connected = False
        logger.info("Graceful shutdown complete. Windy Fly is offline. 🪰")

    async def _replay_offline_queue(self) -> None:
        """Replay messages that were queued while offline."""
        try:
            from windyfly.agent.offline import replay_queued_messages
            count = replay_queued_messages(
                self.config, self.db, self.write_queue, self.tool_registry,
            )
            if count > 0 and self._hatch_dm_room_id:
                from windyfly.agent.offline import offline_notice
                await self.client.room_send(
                    self._hatch_dm_room_id, "m.room.message",
                    {"msgtype": "m.text", "body": offline_notice(count), "windy_original": True},
                )
        except Exception as e:
            logger.debug("Offline queue replay: %s", e)

    async def _sms_inbox_loop(self) -> None:
        """Poll Windy Text for inbound texts; relay contacts, answer the owner (USER band)."""
        from windyfly.agent.capabilities import Band
        from windyfly.agent.executor import run_turn
        from windyfly.channels import sms_inbox
        from windyfly.platform import windy_state_dir
        from windyfly.tools import sms as _sms

        seen = sms_inbox.SeenStore(windy_state_dir() / "sms_inbox_seen.json")
        room_id = self._hatch_dm_room_id or ""
        session_id = _room_session(self, room_id)

        async def send_dm(text: str) -> None:
            await self.client.room_send(room_id, "m.room.message", {
                "msgtype": "m.text", "body": text, "windy_original": True})

        async def run_owner_turn(body: str) -> str:
            reply = await run_turn(
                agent_respond, self.config, self.db, self.write_queue,
                f"[Text message from you] {body}", session_id, self.tool_registry,
                # USER: a text can be spoofed, so only public-read tools (windyfly.tools.bands) run.
                band=Band.USER,
            )
            return str(reply)

        health = sms_inbox.PollHealth()
        while not self._shutting_down:
            try:
                items = await asyncio.to_thread(sms_inbox.fetch_inbox, _sms._windy_text_base())
            except Exception as e:  # a poll failure must never take the bot down
                warn = health.failed(sms_inbox.failure_key(e))
                (logger.warning if warn else logger.debug)("sms inbox poll failed: %s: %s", type(e).__name__, e)
            else:
                if health.ok():
                    logger.info("sms inbox poll recovered")
                try:
                    await sms_inbox.handle_new(
                        items, seen, send_dm=send_dm, run_owner_turn=run_owner_turn)
                except Exception as e:  # noqa: BLE001  (a relay failure must never take the bot down)
                    logger.warning("sms inbox relay failed: %s", e)
            await asyncio.sleep(sms_inbox.POLL_S)

    async def _mark_phone(self, session_id: str, room_id: str, sender: str, band: Any) -> None:
        """An OWNER turn in the owner's DM sees phone tools only while the owner's phone is online there."""
        from windyfly.agent.capabilities.descriptor import Band as _Band
        from windyfly.channels import phone_tools as _phone

        target = None
        if band >= _Band.OWNER:
            target = await asyncio.to_thread(_phone.pick_phone, room_id, sender)
        _phone.mark_turn(session_id, target)

    async def _phone_loop(self) -> None:
        """phone-tools.v1: read the phone's answers off the sync loop; each answer or timeout = one owner turn."""
        from windyfly.channels import phone_tools as _phone

        # Restart sweep: phone events left in 2-member rooms by requests this process no longer tracks.
        me = self.bot_user_id
        for room in list((getattr(self.client, "rooms", {}) or {}).values()):
            others = [u for u in getattr(room, "users", {}) if u != me]
            if len(others) == 1:
                try:
                    await asyncio.to_thread(_phone.sweep_room, room.room_id, others[0])
                except Exception as e:
                    logger.warning("phone tools sweep failed: %s", e)

        while not self._shutting_down:
            try:
                followups = await asyncio.to_thread(_phone.poll_once) if _phone.pending() else []
            except Exception as e:  # a poll failure must never take the bot down
                logger.warning("phone tools poll failed: %s", e)
                followups = []
            for room_id, _owner, stub, data in followups:
                await self._run_result_turn(room_id, stub, data)
            await asyncio.sleep(2 if _phone.pending() else 5)

    async def _run_result_turn(self, room_id: str, stub: str, data: str | None) -> None:
        """One phone result turn (phone-tools 1.1.0): no tools, the data quoted for this one model call,
        only the stub stored; an empty answer (stopped, or Mind's switch unreadable) posts NOTHING."""
        from windyfly.agent.capabilities import Band
        from windyfly.agent.executor import run_turn
        from windyfly.channels import phone_tools as _phone

        session_id = _room_session(self, room_id)
        try:
            _phone.mark_data_turn(session_id, data or "")
            reply = await run_turn(agent_respond, self.config, self.db, self.write_queue,
                                   stub, session_id, self.tool_registry, band=Band.OWNER)
            if reply:
                await self.client.room_send(room_id, "m.room.message", {
                    "msgtype": "m.text", "body": str(reply), "windy_original": True})
        except Exception as e:
            logger.error("phone tools follow-up turn failed: %s", e)
        finally:
            _phone.clear_turn(session_id)

    async def start(self) -> None:
        """Start the bot: login, register callbacks, sync forever with reconnection."""
        await self.login()

        # Register event callbacks (encrypted events only get an honest log line)
        self.client.add_event_callback(self._on_message, nio.RoomMessageText)
        # Typed task messages (agent-teamwork.v1) are m.notice; every other notice is dropped in _on_message.
        self.client.add_event_callback(self._on_message, nio.RoomMessageNotice)
        self.client.add_event_callback(self._on_file, nio.RoomMessageFile)
        self.client.add_event_callback(self._on_stop_event, nio.UnknownEvent)
        self.client.add_event_callback(self._on_invite, nio.InviteMemberEvent)
        self.client.add_event_callback(self._on_encrypted_event, nio.MegolmEvent)
        # Sync outcome callbacks — these are what make a dead homeserver a
        # backoff instead of a hot loop (see _on_sync_error).
        self.client.add_response_callback(self._on_sync_response, nio.SyncResponse)
        self.client.add_response_callback(self._on_sync_error, nio.SyncError)

        # Make sure we are actually in the owner's DM room from the hatch.
        await self._join_hatch_dm_room()

        # Set presence to online (with retry — R1.11)
        for attempt in range(3):
            try:
                await self.client.set_presence(
                    "online", status_msg="Windy Fly is ready 🪰"
                )
                logger.info("Presence set to online")
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning(
                        "Failed to set presence after 3 attempts: %s", e
                    )
                await asyncio.sleep(1)

        from nio.crypto import ENCRYPTION_ENABLED

        logger.info("Windy Fly is online and listening for messages (end-to-end encryption %s)",
                    "available" if ENCRYPTION_ENABLED else "NOT available: the olm library is not installed")

        # Set up graceful shutdown signal handlers
        try:
            loop = asyncio.get_running_loop()
            self._setup_signal_handlers(loop)
        except RuntimeError:
            logger.debug("Could not register signal handlers (no running loop)")

        # Start heartbeat in background
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        # Inbound texts (dark: WINDY_TEXT_BYO=1).
        from windyfly.tools import sms as _sms
        if _sms.byo_enabled() and self._hatch_dm_room_id:
            self._sms_task = asyncio.create_task(self._sms_inbox_loop())
        # Phone tools (dark: WINDY_PHONE_TOOLS=1).
        from windyfly.channels import phone_tools as _phone_flag
        if _phone_flag.enabled():
            self._phone_task = asyncio.create_task(self._phone_loop())
        # Maintenance (journal, inbox watch, ...): exactly one process per agent runs it (strand N4.30).
        from windyfly.agent.maintenance import maintenance_runner
        if maintenance_runner() == "matrix":
            self._maintenance_task = asyncio.create_task(self._start_maintenance())
        else:
            logger.info("maintenance: runs in the %s process for this agent, not here "
                        "(if that process is not running, no maintenance runs)", maintenance_runner())

        # Sync with reconnection retry loop (exponential backoff)
        self._backoff = _INITIAL_BACKOFF_S
        self._connected = True

        try:
            while not self._shutting_down:
                try:
                    # Flush pending responses on reconnect
                    await self._flush_pending()

                    # Replay offline-queued messages on reconnect
                    await self._replay_offline_queue()

                    # Run sync as a cancellable task so a SIGTERM handler can
                    # actually interrupt it (see _handle_shutdown_signal).
                    self._sync_task = asyncio.ensure_future(
                        self.client.sync_forever(
                            timeout=30000,
                            full_state=True,
                        )
                    )
                    await self._sync_task
                    # sync_forever only returns on error
                    self._backoff = _INITIAL_BACKOFF_S
                    self._connected = True
                except asyncio.CancelledError:
                    # Shutdown requested — leave the reconnect loop cleanly.
                    logger.info("Sync cancelled — shutting down Matrix bot")
                    break
                except Exception as e:
                    self._connected = False
                    # A new sync_forever starts a fresh failure run.
                    self._sync_failures = 0

                    # Check if this is a token expiry error
                    if self._is_token_expired_error(e):
                        logger.warning("Access token expired. Attempting re-login...")
                        if await self._relogin_with_password():
                            self._backoff = _INITIAL_BACKOFF_S
                            self._connected = True
                            continue
                        else:
                            logger.error("Re-login failed. Will retry in %ds", self._backoff)

                    logger.warning(
                        "Connection lost: %s. Reconnecting in %ds...",
                        e, self._backoff,
                    )

                    # Log reconnect event for observability (R1.10)
                    try:
                        from windyfly.observability.events import log_event
                        log_event(self.db, self.write_queue, "matrix.reconnect", {
                            "error": str(e)[:200],
                            "backoff_seconds": self._backoff,
                            "token_expired": self._is_token_expired_error(e),
                        })
                    except Exception as e:
                        logger.debug("Reconnect event logging failed: %s", e)

                    if self._shutting_down:
                        break

                    await asyncio.sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, _MAX_BACKOFF_S)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            # Run graceful shutdown inline so presence-offline + flush + close
            # complete BEFORE start() returns and the interpreter exits (which
            # is what lets the atexit runtime-claim release fire). Idempotent.
            await self._graceful_shutdown()

    async def send(self, message: OutgoingMessage) -> None:
        """Send a message to a Matrix room (ChannelAdapter interface)."""
        try:
            await self.client.room_send(
                message.channel_id,
                "m.room.message",
                {
                    "msgtype": "m.text",
                    "body": message.text,
                    "windy_original": True,
                },
            )
        except Exception as e:
            logger.error("Failed to send to %s: %s", message.channel_id, e)

    def is_connected(self) -> bool:
        """Return True if the bot is syncing with the homeserver."""
        return self._connected

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        self._shutting_down = True
        for handle in list(self._report_timers):
            handle.cancel()
        self._report_timers.clear()
        await self._graceful_shutdown()

    @staticmethod
    def _detect_lang(text: str) -> str:
        """Quick language detection based on Unicode character ranges.

        R1.12: Detects the dominant script in the response text and maps
        it to a language code. Falls back to 'en' for Latin scripts.

        Args:
            text: The response text to analyze.

        Returns:
            ISO 639-1 language code (best guess).
        """
        import unicodedata

        sample = text[:200]
        scripts: dict[str, int] = {}
        for ch in sample:
            if ch.isalpha():
                try:
                    name = unicodedata.name(ch, "UNKNOWN").split()[0]
                    scripts[name] = scripts.get(name, 0) + 1
                except ValueError:
                    pass
        if not scripts:
            return "en"
        dominant = max(scripts, key=scripts.get)  # type: ignore[arg-type]
        # Map common Unicode script name prefixes to ISO 639-1 codes
        script_map = {
            "CJK": "zh",
            "HANGUL": "ko",
            "HIRAGANA": "ja",
            "KATAKANA": "ja",
            "ARABIC": "ar",
            "DEVANAGARI": "hi",
            "CYRILLIC": "ru",
            "THAI": "th",
            "HEBREW": "he",
            "BENGALI": "bn",
            "TAMIL": "ta",
            "TELUGU": "te",
        }
        return script_map.get(dominant, "en")
