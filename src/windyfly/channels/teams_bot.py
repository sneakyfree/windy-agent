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

        async def on_turn(turn_context: TurnContext) -> None:
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
        logger.warning(
            "Teams proactive messaging requires conversation reference — "
            "use reply pattern instead"
        )

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
