"""Microsoft Teams adapter via Bot Framework.

Env vars: TEAMS_APP_ID, TEAMS_APP_PASSWORD
Register at dev.teams.microsoft.com.
Install: pip install windyfly[teams]
"""

from __future__ import annotations

import logging
import os
from typing import Any

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class TeamsChannel(ChannelAdapter):
    """Windy Fly on Microsoft Teams."""

    name = "teams"

    def __init__(self, webhook_port: int = 3978) -> None:
        self._webhook_port = webhook_port
        self._connected = False
        # aiohttp.web.AppRunner once start() runs; typed Any because the
        # optional botbuilder import above pulls in the web stack lazily.
        self._runner: Any = None
        # Bot Framework outbound state (populated in start()/on_turn):
        # the adapter instance + app id are needed to send, and Teams
        # proactive messaging REQUIRES a ConversationReference captured
        # from a prior inbound activity (there is no address-a-user-cold
        # API). We cache the most-recent reference per conversation id.
        self._adapter_bf: Any = None
        self._app_id: str = ""
        self._conv_refs: dict[str, Any] = {}
        self._last_conv_id: str | None = None

    async def start(self) -> None:
        app_id = os.environ.get("TEAMS_APP_ID")
        app_password = os.environ.get("TEAMS_APP_PASSWORD")
        if not app_id or not app_password:
            raise RuntimeError("TEAMS_APP_ID and TEAMS_APP_PASSWORD required")

        from aiohttp import web
        from botbuilder.core import (
            BotFrameworkAdapter,
            BotFrameworkAdapterSettings,
            TurnContext,
        )

        settings = BotFrameworkAdapterSettings(app_id, app_password)
        adapter_bf = BotFrameworkAdapter(settings)
        channel_adapter = self
        # Stash for the proactive send() path.
        self._adapter_bf = adapter_bf
        self._app_id = app_id

        async def on_turn(turn_context: TurnContext) -> None:
            # Capture the conversation reference from EVERY inbound turn so
            # a later proactive send() can reach this conversation. Teams
            # has no cold-start send API — this reference is the only way.
            try:
                ref = TurnContext.get_conversation_reference(turn_context.activity)
                conv = turn_context.activity.conversation
                if conv is not None and conv.id:
                    channel_adapter._conv_refs[conv.id] = ref
                    channel_adapter._last_conv_id = conv.id
            except Exception:  # pragma: no cover - reference capture is best-effort
                pass

            if turn_context.activity.type == "message":
                text = turn_context.activity.text or ""

                # Unified command detection
                from windyfly.channels.base import handle_incoming
                was_command, cmd_response = await handle_incoming(
                    text, {
                        "platform": "teams",
                        "sender_id": turn_context.activity.from_property.id,
                    }
                )
                if was_command:
                    await turn_context.send_activity(cmd_response)
                    return

                msg = IncomingMessage(
                    platform="teams",
                    channel_id=turn_context.activity.conversation.id,
                    sender_id=turn_context.activity.from_property.id,
                    sender_name=turn_context.activity.from_property.name or "User",
                    text=text,
                )
                # on_message wired by the channel manager before start().
                assert channel_adapter.on_message is not None
                response = await channel_adapter.on_message(msg)
                await turn_context.send_activity(response)

        async def messages(req: web.Request) -> web.Response:
            body = await req.read()
            activity = adapter_bf.parse_request(body, req.headers)
            await adapter_bf.process_activity(activity, "", on_turn)
            return web.Response(status=200)

        app = web.Application()
        app.router.add_post("/api/messages", messages)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._webhook_port)
        await site.start()
        self._connected = True
        logger.info("Teams bot listening on port %d", self._webhook_port)

    async def send(self, message: OutgoingMessage) -> None:
        """Proactively send a message to a Teams conversation.

        Inbound replies are delivered inline by ``on_turn`` (the manager's
        callback returns text and the turn context sends it). This path is
        for AGENT-INITIATED (proactive) messages — e.g. a proactive
        check-in — which Teams can only route through a
        ``ConversationReference`` captured from a prior inbound turn.

        Pre-2026-07-06 this silently logged and dropped the message, so the
        ChannelManager believed a proactive send succeeded when nothing
        was sent. Now it either delivers via ``continue_conversation`` or
        raises, so a dropped send is never silent.
        """
        if self._adapter_bf is None:
            raise RuntimeError(
                "Teams channel not started — call start() before send()"
            )
        # Resolve the target conversation: explicit channel_id, else the
        # most recent inbound conversation.
        conv_id = message.channel_id or getattr(self, "_last_conv_id", None)
        ref = self._conv_refs.get(conv_id) if conv_id else None
        if ref is None:
            raise RuntimeError(
                "No Teams conversation reference for a proactive send — "
                "Teams cannot message a user cold; the agent must have "
                "received at least one inbound message in that conversation "
                "first. (Inbound replies work without this.)"
            )

        async def _send_turn(turn_context: Any) -> None:
            await turn_context.send_activity(message.text)

        await self._adapter_bf.continue_conversation(
            ref, _send_turn, self._app_id
        )

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
