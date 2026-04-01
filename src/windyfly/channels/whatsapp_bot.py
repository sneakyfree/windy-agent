"""WhatsApp adapter via Twilio WhatsApp API.

Env vars: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER
Set up at twilio.com -> Messaging -> WhatsApp Sandbox (dev) or Business Profile (prod).
Install: pip install windyfly[whatsapp]
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class WhatsAppChannel(ChannelAdapter):
    """Windy Fly on WhatsApp via Twilio webhook."""

    name = "whatsapp"

    def __init__(self, webhook_port: int = 5555) -> None:
        self._webhook_port = webhook_port
        self._twilio_client = None
        self._from_number: str = ""
        self._connected = False
        self._server_thread: threading.Thread | None = None

    async def start(self) -> None:
        sid = os.environ.get("TWILIO_ACCOUNT_SID")
        token = os.environ.get("TWILIO_AUTH_TOKEN")
        self._from_number = os.environ.get(
            "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"
        )

        if not sid or not token:
            raise RuntimeError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN required")

        from twilio.rest import Client

        self._twilio_client = Client(sid, token)

        from flask import Flask, request

        app = Flask(__name__)
        adapter = self
        loop = asyncio.get_event_loop()

        @app.route("/whatsapp/webhook", methods=["POST"])
        def webhook():
            text = request.form.get("Body", "")

            # Unified command detection (sync wrapper for Flask)
            from windyfly.commands.registry import registry, is_command, parse_command
            if is_command(text):
                import asyncio as _aio
                cmd_response = _aio.run_coroutine_threadsafe(
                    registry.execute(parse_command(text), {"platform": "whatsapp"}), loop
                ).result(timeout=15)
                from twilio.twiml.messaging_response import MessagingResponse
                resp = MessagingResponse()
                resp.message(cmd_response)
                return str(resp)

            msg = IncomingMessage(
                platform="whatsapp",
                channel_id=request.form.get("From", ""),
                sender_id=request.form.get("From", ""),
                sender_name=request.form.get("ProfileName", "User"),
                text=text,
            )
            future = asyncio.run_coroutine_threadsafe(adapter.on_message(msg), loop)
            response_text = future.result(timeout=30)

            from twilio.twiml.messaging_response import MessagingResponse

            resp = MessagingResponse()
            resp.message(response_text)
            return str(resp)

        self._server_thread = threading.Thread(
            target=lambda: app.run(port=self._webhook_port, debug=False),
            daemon=True,
        )
        self._server_thread.start()
        self._connected = True
        logger.info("WhatsApp webhook listening on port %d", self._webhook_port)

    async def send(self, message: OutgoingMessage) -> None:
        if self._twilio_client:
            self._twilio_client.messages.create(
                body=message.text,
                from_=self._from_number,
                to=message.channel_id,
            )

    async def stop(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
