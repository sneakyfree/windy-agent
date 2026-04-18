"""Signal adapter via signal-cli-rest-api.

Requires signal-cli-rest-api running (Docker):
  docker run -p 8080:8080 bbernhard/signal-cli-rest-api

Env vars: SIGNAL_API_URL, SIGNAL_PHONE_NUMBER
No extra pip install needed (uses httpx which is already a dependency).
"""

from __future__ import annotations

import asyncio
import logging
import os

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class SignalChannel(ChannelAdapter):
    """Windy Fly on Signal via signal-cli-rest-api."""

    name = "signal"

    def __init__(self) -> None:
        self._api_url = ""
        self._phone = ""
        self._connected = False
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._api_url = os.environ.get("SIGNAL_API_URL", "http://localhost:8080")
        phone = os.environ.get("SIGNAL_PHONE_NUMBER")
        if not phone:
            raise RuntimeError(
                "SIGNAL_PHONE_NUMBER required (your registered Signal number)"
            )
        self._phone = phone

        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{self._api_url}/v1/about")
            if resp.status_code != 200:
                raise RuntimeError(f"Signal API not reachable at {self._api_url}")

        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_messages())
        logger.info("Signal bot connected via %s", self._api_url)

    async def _poll_messages(self) -> None:
        import httpx

        while self._connected:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(
                        f"{self._api_url}/v1/receive/{self._phone}"
                    )
                    if resp.status_code == 200:
                        for item in resp.json():
                            envelope = item.get("envelope", {})
                            data = envelope.get("dataMessage", {})
                            if data.get("message"):
                                text = data["message"]

                                # Unified command detection
                                from windyfly.channels.base import handle_incoming
                                was_command, cmd_response = await handle_incoming(
                                    text, {"platform": "signal"}
                                )
                                if was_command:
                                    await self.send(
                                        OutgoingMessage(
                                            text=cmd_response,
                                            channel_id=envelope.get("source", ""),
                                        )
                                    )
                                    continue

                                msg = IncomingMessage(
                                    platform="signal",
                                    channel_id=envelope.get("source", ""),
                                    sender_id=envelope.get("source", ""),
                                    sender_name=envelope.get("sourceName", "User"),
                                    text=text,
                                )
                                # on_message wired by the channel manager before start().
                                assert self.on_message is not None
                                response = await self.on_message(msg)
                                await self.send(
                                    OutgoingMessage(
                                        text=response, channel_id=msg.channel_id,
                                    )
                                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Signal poll error: %s", exc)
            await asyncio.sleep(1)

    async def send(self, message: OutgoingMessage) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{self._api_url}/v2/send",
                json={
                    "message": message.text,
                    "number": self._phone,
                    "recipients": [message.channel_id],
                },
            )

    async def stop(self) -> None:
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()

    def is_connected(self) -> bool:
        return self._connected
